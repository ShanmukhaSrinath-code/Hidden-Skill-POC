"""Phase 2: Agents 1-4 turn canonical activity threads into an evidence-backed employee skill graph.

    Agent 1  relevance filter      small local model   keep help requests / helpful answers / confirmed fixes
    Agent 2  skill categorization  local model         who helped whom, on which skill and topic
    Agent 3  skill accuracy        rules + local model explicit confirmation first ("that fixed it" from someone
                                                       else); a ticket moved to Done backs the outcome but the answer
                                                       is still judged 1-10; otherwise an implicit 1-10 score
    Agent 4  graph update          deterministic code  only writer; idempotent; explicit vs discovered skills

Models run locally through Ollama (OLLAMA_URL, AGENT1_MODEL, AGENT2_MODEL, AGENT3_MODEL). Only threads that are
new or changed since the last run are processed.

    python -m integrations.skills                 # process new / changed threads
    python -m integrations.skills --limit 5       # at most 5 threads (quick check)
    python -m integrations.skills --reprocess     # re-run every thread
"""

import argparse
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
AGENT1_MODEL = os.getenv("AGENT1_MODEL", "qwen2.5:3b")
AGENT2_MODEL = os.getenv("AGENT2_MODEL", "qwen2.5:7b")
AGENT3_MODEL = os.getenv("AGENT3_MODEL", "qwen2.5:7b")
IMPLICIT_MIN_SCORE = int(os.getenv("IMPLICIT_MIN_SCORE", "7"))
MAX_LINES = 30          # most recent messages per thread sent to the models
MAX_CHARS = 400         # per message

CATEGORIES = ["Backend", "Frontend", "Data & Databases", "DevOps & Cloud", "AI / ML", "Quality & Testing",
              "Security", "Project & People", "Documentation & Process", "Other"]

# Skill taxonomy: canonical name -> (category, aliases). Declared HRMS/resume skills and model output are both
# normalised onto these names, so "RAG" (resume) and "RAG & Retrieval" (discovered) are the same node.
TAXONOMY = {
    "Python": ("Backend", ["python3"]),
    "FastAPI": ("Backend", ["fast api", "uvicorn"]),
    "API Design": ("Backend", ["rest api", "rest apis", "restapis", "graphql"]),
    "Async Programming": ("Backend", ["asyncio", "async", "httpx", "async testing"]),
    "Performance Tuning": ("Backend", ["performance", "latency", "memory leak", "profiling", "connection pool"]),
    "SQL & PostgreSQL": ("Data & Databases", ["sql", "postgres", "postgresql", "sqlalchemy", "sql joins", "joins"]),
    "Redis & Caching": ("Data & Databases", ["redis", "caching", "cache", "session cache"]),
    "Vector Databases": ("Data & Databases", ["pgvector", "qdrant", "hnsw", "vector search", "semantic search", "embeddings"]),
    "Data Pipelines": ("Data & Databases", ["etl", "ingestion", "data pipeline"]),
    "Kubernetes": ("DevOps & Cloud", ["k8s", "hpa", "autoscaling", "kubectl"]),
    "Docker": ("DevOps & Cloud", ["containers", "docker buildx", "multi-arch builds", "dockerfile"]),
    "CI/CD": ("DevOps & Cloud", ["github actions", "pipelines", "continuous integration", "ci", "deployment"]),
    "Helm": ("DevOps & Cloud", ["helm charts", "helm rollback"]),
    "Observability & Monitoring": ("DevOps & Cloud", ["prometheus", "grafana", "monitoring", "alerting", "observability"]),
    "Cloud (AWS / Azure / GCP)": ("DevOps & Cloud", ["aws", "azure", "gcp", "cloud", "load balancer", "alb"]),
    "Infrastructure as Code": ("DevOps & Cloud", ["terraform", "iac"]),
    "Incident Management": ("Project & People", ["incident response", "postmortem", "on-call", "incident handoff"]),
    "RAG & Retrieval": ("AI / ML", ["rag", "retrieval", "reranking", "reranker", "cross-encoder"]),
    "LLM Engineering": ("AI / ML", ["llm", "prompt caching", "prompt engineering", "llm inference", "vllm", "agents"]),
    "Model Evaluation": ("AI / ML", ["llm evaluation", "evaluation metrics", "mrr", "ndcg", "evals"]),
    "Machine Learning": ("AI / ML", ["ml"]),
    "PyTorch": ("AI / ML", ["torch"]),
    "React": ("Frontend", ["react.js", "next.js", "nextjs"]),
    "TypeScript": ("Frontend", ["ts", "javascript"]),
    "CSS": ("Frontend", ["styling", "tailwind"]),
    "Test Automation": ("Quality & Testing", ["ui testing", "pytest", "automated testing", "regression testing"]),
    "Selenium": ("Quality & Testing", []),
    "Manual Testing": ("Quality & Testing", ["qa"]),
    "Load Testing": ("Quality & Testing", ["soak test", "load test", "performance testing"]),
    "Documentation": ("Documentation & Process", ["sop", "runbook", "technical writing", "docs"]),
    "Project Management": ("Project & People", ["roadmapping", "planning", "delivery planning"]),
    "Agile Delivery": ("Project & People", ["agile", "scrum", "kanban"]),
    "Stakeholder Communication": ("Project & People", ["communication", "reporting"]),
    "People Leadership": ("Project & People", ["leadership", "management"]),
    "Mentoring": ("Project & People", ["coaching"]),
    "Code Review": ("Backend", ["review"]),
    "Security": ("Security", ["auth", "authentication", "oauth"]),
}
_ALIASES = {}
for _name, (_cat, _aliases) in TAXONOMY.items():
    for _a in [_name, *_aliases]:
        _ALIASES[_a.lower()] = _name

