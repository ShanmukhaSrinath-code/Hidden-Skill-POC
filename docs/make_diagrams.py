"""Regenerate the static README diagrams in docs/images/ (run from the project folder: python docs/make_diagrams.py)."""
from pathlib import Path

OUT = Path("docs/images")
FONT = 'font-family="Segoe UI, Inter, Helvetica, Arial, sans-serif"'
MONO = 'font-family="Consolas, Menlo, monospace"'
INK, MUTED, BLUE, TEAL, AMBER, LINE = "#14213d", "#5b6b85", "#2f6fed", "#0f9d8f", "#b7791f", "#dfe6f0"
ARROW = ('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
         '<path d="M0,0 L10,5 L0,10 z" fill="#2f6fed"/></marker></defs>')


def svg(inner, w, h, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{title}" {FONT}>\n<title>{title}</title>\n'
            f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="20" fill="#ffffff" stroke="{LINE}" stroke-width="2"/>\n'
            f'{ARROW}{inner}\n</svg>\n')


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x, y, w, h, title, lines=(), fill="#fff", stroke=LINE, tcolor=INK, size=15, lsize=12.5, lcolor=MUTED, dash=False):
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.6"'
           + (' stroke-dasharray="6 5"' if dash else "") + "/>")
    out += f'<text x="{x + 16}" y="{y + 28}" font-size="{size}" font-weight="700" fill="{tcolor}">{esc(title)}</text>'
    for i, line in enumerate(lines):
        out += f'<text x="{x + 16}" y="{y + 52 + i * 19}" font-size="{lsize}" fill="{lcolor}">{esc(line)}</text>'
    return out


def arrow(d, color=BLUE, width=2.2, dash=False):
    return (f'<path d="{d}" stroke="{color}" stroke-width="{width}" fill="none" marker-end="url(#a)"'
            + (' stroke-dasharray="6 5"' if dash else "") + "/>")


def band(y, h, label, color, status="✓ built", lx=40):
    return (f'<rect x="20" y="{y}" width="1160" height="{h}" rx="16" fill="{color}" opacity="0.55"/>'
            f'<text x="{lx}" y="{y + 26}" font-size="13" font-weight="800" fill="{MUTED}" letter-spacing="1.4">{esc(label)}</text>'
            f'<text x="1160" y="{y + 26}" font-size="13" font-weight="700" fill="{TEAL}" text-anchor="end">{esc(status)}</text>')


# ---------------------------------------------------------------- architecture
a = band(20, 190, "COLLECT · 4 SOURCES", "#f4f6fa") + band(230, 190, "PHASE 1 · IDENTITY + CANONICAL RECORDS", "#eef3fd") \
    + band(440, 200, "PHASE 2 · AGENTS ON A LOCAL MODEL (OLLAMA) → SKILL GRAPH", "#fdf6e9", lx=250)
