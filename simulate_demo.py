"""Post a realistic week of team activity into the demo Jira site and ClickUp workspace.

Each action is performed with the acting person's own token, so the platforms record the real sender:
- ClickUp: CLICKUP_API_TOKEN (phani) plus CLICKUP_USER_TOKEN_<NAME> for the other members.
- Jira: JIRA_EMAIL/JIRA_API_TOKEN (phani). Other people's Jira comments are only posted when
  JIRA_USER_TOKEN_<NAME>=<their email>:<their API token> is set; otherwise they are skipped and reported.

Usage:
    python simulate_demo.py                 # Jira + ClickUp, then sync both into PostgreSQL
    python simulate_demo.py --only clickup
    python simulate_demo.py --force         # post again even if demo data already exists

Jira issues get the label "poc-demo"; ClickUp tasks go into a List named "POC Demo".
"""

import argparse
import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

DEMO_LABEL = "poc-demo"
DEMO_LIST = "POC Demo"
PEOPLE = ("phani", "nikhil", "tharun", "ramu", "pradeep")


def call(method, url, headers, body=None, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    headers = {**headers, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw.strip() else None
        except HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                time.sleep(15)
                continue
            raise RuntimeError(f"{method} {url} -> {exc.code}: {exc.read()[:300].decode(errors='replace')}") from exc


def due(days):
    return datetime.now(timezone.utc) + timedelta(days=days)


# ---------------------------------------------------------------- Jira

JIRA_ISSUES = [
    {"summary": "Payment API returns 500 under peak load", "type": "Bug", "assignee": "ramu",
     "watchers": ["tharun", "pradeep"], "labels": ["backend", "incident"], "due": 1,
     "description": "Checkout fails with HTTP 500 when traffic goes above 200 rps. @Ramu please investigate; @Tharun FYI for infra.",
     "comments": [
         ("phani", "@Ramu logs show 'too many connections' from Postgres. Can you check the connection pool settings?"),
         ("ramu", "Root cause: SQLAlchemy pool_size=5 with no overflow. Raised pool_size to 20, max_overflow to 10 and added retries with exponential backoff. Load test at 400 rps is clean."),
         ("phani", "Great fix @Ramu. @Pradeep please add a regression load test for checkout."),
     ],
     "transitions": ["In Progress", "Done"]},
    {"summary": "Add cross-encoder reranking to the RAG pipeline", "type": "Story", "assignee": "nikhil",
     "watchers": ["pradeep"], "labels": ["rag", "ml"], "due": 5,
     "description": "Retrieval precision drops on long queries. Add a reranking stage on top of vector search.",
     "comments": [
         ("phani", "@nikhil try a cross-encoder reranker over the top-20 retrieved chunks."),
         ("nikhil", "Added an ms-marco MiniLM cross-encoder reranking top-20 to top-5. MRR@10 went from 0.41 to 0.58 on our eval set."),
         ("phani", "@Pradeep can you add MRR and nDCG to the evaluation dashboard?"),
     ],
     "transitions": ["In Progress"]},
    {"summary": "Autoscale the LLM inference service on Kubernetes", "type": "Task", "assignee": "tharun",
     "watchers": ["phani"], "labels": ["devops", "kubernetes"], "due": 4,
     "description": "Inference pods saturate at peak traffic. Scale on GPU utilisation instead of CPU.",
     "comments": [
         ("phani", "@Tharun please set up an HPA driven by GPU utilisation."),
         ("tharun", "HPA now uses DCGM exporter GPU metrics through the Prometheus adapter and scales 2 to 8 replicas at 70% GPU. Also enabled vLLM continuous batching."),
     ],
     "transitions": ["In Progress", "In Review"]},
    {"summary": "Evaluation metrics dashboard for agent workflows", "type": "Task", "assignee": "pradeep",
     "watchers": ["nikhil"], "labels": ["evaluation", "frontend"], "due": 7,
     "description": "Track task success rate, tool-call accuracy and latency per agent.",
     "comments": [
         ("phani", "@Pradeep we need task success, tool-call accuracy and p95 latency per agent."),
         ("pradeep", "Built it with FastAPI and React, pulling traces from LangSmith. @nikhil can you share the reranking eval numbers so I can add them?"),
     ],
     "transitions": ["In Progress"]},
    {"summary": "Docker build fails on ARM CI runners", "type": "Bug", "assignee": "tharun",
     "watchers": ["ramu"], "labels": ["devops", "docker"], "due": 2,
     "description": "Image builds fail with 'exec format error' on the new ARM runners.",
     "comments": [
         ("phani", "@Tharun @Ramu does anyone know multi-arch builds?"),
         ("ramu", "Use docker buildx with --platform linux/amd64,linux/arm64 and QEMU emulation. I fixed the same problem in the payments repo last month."),
     ],
     "transitions": ["In Progress", "Done"]},
]


class Jira:
    def __init__(self):
        self.base = os.environ["JIRA_BASE_URL"].rstrip("/")
        self.project = os.getenv("JIRA_PROJECT", "KAN")
        self.auth = {"phani": self._header(os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])}
        for name in PEOPLE:
            value = os.getenv(f"JIRA_USER_TOKEN_{name.upper()}", "").strip()
            email, _, token = value.partition(":")
            if email.strip() and token.strip():  # "email:" with no token yet = not provided
                self.auth[name] = self._header(email.strip(), token.strip())
        users = self.get("/rest/api/3/users/search", {"maxResults": 200})
        self.accounts = {u["displayName"].lower(): u["accountId"] for u in users if u.get("accountType") == "atlassian"}
        missing = [p for p in PEOPLE if p not in self.accounts]
        if missing:
            raise RuntimeError(f"Jira users not found by display name: {missing}")

    @staticmethod
    def _header(email, token):
        return {"Authorization": "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()}

    def get(self, path, params=None, who="phani"):
        return call("GET", f"{self.base}{path}" + (f"?{urlencode(params)}" if params else ""), self.auth[who])

    def post(self, path, body, who="phani"):
        return call("POST", f"{self.base}{path}", self.auth[who], body)

    def adf(self, text):
        """Plain text with @Name tokens -> Atlassian Document Format with real mention nodes."""
        content = []
        for part in re.split(r"(@\w+)", text):
            name = part[1:].lower()
            if part.startswith("@") and name in self.accounts:
                content.append({"type": "mention", "attrs": {"id": self.accounts[name], "text": part}})
            elif part:
                content.append({"type": "text", "text": part})
        return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": content}]}

    def exists(self):
        found = self.post("/rest/api/3/search/jql", {"jql": f"labels = {DEMO_LABEL}", "maxResults": 1, "fields": ["summary"]})
        return bool(found.get("issues"))

    def run(self):
        skipped = []
        for spec in JIRA_ISSUES:
            fields = {"project": {"key": self.project}, "summary": spec["summary"],
                      "issuetype": {"name": spec["type"]}, "description": self.adf(spec["description"]),
                      "assignee": {"accountId": self.accounts[spec["assignee"]]},
                      "labels": [DEMO_LABEL, *spec["labels"]], "duedate": due(spec["due"]).date().isoformat()}
            try:
                key = self.post("/rest/api/3/issue", {"fields": fields})["key"]
            except RuntimeError:
                fields["issuetype"] = {"name": "Task"}  # the project may not have Bug/Story types
                key = self.post("/rest/api/3/issue", {"fields": fields})["key"]
            print(f"  Jira {key}: {spec['summary']} (assigned to {spec['assignee']})")
            for watcher in spec["watchers"]:
                self.post(f"/rest/api/3/issue/{key}/watchers", self.accounts[watcher])
            for author, text in spec["comments"]:
                if author not in self.auth:
                    skipped.append(f"{key} comment by {author}")
                    continue
                self.post(f"/rest/api/3/issue/{key}/comment", {"body": self.adf(text)}, who=author)
            for status in spec["transitions"]:
                options = self.get(f"/rest/api/3/issue/{key}/transitions")["transitions"]
                match = next((t for t in options if status.lower() in (t["name"].lower(), t["to"]["name"].lower())), None)
                if match:
                    self.post(f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": match["id"]}})
                else:
                    print(f"    (no '{status}' transition available on {key})")
        if skipped:
            print(f"  Skipped {len(skipped)} Jira comments by people without a JIRA_USER_TOKEN_<NAME>: "
                  + ", ".join(sorted({s.split(' by ')[1] for s in skipped})))


# ---------------------------------------------------------------- ClickUp

CLICKUP_TASKS = [
    {"creator": "phani", "name": "Set up CI/CD pipeline with GitHub Actions", "assignees": ["tharun"],
     "priority": 2, "due": 3, "tags": ["devops"],
     "description": "Lint, test and build a Docker image on every pull request; deploy on merge to main.",
     "thread": [("phani", "@Tharun please set up lint, tests and Docker build on every PR."),
                ("tharun", "Pipeline is live: ruff, pytest and docker buildx, pushing images to ECR on main. Average run is 4 minutes.")],
     "status": [("tharun", "progress"), ("tharun", "done")]},
    {"creator": "pradeep", "name": "Fix memory leak in FastAPI worker", "assignees": ["ramu"],
     "priority": 1, "due": 1, "tags": ["backend", "python"],
     "description": "Worker memory grows until it is OOM-killed every few hours.",
     "thread": [("pradeep", "@Ramu worker RSS grows about 200MB per hour until OOM. Any ideas?"),
                ("ramu", "Found it: a new httpx.AsyncClient was created per request and never closed. Moved to one shared client in the app lifespan; memory is flat now."),
                ("pradeep", "Confirmed flat after a 6 hour soak test, thanks!")],
     "status": [("ramu", "progress"), ("ramu", "done")]},
    {"creator": "nikhil", "name": "Benchmark vector databases for RAG", "assignees": ["nikhil", "ramu"],
     "priority": 3, "due": 6, "tags": ["rag"],
     "description": "Compare pgvector and Qdrant at 1M vectors for latency and cost.",
     "thread": [("nikhil", "@Ramu can you help compare pgvector and Qdrant at 1M vectors?"),
                ("ramu", "pgvector with HNSW (m=16, ef=64) gives p95 18ms; Qdrant gives p95 9ms. At our scale pgvector is enough and keeps us on one database.")],
     "status": [("nikhil", "progress")]},
    {"creator": "ramu", "name": "Write incident response SOP", "assignees": ["pradeep"],
     "priority": 3, "due": 8, "tags": ["docs"],
     "description": "On-call runbook based on last week's payment incident.",
     "thread": [("ramu", "@Pradeep can you draft the on-call runbook from last week's payment incident?"),
                ("pradeep", "Draft ready: detection, triage, rollback, comms template and postmortem checklist.")],
     "status": [("pradeep", "progress")]},
    {"creator": "phani", "name": "Prompt caching for agent workflows", "assignees": ["nikhil"],
     "priority": 2, "due": 4, "tags": ["llm", "cost"],
     "description": "Token costs doubled this month; cache shared prompt prefixes.",
     "thread": [("phani", "@nikhil token costs doubled this month. Can we cache the system prompts?"),
                ("nikhil", "Enabled prompt caching for the shared system prompt and tool definitions; cost per run is down 38% and latency down 22%.")],
     "status": [("nikhil", "progress"), ("nikhil", "done")]},
]

# (sender, other participants, [(speaker, text, reply_to_index or None, reactions [(reactor, emoji)])])
CLICKUP_CHATS = [
    ("phani", ["tharun"], [
        ("phani", "Tharun, how do I roll back a failed Helm release in prod?", None, []),
        ("tharun", "Run `helm history <release>` to find the last good revision, then `helm rollback <release> <revision> --wait`.", None, [("phani", "+1")]),
    ]),
    ("pradeep", ["ramu"], [
        ("pradeep", "Ramu, semantic search got slow after we crossed 500k documents. What should I check?", None, []),
        ("ramu", "Add an HNSW index on the embedding column, run ANALYZE and cap ef_search at 64. It should drop from seconds to around 20ms.", None, []),
        ("pradeep", "That fixed it, 25ms now. Thanks!", None, [("ramu", "tada")]),
    ]),
    ("nikhil", ["phani"], [
        ("nikhil", "phani, the reranker is done: MRR@10 went from 0.41 to 0.58.", None, []),
        ("phani", "Excellent work! Let's present it in Friday's demo.", None, [("nikhil", "+1")]),
    ]),
    ("phani", ["ramu", "tharun"], [
        ("phani", "Payments outage postmortem at 4pm. @Ramu can you walk through the root cause?", None, []),
        ("ramu", "Yes: database connection pool exhaustion. I'll share the timeline and the fix.", None, []),
        ("tharun", "I'll add the Grafana alert we created for pool saturation.", 1, []),
    ]),
]


class ClickUp:
    V2 = "https://api.clickup.com/api/v2"
    V3 = "https://api.clickup.com/api/v3"

    def __init__(self):
        self.team = os.environ["CLICKUP_TEAM_ID"]
        tokens = {"phani": os.environ["CLICKUP_API_TOKEN"]}
        tokens.update({n: os.getenv(f"CLICKUP_USER_TOKEN_{n.upper()}", "").strip() for n in PEOPLE if n != "phani"})
        self.tokens, self.ids = {}, {}
        for name, token in tokens.items():
            if not token:
                continue
            user = call("GET", f"{self.V2}/user", {"Authorization": token})["user"]
            self.tokens[name], self.ids[name] = token, int(user["id"])
        missing = [p for p in PEOPLE if p not in self.tokens]
        if missing:
            raise RuntimeError(f"Missing ClickUp tokens for: {missing}")

    def req(self, method, url, who, body=None):
        return call(method, url, {"Authorization": self.tokens[who]}, body)

    def demo_list(self, create):
        spaces = self.req("GET", f"{self.V2}/team/{self.team}/space?archived=false", "phani")["spaces"]
        for space in spaces:
            for lst in self.req("GET", f"{self.V2}/space/{space['id']}/list?archived=false", "phani").get("lists", []):
                if lst["name"] == DEMO_LIST:
                    return lst
        if not create:
            return None
        return self.req("POST", f"{self.V2}/space/{spaces[0]['id']}/list", "phani", {"name": DEMO_LIST})

    def comment_body(self, text):
        """Text with @Name tokens -> ClickUp comment segments with real user tags."""
        segments = []
        for part in re.split(r"(@\w+)", text):
            name = part[1:].lower()
            if part.startswith("@") and name in self.ids:
                segments.append({"type": "tag", "user": {"id": self.ids[name]}})
            elif part:
                segments.append({"text": part})
        # Sending both "comment" and "comment_text" makes ClickUp store the text twice.
        return {"comment": segments, "notify_all": False}

    def run(self):
        lst = self.demo_list(create=True)
        statuses = self.req("GET", f"{self.V2}/list/{lst['id']}", "phani").get("statuses", [])
        names = [s["status"] for s in statuses]
        status_for = {
            "progress": next((n for n in names if "progress" in n.lower()), names[1] if len(names) > 1 else None),
            "done": next((s["status"] for s in statuses if s.get("type") in ("closed", "done")), names[-1] if names else None),
        }
        print(f"  ClickUp list '{DEMO_LIST}' statuses: {names}")
        for spec in CLICKUP_TASKS:
            task = self.req("POST", f"{self.V2}/list/{lst['id']}/task", spec["creator"], {
                "name": spec["name"], "description": spec["description"], "priority": spec["priority"],
                "assignees": [self.ids[a] for a in spec["assignees"]], "tags": spec["tags"],
                "due_date": int(due(spec["due"]).timestamp() * 1000)})
            print(f"  ClickUp task by {spec['creator']}: {spec['name']} (assigned to {', '.join(spec['assignees'])})")
            first_author, first_text = spec["thread"][0]
            first = self.req("POST", f"{self.V2}/task/{task['id']}/comment", first_author, self.comment_body(first_text))
            for author, text in spec["thread"][1:]:
                self.req("POST", f"{self.V2}/comment/{first['id']}/reply", author, self.comment_body(text))
            for actor, key in spec["status"]:
                if status_for.get(key):
                    self.req("PUT", f"{self.V2}/task/{task['id']}", actor, {"status": status_for[key]})

        chat = f"{self.V3}/workspaces/{self.team}/chat"
        for sender, others, lines in CLICKUP_CHATS:
            channel = self.req("POST", f"{chat}/channels/direct_message", sender,
                               {"user_ids": [str(self.ids[o]) for o in others]})
            channel_id = (channel.get("data") or channel)["id"]
            posted = []
            for speaker, text, reply_to, reactions in lines:
                body = {"type": "message", "content": text, "content_format": "text/md"}
                if reply_to is None:
                    message = self.req("POST", f"{chat}/channels/{channel_id}/messages", speaker, body)
                else:
                    message = self.req("POST", f"{chat}/messages/{posted[reply_to]['id']}/replies", speaker, body)
                posted.append(message)
                for reactor, emoji in reactions:
                    try:
                        self.req("POST", f"{chat}/messages/{message['id']}/reactions", reactor, {"reaction": emoji})
                    except RuntimeError as exc:
                        print(f"    (reaction '{emoji}' not accepted: {exc})")
            print(f"  ClickUp {'DM' if len(others) == 1 else 'group DM'}: {sender} -> {', '.join(others)} ({len(lines)} messages)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=("jira", "clickup"))
    parser.add_argument("--force", action="store_true", help="post even if demo data already exists")
    parser.add_argument("--no-sync", action="store_true", help="do not sync into PostgreSQL afterwards")
    args = parser.parse_args()

    from integrations import sync
    if args.only in (None, "jira"):
        print("Jira:")
        jira = Jira()
        if jira.exists() and not args.force:
            print(f"  issues labelled {DEMO_LABEL} already exist; skipping (use --force to post again)")
        else:
            jira.run()
        if not args.no_sync:
            print(f"  synced {sync.sync_jira()} Jira records")
    if args.only in (None, "clickup"):
        print("ClickUp:")
        clickup = ClickUp()
        existing = clickup.demo_list(create=False)
        if existing and existing.get("task_count") and not args.force:
            print(f"  list '{DEMO_LIST}' already has tasks; skipping (use --force to post again)")
        else:
            clickup.run()
        if not args.no_sync:
            print(f"  synced {sync.sync_clickup(full=True)} ClickUp records")


if __name__ == "__main__":
    main()