CONFIRM = re.compile(r"\b(thanks|thank you|worked|works now|fixed it|that fixed|resolved|solved|that did it|confirmed|"
                     r"great fix|perfect|memory is flat|is clean now|that helped)\b", re.I)
NEGATE = re.compile(r"\b(didn'?t work|does not work|doesn'?t work|still (failing|broken|slow)|not fixed)\b", re.I)
DONE_STATUS = re.compile(r"status: .*-> *(done|complete|completed|closed|resolved)\b", re.I)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def canonical_skill(name, category=None):
    """Map any skill name onto the taxonomy; unknown names become new skills under the model's category."""
    key = (name or "").strip().lower()
    if key in _ALIASES:
        return _ALIASES[key], TAXONOMY[_ALIASES[key]][0]
    for alias, canonical in _ALIASES.items():   # e.g. "Kubernetes autoscaling" -> Kubernetes
        if len(alias) > 3 and re.search(rf"\b{re.escape(alias)}\b", key):
            return canonical, TAXONOMY[canonical][0]
    clean = " ".join(w if w.isupper() else w.capitalize() for w in (name or "").split())[:60]
    return clean, category if category in CATEGORIES else "Other"


# ---------------------------------------------------------------- local model client

def llm(model, system, prompt, schema, num_predict=300):
    """One structured call to a local Ollama model; returns the parsed JSON object."""
    body = {"model": model, "stream": False, "format": schema, "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": num_predict},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if model.startswith("qwen3"):
        body["think"] = False
    request = Request(f"{OLLAMA_URL}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=900) as response:
            content = json.load(response)["message"]["content"]
    except URLError as exc:
        raise RuntimeError(f"Ollama not reachable at {OLLAMA_URL}: {exc}") from exc
    return json.loads(content)


# ---------------------------------------------------------------- thread input

def thread_messages(cur, thread_key):
    cur.execute("""SELECT a.activity_id, a.source, a.activity_type, a.actor_emp_id, a.actor_name, a.text, a.source_url,
                          a.occurred_at, a.conversation_title, a.context, e.role
                   FROM activity_records a LEFT JOIN employees e ON e.emp_id = a.actor_emp_id
                   WHERE a.thread_key = %s ORDER BY a.occurred_at""", (thread_key,))
    rows = cur.fetchall()
    return rows[-MAX_LINES:]


def render(messages):
    """Numbered transcript the models read: [n] Name (role) · type: text."""
    lines = []
    for i, m in enumerate(messages, 1):
        text = re.sub(r"\s+", " ", m["text"] or "").strip()[:MAX_CHARS]
        who = f"{m['actor_name'] or 'unknown'}" + (f" ({m['role']})" if m["role"] else "")
        lines.append(f"[{i}] {who} · {m['activity_type']}: {text}")
    head = messages[0]
    return f"Source: {head['source']} · {head['conversation_title'] or ''}\n" + "\n".join(lines)


# ---------------------------------------------------------------- Agent 1: relevance filter

AGENT1_SCHEMA = {"type": "object", "required": ["relevant", "signal", "reason"], "properties": {
    "relevant": {"type": "boolean"},
    "signal": {"type": "string", "enum": ["help_request", "helpful_answer", "confirmed_fix", "work_solution", "none"]},
    "reason": {"type": "string"}}}
AGENT1_SYSTEM = ("You filter workplace conversations before skill analysis. A conversation is RELEVANT if someone asks for "
                 "help, gives a technical or professional answer, explains how a problem was solved, or confirms a fix. "
                 "It is NOT relevant if it is only greetings, small talk, memes, lunch plans, gossip, FYIs, bare task "
                 "assignments or status updates with no explanation. Reason: at most 12 words.")


def agent1(transcript):
    words = len(re.findall(r"[A-Za-z]{2,}", transcript.split("\n", 1)[-1]))
    if words < 6:   # nothing to judge: skip the model call
        return {"relevant": False, "signal": "none", "reason": "too little text", "model": "rule"}
    out = llm(AGENT1_MODEL, AGENT1_SYSTEM, transcript, AGENT1_SCHEMA, num_predict=80)
    out["model"] = AGENT1_MODEL
    return out


# ---------------------------------------------------------------- Agent 2: who helped whom, on what

AGENT2_SCHEMA = {"type": "object", "required": ["interactions"], "properties": {"interactions": {
    "type": "array", "items": {"type": "object",
                               "required": ["helper_message", "requester_message", "skill", "category", "topic"],
                               "properties": {
                                   "helper_message": {"type": "integer"},
                                   "requester_message": {"type": "integer"},
                                   "skill": {"type": "string"},
                                   "category": {"type": "string", "enum": CATEGORIES},
                                   "topic": {"type": "string"}}}}}}
AGENT2_SYSTEM = ("You map help exchanges in a workplace conversation. For each place where one person HELPS another "
                 "(answers a question, explains a fix, shares a solution, reviews work, documents a process), output: "
                 "helper_message = number [n] of the message containing the help; requester_message = number of the "
                 "message where help was asked for (0 if nobody asked); skill = the MOST SPECIFIC skill or technology "
                 "demonstrated (e.g. 'Vector Databases' rather than 'Performance Tuning', 'Redis & Caching' rather than "
                 "'Backend'), preferably one of: " + ", ".join(TAXONOMY) + "; category; topic = 3-8 words. If one "
                 "answer shows two distinct skills, output two entries. The helper message must itself contain the "
                 "help: a concrete answer, fix, explanation or review. NOT help: questions, requests, task assignments, "
                 "thanks, promises or plans ('I'll add...'), and reports of one's own finished work with no explanation "
                 "for someone else. Only skills actually demonstrated "
                 "in the helper's message. Do not invent. Return an empty list if nobody helps anyone.")


def agent2(transcript, messages):
    out = llm(AGENT2_MODEL, AGENT2_SYSTEM, transcript, AGENT2_SCHEMA, num_predict=400)
    interactions = []
    for item in out.get("interactions", [])[:6]:
        h, r = item.get("helper_message", 0), item.get("requester_message", 0)
        if not 1 <= h <= len(messages):
            continue
        helper = messages[h - 1]
        requester = messages[r - 1] if 1 <= r <= len(messages) else None
        if not helper["actor_emp_id"]:
            continue
        skill, category = canonical_skill(item.get("skill"), item.get("category"))
        interactions.append({
            "helper_index": h, "requester_index": r if requester else None,
            "helper_emp_id": helper["actor_emp_id"],
            "requester_emp_id": requester["actor_emp_id"] if requester and requester["actor_emp_id"] != helper["actor_emp_id"] else None,
            "skill": skill, "category": category, "topic": (item.get("topic") or "")[:80],
            "activity_id": helper["activity_id"]})
    return interactions


# ---------------------------------------------------------------- Agent 3: is it really evidence?

AGENT3_SCHEMA = {"type": "object", "required": ["score", "reason"], "properties": {
    "score": {"type": "integer", "minimum": 1, "maximum": 10}, "reason": {"type": "string"}}}
AGENT3_SYSTEM = ("You judge whether a workplace answer demonstrates real skill. Score 1-10 for technical correctness and "
                 "usefulness of the HELPER's answer to the question: 9-10 precise and correct, 7-8 correct and useful, "
                 "4-6 vague or partly right, 1-3 wrong, a question, a promise or plan without technical content, or just "
                 "talking about the topic. Reason: at most 15 words.")


def explicit_signal(messages, interaction):
    """Sub-agent 1 (rules), looking after the helper's message:
    ("confirmation", ...) someone else says it worked  -> strongest evidence
    ("outcome", ...)      the ticket / task moved to done -> backs the work, but not who did it
    """
    h = interaction["helper_index"]
    helper = interaction["helper_emp_id"]
    outcome = None
    for m in messages[h:]:
        text = m["text"] or ""
        if m["actor_emp_id"] and m["actor_emp_id"] != helper and CONFIRM.search(text) and not NEGATE.search(text):
            return "confirmation", f"{m['actor_name']}: \"{text.strip()[:80]}\""
        if not outcome and m["activity_type"] in ("field_change", "status_change") and DONE_STATUS.search(text):
            outcome = f"outcome: {text.strip()[:80]}"
    return ("outcome", outcome) if outcome else (None, None)


def agent3(messages, interaction):
    kind, signal = explicit_signal(messages, interaction)
    if kind == "confirmation":
        return {"validation": "explicit", "score": 10, "signal": signal, "model": "rule"}
    helper = messages[interaction["helper_index"] - 1]
    asked = messages[interaction["requester_index"] - 1]["text"] if interaction["requester_index"] else "(no explicit question)"
    prompt = (f"Skill claimed: {interaction['skill']} ({interaction['topic']})\nQuestion: {asked[:MAX_CHARS]}\n"
              f"Helper's answer: {(helper['text'] or '')[:MAX_CHARS]}")
    out = llm(AGENT3_MODEL, AGENT3_SYSTEM, prompt, AGENT3_SCHEMA, num_predict=80)
    score = max(1, min(10, int(out.get("score", 1))))
    if kind == "outcome":
        # A closed ticket backs the work but not the person: the helper's own message must still be judged good.
        return {"validation": "outcome", "score": score, "model": AGENT3_MODEL,
                "signal": f"{signal}; answer judged {score}/10: {out.get('reason', '')}"[:200]}
    return {"validation": "implicit", "score": score, "signal": out.get("reason", "")[:160], "model": AGENT3_MODEL}


# ---------------------------------------------------------------- Agent 4: the only writer to the graph

def seed_graph(cur):
    """Taxonomy + declared (explicit) skills from HRMS / resume."""
    for name, (category, _) in TAXONOMY.items():
        cur.execute("INSERT INTO skills (skill_id, name, category) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (slug(name), name, category))
    cur.execute("SELECT emp_id, skill, source FROM employee_declared_skills")
    declared = {}
    for row in cur.fetchall():
        emp_id, source = row["emp_id"], row["source"]
        name, category = canonical_skill(row["skill"])
        cur.execute("INSERT INTO skills (skill_id, name, category) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (slug(name), name, category))
        declared.setdefault((emp_id, slug(name)), set()).add(source)
    for (emp_id, skill_id), sources in declared.items():
        cur.execute("""INSERT INTO employee_skills (emp_id, skill_id, kind, declared_source) VALUES (%s, %s, 'explicit', %s)
                       ON CONFLICT (emp_id, skill_id) DO UPDATE SET kind = 'explicit', declared_source = EXCLUDED.declared_source""",
                    (emp_id, skill_id, ",".join(sorted(sources))))
    return declared


def agent4_write(cur, thread_key, messages, validated):
    """Replace this thread's evidence (idempotent) and store each validated interaction as an evidence row."""
    cur.execute("DELETE FROM skill_evidence WHERE thread_key = %s", (thread_key,))
    by_id = {m["activity_id"]: m for m in messages}
    for item in validated:
        sid = slug(item["skill"])
        cur.execute("INSERT INTO skills (skill_id, name, category) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (sid, item["skill"], item["category"]))
        message = by_id[item["activity_id"]]
        evidence_id = "ev_" + hashlib.md5(f"{item['helper_emp_id']}|{sid}|{item['activity_id']}".encode()).hexdigest()[:12]
        cur.execute(
            """INSERT INTO skill_evidence (evidence_id, emp_id, skill_id, requester_emp_id, thread_key, activity_id,
                   validation, score, signal, topic, source, source_url, occurred_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (evidence_id, item["helper_emp_id"], sid, item["requester_emp_id"], thread_key, item["activity_id"],
             item["validation"], item["score"], item["signal"], item["topic"], message["source"],
             message["source_url"], message["occurred_at"]))


def recompute_graph(cur, declared):
    """Rebuild every employee_skills aggregate from the evidence table (so totals never double count)."""
    cur.execute("""SELECT emp_id, skill_id, count(*) AS n, count(*) FILTER (WHERE validation = 'explicit') AS explicit_n,
                          avg(score) FILTER (WHERE validation = 'implicit') AS implicit_avg,
                          array_agg(DISTINCT requester_emp_id) FILTER (WHERE requester_emp_id IS NOT NULL) AS requesters,
                          array_agg(DISTINCT source) AS sources, min(occurred_at) AS first_seen, max(occurred_at) AS last_seen,
                          sum(CASE WHEN validation = 'explicit' THEN 1.0 WHEN validation = 'outcome' THEN score / 10.0 * 0.85
                                  ELSE score / 10.0 * 0.7 END) AS weight
                   FROM skill_evidence GROUP BY emp_id, skill_id""")
    rows = cur.fetchall()
    cur.execute("""UPDATE employee_skills SET evidence_count = 0, explicit_count = 0, implicit_avg = NULL,
                   requester_emp_ids = '{}', sources = '{}', confidence = NULL, first_seen = NULL, last_seen = NULL""")
    cur.execute("DELETE FROM employee_skills WHERE kind = 'discovered'")
    for r in rows:
        # Confidence grows with validated evidence and saturates: 1 explicit ~0.28, 3 ~0.63, 6 ~0.86.
        confidence = round(1 - math.exp(-float(r["weight"]) / 3), 2)
        kind = "explicit" if (r["emp_id"], r["skill_id"]) in declared else "discovered"
        cur.execute(
            """INSERT INTO employee_skills (emp_id, skill_id, kind, evidence_count, explicit_count, implicit_avg,
                   requester_emp_ids, sources, confidence, first_seen, last_seen, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
               ON CONFLICT (emp_id, skill_id) DO UPDATE SET evidence_count = EXCLUDED.evidence_count,
                   explicit_count = EXCLUDED.explicit_count, implicit_avg = EXCLUDED.implicit_avg,
                   requester_emp_ids = EXCLUDED.requester_emp_ids, sources = EXCLUDED.sources,
                   confidence = EXCLUDED.confidence, first_seen = EXCLUDED.first_seen, last_seen = EXCLUDED.last_seen,
                   updated_at = NOW()""",
            (r["emp_id"], r["skill_id"], kind, r["n"], r["explicit_n"],
             round(float(r["implicit_avg"]), 1) if r["implicit_avg"] is not None else None,
             r["requesters"] or [], r["sources"] or [], confidence, r["first_seen"], r["last_seen"]))


# ---------------------------------------------------------------- pipeline

def run(limit=None, reprocess=False, database_url=None, log=print):
    database_url = database_url or os.getenv("DATABASE_URL")
    with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "skills_schema.sql").read_text(encoding="utf-8"))
        declared = seed_graph(cur)
        conn.commit()
        cur.execute("""SELECT t.thread_key, md5(t.transcript) AS content_hash, r.content_hash AS done_hash
                       FROM activity_threads t LEFT JOIN agent_thread_runs r ON r.thread_key = t.thread_key
                       ORDER BY t.last_activity_at DESC""")
        todo = [t for t in cur.fetchall() if reprocess or t["content_hash"] != t["done_hash"]]
        if limit:
            todo = todo[:limit]
        stats = {"threads": len(todo), "relevant": 0, "interactions": 0, "evidence": 0, "rejected": 0, "errors": 0}
        log(f"Skill pipeline: {len(todo)} new/changed threads (Agent 1: {AGENT1_MODEL}, Agents 2-3: {AGENT2_MODEL})")
        for n, thread in enumerate(todo, 1):
            key, started = thread["thread_key"], time.monotonic()
            messages = thread_messages(cur, key)
            record = {"agent1": None, "agent2": None, "agent3": None, "error": None}
            try:
                transcript = render(messages)
                record["agent1"] = a1 = agent1(transcript)
                if not a1.get("relevant"):
                    status, validated = "filtered_out", []
                else:
                    stats["relevant"] += 1
                    record["agent2"] = interactions = agent2(transcript, messages)
                    stats["interactions"] += len(interactions)
                    validated, verdicts = [], []
                    for item in interactions:
                        verdict = agent3(messages, item)
                        verdicts.append({**verdict, "skill": item["skill"], "helper": item["helper_emp_id"]})
                        # explicit confirmation always counts; outcome-backed or implicit answers need a good score
                        if verdict["validation"] == "explicit" or verdict["score"] >= IMPLICIT_MIN_SCORE:
                            validated.append({**item, **verdict})
                        else:
                            stats["rejected"] += 1
                    record["agent3"] = verdicts
                    status = "processed" if interactions else "no_interactions"
                agent4_write(cur, key, messages, validated)
                stats["evidence"] += len(validated)
            except Exception as exc:  # keep going; the thread is retried next run
                status, record["error"] = "error", f"{exc.__class__.__name__}: {exc}"
                stats["errors"] += 1
                thread["content_hash"] = "retry"
            cur.execute(
                """INSERT INTO agent_thread_runs (thread_key, content_hash, status, agent1, agent2, agent3, models, error, processed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                   ON CONFLICT (thread_key) DO UPDATE SET content_hash = EXCLUDED.content_hash, status = EXCLUDED.status,
                       agent1 = EXCLUDED.agent1, agent2 = EXCLUDED.agent2, agent3 = EXCLUDED.agent3,
                       models = EXCLUDED.models, error = EXCLUDED.error, processed_at = NOW()""",
                (key, thread["content_hash"], status, json.dumps(record["agent1"]), json.dumps(record["agent2"]),
                 json.dumps(record["agent3"]),
                 json.dumps({"agent1": AGENT1_MODEL, "agent2": AGENT2_MODEL, "agent3": AGENT3_MODEL}), record["error"]))
            recompute_graph(cur, declared)
            conn.commit()
            found = ", ".join(f"{v['helper_emp_id']}:{v['skill']}" for v in validated) if status == "processed" else ""
            log(f"  [{n}/{len(todo)}] {status:15} {time.monotonic() - started:5.0f}s  {key[:60]}"
                + (f"  -> {found}" if found else "") + (f"  ({record['error']})" if record["error"] else ""))
        recompute_graph(cur, declared)
        conn.commit()
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="process at most N threads")
    parser.add_argument("--reprocess", action="store_true", help="re-run every thread, not only new/changed ones")
    args = parser.parse_args()
    stats = run(limit=args.limit, reprocess=args.reprocess)
    print(f"Done: {stats['threads']} threads, {stats['relevant']} relevant, {stats['interactions']} interactions, "
          f"{stats['evidence']} evidence rows, {stats['rejected']} rejected by Agent 3, {stats['errors']} errors")


if __name__ == "__main__":
    main()