a += box(40, 52, 210, 140, "📡 Sources")
for i, (name, color) in enumerate([("💬 Slack", "#4a154b"), ("🎫 Jira", "#0052cc"), ("📘 Confluence", "#1868db"), ("✅ ClickUp", "#7b68ee")]):
    x, y = 54 + (i % 2) * 96, 94 + (i // 2) * 34
    a += (f'<rect x="{x}" y="{y}" width="90" height="26" rx="7" fill="#fff" stroke="{color}" stroke-width="1.5"/>'
          f'<text x="{x + 45}" y="{y + 17}" font-size="11.5" font-weight="700" fill="{INK}" text-anchor="middle">{name}</text>')
a += f'<text x="54" y="178" font-size="11" fill="{MUTED}">next: Outlook · Teams · Gong</text>'
a += box(300, 52, 220, 140, "🔌 Connectors", ["ingest.py (Socket Mode)", "jira · confluence · clickup.py", "every key = what its owner sees", "instant / every 60 s"])
a += box(570, 52, 250, 140, "🗄️ Raw tables", ["slack_* (Slack)", "unified_* (Jira · Confluence · ClickUp)", "sender · recipients · @mentions", "upserted · source links"])
a += box(870, 52, 290, 140, "📊 Live dashboard  /", ["live stream of every message", "Person + Platform filters", "illustrated /how-it-works"], fill="#14213d", stroke="#14213d", tcolor="#fff", lcolor="#9fb8e6")
a += arrow("M250 122 L296 122") + arrow("M520 122 L566 122") + arrow("M820 122 L866 122")
a += box(40, 262, 210, 140, "👥 HRMS file", ["EMP_ID · role · department", "declared + resume skills", "data/hrms_employees.csv"])
a += box(300, 262, 220, 140, "🪪 Identity resolution", ["every account → EMP_ID", "matched by email", "manual overrides · no LLM"], fill="#fff", stroke="#9fbaf6")
a += box(570, 262, 250, 140, "🧾 activity_records", ["who · did what · where · when", "+ business context + link", "all sources · people as EMP_IDs", "bots & join notices removed"], stroke="#9fbaf6")
a += box(870, 262, 290, 140, "🧵 activity_threads", ["one help exchange = one thread", "readable transcript", "evidence links", "→ input for Agent 1"], stroke="#9fbaf6")
a += arrow("M250 332 L296 332") + arrow("M520 332 L566 332") + arrow("M820 332 L866 332")
a += arrow("M695 192 C695 232 470 222 470 258")
agents = [("1 · Filter", "qwen2.5:3b", ["keep help, answers,", "fixes · drop noise"]),
          ("2 · Who & what", "qwen2.5:7b", ["helper · asker", "skill · topic"]),
          ("3 · Validate", "rules + qwen2.5:7b", ["confirmed? → 10/10", "else judged, keep ≥ 7"]),
          ("4 · Graph write", "code, no LLM", ["only writer", "never double counts"])]
for i, (name, model, lines) in enumerate(agents):
    x = 40 + i * 192
    a += (f'<rect x="{x}" y="474" width="172" height="148" rx="14" fill="#fff" stroke="#f1dfb6" stroke-width="1.6"/>'
          f'<circle cx="{x + 26}" cy="502" r="15" fill="{AMBER}"/><text x="{x + 26}" y="507" font-size="13" font-weight="800" fill="#fff" text-anchor="middle">{i + 1}</text>'
          f'<text x="{x + 48}" y="507" font-size="14" font-weight="700" fill="{INK}">{esc(name.split(" · ")[1])}</text>'
          f'<rect x="{x + 14}" y="522" width="144" height="22" rx="6" fill="#fcf0dc"/><text x="{x + 86}" y="537" font-size="11" font-weight="700" fill="{AMBER}" text-anchor="middle" {MONO}>{esc(model)}</text>'
          + "".join(f'<text x="{x + 16}" y="{570 + j * 19}" font-size="12.5" fill="{MUTED}">{esc(t)}</text>' for j, t in enumerate(lines)))
    if i < 3:
        a += arrow(f"M{x + 172} 548 L{x + 188} 548")
a += box(820, 474, 340, 148, "🧠 Skill graph  /skills", ["explicit (declared) vs discovered skills", "evidence → the original message", "expert finder · hidden skills", "who people go to for help"], fill="#14213d", stroke="#14213d", tcolor="#fff", lcolor="#ffd99a")
a += arrow("M808 548 L816 548")
a += arrow("M1015 402 C1015 446 126 424 126 470")
(OUT / "architecture.svg").write_text(svg(a, 1200, 660, "System architecture: collect, Phase 1 identity and canonical records, Phase 2 agents and skill graph"), encoding="utf-8")

# ---------------------------------------------------------------- agents walkthrough
g = f'<text x="40" y="44" font-size="13" font-weight="800" fill="{MUTED}" letter-spacing="1.4">ONE REAL THREAD THROUGH AGENTS 1–4</text>'
g += box(30, 64, 330, 236, "✅ ClickUp · DM Pradeep ↔ Ramu")
bubbles = [("P", AMBER, "Semantic search got slow after", "500k documents. What should I check?"),
           ("R", TEAL, "Add an HNSW index, run ANALYZE,", "cap ef_search at 64 → ~20ms."),
           ("P", AMBER, "That fixed it, 25ms now. Thanks!", "")]
for i, (who, color, l1, l2) in enumerate(bubbles):
    y = 108 + i * 62
    g += (f'<circle cx="58" cy="{y + 18}" r="14" fill="{color}"/><text x="58" y="{y + 23}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">{who}</text>'
          f'<rect x="80" y="{y}" width="266" height="{48 if l2 else 34}" rx="10" fill="{"#e2f5f1" if who == "R" else "#f3f5f9"}"/>'
          f'<text x="92" y="{y + 21}" font-size="12" fill="{INK}">{esc(l1)}</text>'
          + (f'<text x="92" y="{y + 39}" font-size="12" fill="{INK}">{esc(l2)}</text>' if l2 else ""))
cols = [("1 · Filter", "qwen2.5:3b", "KEEP", TEAL, ["signal: helpful answer", "noise (greetings, memes,", "status chatter) is dropped"]),
        ("2 · Who & what", "qwen2.5:7b", "Ramu → Pradeep", INK, ["skill: Vector Databases", "topic: slow semantic search", "people as EMP_IDs"]),
        ("3 · Validate", "rules first", "CONFIRMED 10/10", TEAL, ["“That fixed it, 25ms now”", "from someone else = proof", "no proof? judged 1–10"]),
        ("4 · Graph write", "code", "DISCOVERED", AMBER, ["Ramu · Vector Databases", "not in his Frontend role", "ev_… → message link"])]
for i, (name, model, result, color, lines) in enumerate(cols):
    x = 400 + i * 196
    g += (f'<rect x="{x}" y="64" width="180" height="66" rx="14" fill="#fff8ec" stroke="#f1dfb6" stroke-width="1.6"/>'
          f'<text x="{x + 16}" y="92" font-size="14.5" font-weight="700" fill="{INK}">{esc(name)}</text>'
          f'<text x="{x + 16}" y="114" font-size="11.5" fill="{AMBER}" font-weight="700" {MONO}>{esc(model)}</text>'
          + arrow(f"M{x + 90} 132 L{x + 90} 152")
          + f'<rect x="{x}" y="156" width="180" height="144" rx="14" fill="#fff" stroke="{LINE}" stroke-width="1.6"/>'
          f'<text x="{x + 90}" y="188" font-size="15" font-weight="800" fill="{color}" text-anchor="middle">{esc(result)}</text>'
          + "".join(f'<text x="{x + 14}" y="{220 + j * 22}" font-size="12" fill="{MUTED}">{esc(t)}</text>' for j, t in enumerate(lines)))
    if i < 3:
        g += arrow(f"M{x + 180} 97 L{x + 192} 97")
g += arrow("M360 97 L396 97")
g += f'<text x="40" y="344" font-size="13" font-weight="800" fill="{MUTED}" letter-spacing="1.4">AGENT 3 DECISION RULES</text>'
rules = [("✅", "Confirmed by someone else", ["“thanks, it worked” → evidence,", "scored 10/10"], "#e2f5f1", TEAL),
         ("🎫", "Ticket / task moved to Done", ["backs the outcome; the answer is", "still judged, kept if ≥ 7"], "#eef3fd", BLUE),
         ("🤔", "No signal at all", ["local model judges correctness", "1–10, kept if ≥ 7"], "#fcf0dc", AMBER),
         ("🚫", "Not help", ["questions, promises, assignments,", "own status reports"], "#fdf1f2", "#b4434b")]
for i, (icon, title, lines, fill, color) in enumerate(rules):
    x = 30 + i * 288
    g += (f'<rect x="{x}" y="360" width="276" height="86" rx="12" fill="{fill}"/>'
          f'<text x="{x + 14}" y="388" font-size="14" font-weight="700" fill="{color}">{icon} {esc(title)}</text>'
          + "".join(f'<text x="{x + 14}" y="{410 + j * 18}" font-size="12" fill="{MUTED}">{esc(t)}</text>' for j, t in enumerate(lines)))
(OUT / "agents.svg").write_text(svg(g, 1200, 470, "Agents 1-4 applied to one real thread"), encoding="utf-8")


# ---------------------------------------------------------------- data model v2
def table_box(x, y, w, title, rows, fill, stroke, code):
    h = 76 + len(rows) * 34
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="40" rx="10" fill="{INK}"/>'
           f'<text x="{x + 14}" y="{y + 26}" font-size="13" font-weight="700" fill="#fff" {MONO}>{esc(code)}</text>')
    out += arrow(f"M{x + w / 2} {y + 40} L{x + w / 2} {y + 62}")
    y += 66
    out += (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
            f'<text x="{x + 16}" y="{y + 28}" font-size="15" font-weight="700" fill="{INK}">{esc(title)}</text>')
    for i, (name, note, view) in enumerate(rows):
        ry = y + 44 + i * 34
        out += (f'<rect x="{x + 12}" y="{ry}" width="{w - 24}" height="28" rx="7" fill="{"#f3fbf9" if view else "#fff"}" stroke="#e3e8f0"/>'
                f'<text x="{x + 24}" y="{ry + 19}" font-size="12.5" font-weight="600" fill="{INK}" {MONO}>{esc(name)}</text>'
                f'<text x="{x + w - 22}" y="{ry + 19}" font-size="11" fill="{MUTED}" text-anchor="end">{esc(note)}</text>')
    return out


dm = table_box(30, 30, 360, "🗄️ Raw · per source", [
    ("slack_messages", "text · permalink", 0), ("slack_conversations", "channels · DMs", 0),
    ("conversation_members", "recipients", 0), ("slack_mentions", "@user @here", 0), ("slack_users", "people", 0),
    ("unified_records", "Jira · Conf. · ClickUp", 0), ("unified_participants", "recipients · roles", 0),
    ("unified_mentions", "@mentions", 0), ("unified_message_map", "view", 1)], "#f6f3fa", "#e4d8ee",
    "ingest.py · integrations/*.py")
dm += table_box(420, 30, 360, "🪪 Phase 1 · canonical", [
    ("employees", "EMP_ID · role (HRMS)", 0), ("employee_declared_skills", "hrms + resume", 0),
    ("person_accounts", "account → EMP_ID", 0), ("activity_records", "all sources, EMP_IDs", 0),
    ("activity_threads", "view · Agent 1 input", 1), ("employee_activity_summary", "view", 1)], "#eef3fd", "#c9d8fb",
    "integrations/identity.py")
dm += table_box(810, 30, 360, "🧠 Phase 2 · skill graph", [
    ("agent_thread_runs", "every agent decision", 0), ("skills", "taxonomy", 0),
    ("skill_evidence", "validated · linked", 0), ("employee_skills", "explicit / discovered", 0),
    ("skill_experts", "view · ranking", 1), ("help_edges", "view · who helps whom", 1)], "#fdf6e9", "#f1dfb6",
    "integrations/skills.py")
dm += arrow("M390 300 L416 300") + arrow("M780 300 L806 300")
dm += (f'<rect x="420" y="440" width="750" height="56" rx="12" fill="{INK}"/><text x="440" y="474" font-size="14" fill="#fff">'
       f'📊 <tspan font-weight="700" {MONO}>dashboard.py</tspan> reads everything:  /  live stream · /skills skill graph · /how-it-works</text>')
dm += f'<text x="30" y="470" font-size="12" fill="{MUTED}">🔑 keys live in .env (never committed)</text><text x="30" y="490" font-size="12" fill="{MUTED}">white row = table · green row = view</text>'
(OUT / "data-model.svg").write_text(svg(dm, 1200, 520, "Data model: raw tables, Phase 1 canonical tables, Phase 2 skill graph"), encoding="utf-8")

# ---------------------------------------------------------------- identity v2
i2 = f'<g stroke="#9fbaf6" stroke-width="2"><path d="M290 70 L460 140"/><path d="M290 240 L460 180"/><path d="M910 70 L740 140"/><path d="M910 240 L740 180"/></g>'
for x, y, tool, name in [(60, 40, "💬 Slack", "@ramudhatla"), (60, 210, "🎫 Jira", "Ramu"), (910, 40, "📘 Confluence", "Ramu"), (910, 210, "✅ ClickUp", "Ramu")]:
    i2 += (f'<rect x="{x}" y="{y}" width="230" height="60" rx="12" fill="#fff" stroke="{LINE}" stroke-width="1.6"/>'
           f'<text x="{x + 18}" y="{y + 27}" font-size="14" font-weight="700" fill="{INK}">{tool}</text>'
           f'<text x="{x + 18}" y="{y + 47}" font-size="13" fill="{MUTED}">{name}</text>')
i2 += (f'<rect x="460" y="110" width="280" height="100" rx="18" fill="{INK}"/>'
       f'<text x="600" y="140" font-size="12.5" fill="#9fb8e6" text-anchor="middle">✉️ ramudhatla@outlook.com</text>'
       f'<text x="600" y="168" font-size="19" font-weight="800" fill="#fff" text-anchor="middle">EMP_1004 · Ramu</text>'
       f'<text x="600" y="192" font-size="12.5" fill="#ffd99a" text-anchor="middle">Frontend Developer · Web</text>')
i2 += (f'<rect x="470" y="22" width="260" height="50" rx="12" fill="#fcf0dc"/>'
       f'<text x="600" y="43" font-size="12" font-weight="700" fill="{AMBER}" text-anchor="middle">👥 HRMS + resume (declared)</text>'
       f'<text x="600" y="61" font-size="12" fill="{INK}" text-anchor="middle">React · TypeScript · CSS · Next.js</text>')
i2 += arrow("M600 72 L600 106", AMBER)
i2 += arrow("M600 210 L600 246")
i2 += (f'<rect x="420" y="250" width="360" height="40" rx="10" fill="#eef3fd"/>'
       f'<text x="600" y="275" font-size="13" font-weight="700" fill="{BLUE}" text-anchor="middle">every message, mention and skill is attached to EMP_1004</text>')
(OUT / "identity.svg").write_text(svg(i2, 1200, 310, "Identity resolution: four accounts linked by email to one EMP_ID"), encoding="utf-8")

# ---------------------------------------------------------------- banner v2
TOOLS = [("💬", "Slack", "#4a154b"), ("🎫", "Jira", "#0052cc"), ("📘", "Confluence", "#1868db"), ("✅", "ClickUp", "#7b68ee")]
b = '<defs><linearGradient id="bg" x1="0" x2="1"><stop offset="0" stop-color="#f4f8ff"/><stop offset="1" stop-color="#fdf6e9"/></linearGradient></defs>'
b += '<rect x="1" y="1" width="1198" height="318" rx="20" fill="url(#bg)"/>'
for i, (icon, name, color) in enumerate(TOOLS):
    y = 58 + i * 58
    b += (f'<rect x="50" y="{y}" width="180" height="46" rx="12" fill="#fff" stroke="{LINE}" stroke-width="1.5"/>'
          f'<rect x="50" y="{y}" width="8" height="46" rx="4" fill="{color}"/>'
          f'<text x="74" y="{y + 30}" font-size="18" font-weight="700" fill="{INK}">{icon}  {name}</text>'
          f'<path d="M230 {y + 23} C300 {y + 23} 300 160 352 160" stroke="#9fbaf6" stroke-width="2" fill="none"/>')
b += (f'<circle cx="392" cy="160" r="40" fill="{BLUE}"/><text x="392" y="172" font-size="30" text-anchor="middle">🪪</text>'
      + arrow("M432 160 L470 160", BLUE, 3)
      + f'<circle cx="512" cy="160" r="40" fill="{AMBER}"/><text x="512" y="172" font-size="30" text-anchor="middle">🧠</text>'
      + arrow("M552 160 L590 160", AMBER, 3))
b += (f'<text x="610" y="108" font-size="33" font-weight="800" fill="{INK}" letter-spacing="-1">Agent Messaging Intelligence</text>'
      f'<text x="612" y="146" font-size="18" fill="{MUTED}">Work conversations → evidence-backed skill graph.</text>')
chips = [("🪪 one EMP_ID each", "#eef3fd", BLUE, 168), ("🧠 local-AI agents", "#fcf0dc", AMBER, 164),
         ("🔗 skill → evidence", "#e2f5f1", TEAL, 168)]
x = 612
for text, fill, color, w in chips:
    b += (f'<rect x="{x}" y="172" width="{w}" height="34" rx="17" fill="{fill}"/>'
          f'<text x="{x + w / 2}" y="194" font-size="14" font-weight="600" fill="{color}" text-anchor="middle">{esc(text)}</text>')
    x += w + 10
b += f'<text x="612" y="244" font-size="14" fill="{MUTED}">who talked to whom · explicit vs discovered skills · expert finder</text>'
(OUT / "banner.svg").write_text(svg(b, 1200, 320, "Agent Messaging Intelligence"), encoding="utf-8")

for f in ("architecture", "agents", "data-model", "identity", "banner"):
    print(f, (OUT / f"{f}.svg").stat().st_size)
