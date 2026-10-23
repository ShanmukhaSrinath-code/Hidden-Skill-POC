<p align="center"><img src="docs/images/banner.svg" alt="Agent Messaging Intelligence: who talked to whom, about what, across Slack, Jira, Confluence and ClickUp" width="100%"></p>

<p align="center">
  <b>💬 Slack</b> · <b>🎫 Jira</b> · <b>📘 Confluence</b> · <b>✅ ClickUp</b> &nbsp;→&nbsp; <b>🗄️ PostgreSQL</b> &nbsp;→&nbsp; <b>📊 live dashboard</b>
  <br>
  <a href="#how-it-works">How it works</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#live-demo">Live demo</a> ·
  <a href="#project-structure">Project structure</a>
</p>

---

## ✨ At a glance

| | |
|---|---|
| 📥 **Collects** | messages, DMs, group chats, thread replies, comments, inline comments, page edits, assignments and status changes |
| 🧭 **Maps** | every item to **sender → recipients → @mentions**, plus where, when, text and a link to the original |
| 👤 **Links people** | one person = one email = their Slack, Jira, Confluence and ClickUp accounts |
| 🗄️ **Stores** | everything in one PostgreSQL database, in the same shape for every tool |
| 🪪 **Resolves identity** | every account → one canonical **EMP_ID** from HRMS, with declared skills (Phase 1) |
| 🧠 **Finds skills** | Agents 1–4 on a **local model (Ollama)** turn validated help into an explicit vs discovered **skill graph** (Phase 2) |
| 📊 **Shows** | live stream with Person / Platform filters · `/skills` skill graph · illustrated `/how-it-works` |

<img src="docs/images/architecture.svg" alt="Architecture: collect from 4 sources, Phase 1 identity and canonical records, Phase 2 local-model agents and skill graph" width="100%">

---

<a id="how-it-works"></a>

## 🧭 How it works

<img src="docs/images/pipeline.svg" alt="Pipeline: 4 tools, connectors, mapping, PostgreSQL, dashboard" width="100%">

| Tool | Collected | Updates |
|---|---|---|
| 💬 Slack | channels, private channels, DMs, group DMs, threads, mentions | ⚡ instant (Socket Mode) |
| 🎫 Jira | issues, comments, mentions, assignments, field and status history | ⏱ every 60 s |
| 📘 Confluence | pages, blog posts, comments, inline comments, edits, inline tasks | ⏱ every 60 s |
| ✅ ClickUp | tasks, comments and replies, chat channels, DMs, group DMs | ⏱ every 60 s |

### 🔑 Who can read what

<img src="docs/images/access-keys.svg" alt="Keys: Slack and ClickUp need one key per person for private chats; Jira and Confluence use one admin key" width="100%">

> 🔒 A key only opens what its owner can already see. Private chats need each member's own key. Jira and Confluence have no private chats, so one admin key reads everything.

<details><summary><b>Slack scopes</b></summary>

| Source | Read by | Scopes |
|---|---|---|
| Public channels | Bot | `channels:read`, `channels:history`, `channels:join` |
| Private channels | Bot (once invited) or user token | `groups:read`, `groups:history` |
| Group DMs | User token | `mpim:read`, `mpim:history` |
| 1:1 DMs | User token only | `im:read`, `im:history` |
| Users / user groups | Bot | `users:read`, `users:read.email`, `usergroups:read` |

For live DMs, subscribe the Slack app to `message.channels`, `message.groups`, `message.im`, `message.mpim` (bot events) and `message.im`, `message.mpim` (events on behalf of users).
</details>

### 🧩 One message, mapped

<img src="docs/images/message-mapping.svg" alt="A ClickUp DM from Ramu to Pradeep turned into a stored record with sender, recipients, mentions, where, when, text and link" width="100%">

| Tool | 💭 Conversation | ✉️ Message | 👥 Recipients |
|---|---|---|---|
| 💬 Slack | channel · DM · group DM | message · thread reply | members |
| 🎫 Jira | issue | comment · status change | reporter · assignee · watchers · commenters |
| 📘 Confluence | page · blog post | comment · inline comment · edit | author · editors · commenters · @mentioned |
| ✅ ClickUp | task · chat · DM | comment · reply · chat message | creator · assignees · watchers · members |

### 👤 One person, four accounts

<img src="docs/images/identity.svg" alt="Ramu's Slack, Jira, Confluence and ClickUp accounts linked through his email" width="100%">

> Jira and Confluence hide most users' emails, so each person's own Atlassian token is used to read *their own* email. That links all five demo users on every tool.

### 🗄️ Data model

<img src="docs/images/data-model.svg" alt="Code writes Slack tables and unified tables; views summarise them; dashboard reads both" width="100%">

| View | Answers |
|---|---|
| `unified_message_map` | each item's sender, recipients and mentions |
| `unified_person_messages` | everything a person **sent**, **received** or was **mentioned** in |

### 🪪 Identity + canonical activity records (Phase 1)

<details><summary><b>Flow as text</b></summary>

```
data/hrms_employees.csv ─▶ employees (EMP_ID) + declared skills
                                   │  email
every Slack / Jira / Confluence / ClickUp account ─▶ person_accounts ─▶ EMP_ID
                                   │
all sources ─▶ activity_records  (who · did what · where · when · context, as EMP_IDs)
                                   │
                         activity_threads  (one thread = Agent 1 input)
```
</details>

| Object | Holds |
|---|---|
| `employees`, `employee_declared_skills` | canonical `EMP_ID`, role, department, declared (HRMS + resume) skills |
| `person_accounts` | every platform account → `EMP_ID` (`email`, `manual` or `unmatched`) |
| `activity_records` | one row per message / comment / edit / change from **all** sources, sender + recipients + mentions as EMP_IDs, `dedup_hash`, link |
| `activity_threads` | threads with a readable transcript and evidence links |
| `employee_activity_summary` | sent · received · mentioned per employee and source |

Deterministic, no LLM. `dashboard.py` re-runs it every `CANONICAL_REFRESH_SECONDS` (default 60). Bots and system notices are excluded. Force a link with `data/identity_overrides.csv` (`platform,platform_user_id,emp_id`).

### 🧠 Skill graph: Agents 1–4 on a local model (Phase 2)

<img src="docs/images/agents.svg" alt="One real thread through Agents 1-4, and Agent 3's decision rules" width="100%">

<img src="docs/images/skill-graph.svg" alt="An employee's skill graph: declared skills vs skills discovered from validated help" width="100%">

<details><summary><b>Pipeline as text</b></summary>

```
activity_threads ─▶ Agent 1  relevance filter   qwen2.5:3b   keep help requests · answers · confirmed fixes
                 ─▶ Agent 2  who helped whom    qwen2.5:7b   helper · requester · skill · topic (taxonomy-normalised)
                 ─▶ Agent 3  skill accuracy     rules first  explicit: "that fixed it" / ticket → Done  = 10/10
                                                qwen2.5:7b   otherwise implicit correctness 1–10 (kept if ≥ 7)
                 ─▶ Agent 4  graph update       code only    the only writer · idempotent · explicit vs discovered
                                                    ▼
          employee_skills (EMPLOYEE → HAS_SKILL → SKILL)  ·  skill_evidence (→ SUPPORTED_BY → message link)
```
</details>

Models run **locally through Ollama**; no data leaves the machine. Only new or changed threads are processed. Every agent decision is stored in `agent_thread_runs` for audit.

| Object | Holds |
|---|---|
| `skills` | taxonomy; declared and discovered skills map onto the same names |
| `skill_evidence` | one validated piece of evidence: helper, requester, skill, explicit/implicit, score, signal, link |
| `employee_skills` | the graph: **explicit** (declared) vs **discovered** skills, evidence counts, requesters, confidence |
| `skill_experts`, `help_edges` | expert finder ranking · who people go to for help |

Open **http://127.0.0.1:8000/skills** for the employee skill profile, the evidence behind each skill, the expert finder, hidden skills and help edges.

---

<a id="quick-start"></a>

## 🚀 Quick start

```powershell
# 1 · install
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install slack_sdk "psycopg[binary]" python-dotenv

# 2 · database (Docker Desktop running)
docker run -d --name slack-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=slack_intelligence -p 5432:5432 -v slack-pg-data:/var/lib/postgresql/data postgres:16

# 3 · keys → create .env (see Configuration)

# 4 · run → http://127.0.0.1:8000
.\.venv\Scripts\python.exe dashboard.py
```

The dashboard creates the tables, backfills Slack, then keeps every enabled tool in sync.

---

<a id="configuration"></a>

## ⚙️ Configuration

All keys live in **`.env`**, which is gitignored and never committed.

| | Variable | Notes |
|---|---|---|
| 🗄️ | `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/slack_intelligence` |
| 💬 | `SLACK_BOT_TOKEN` | `xoxb-…` bot key |
| 💬 | `SLACK_USER_TOKENS` | `xoxp-…,xoxp-…`, one per member (needed for DMs) |
| 💬 | `SLACK_APP_TOKEN` | `xapp-…`, enables instant Socket Mode events |
| 🎫📘 | `JIRA_BASE_URL` · `JIRA_EMAIL` · `JIRA_API_TOKEN` | one Atlassian admin key, used for Jira **and** Confluence |
| 🎫 | `JIRA_JQL` | which issues, e.g. `project = KAN ORDER BY updated DESC` |
| 📘 | `CONFLUENCE_ENABLED` · `CONFLUENCE_SPACE_KEYS` | `true` · e.g. `Agents` (empty = all spaces) |
| ✅ | `CLICKUP_TEAM_ID` · `CLICKUP_API_TOKEN` | workspace id · admin `pk_…` key |
| ✅ | `CLICKUP_USER_TOKEN_<NAME>` | one `pk_…` per member (needed for chat DMs) |
| 🎬 | `JIRA_USER_TOKEN_<NAME>` | `email:token`, used only by the demo to post as other people |
| 🧠 | `OLLAMA_URL` · `AGENT1_MODEL` · `AGENT2_MODEL` · `AGENT3_MODEL` | defaults `http://127.0.0.1:11434` · `qwen2.5:3b` · `qwen2.5:7b` · `qwen2.5:7b` |
| 🧠 | `SKILLS_PIPELINE_ENABLED` · `SKILLS_PIPELINE_INTERVAL_SECONDS` · `IMPLICIT_MIN_SCORE` | run agents from the dashboard (`true`) · every 900 s · keep implicit evidence ≥ 7 |
| 👥 | `HRMS_FILE` | employee master file (default `data/hrms_employees.csv`) |
| 📊 | `FILTER_EMAILS` | Person filter list when no HRMS file is loaded |

<details><summary><b>Optional tuning</b></summary>

| Variable | Default | Meaning |
|---|---|---|
| `LOOKBACK_DAYS` | 7 | Slack re-scan window for late replies and edits |
| `SLACK_AUTO_SYNC` | true | `false` turns off Slack collection in the dashboard |
| `SLACK_SYNC_INTERVAL_SECONDS` | 60 | fallback polling when `SLACK_APP_TOKEN` is missing |
| `JIRA_SYNC_INTERVAL_SECONDS` · `CONFLUENCE_SYNC_INTERVAL_SECONDS` · `CLICKUP_SYNC_INTERVAL_SECONDS` | 60 | polling interval (minimum 30) |
| `CLICKUP_CHAT_LOOKBACK_HOURS` | 24 | re-read window for late chat replies |
| `DASHBOARD_HOST` · `DASHBOARD_PORT` | 127.0.0.1 · 8000 | dashboard address |
</details>

---

## 🛠️ Commands

| Do this | Command |
|---|---|
| 📊 Dashboard (sync + live view) | `.\.venv\Scripts\python.exe dashboard.py` |
| 🔍 Check what Slack tokens can read | `.\.venv\Scripts\python.exe ingest.py --check` |
| 💬 Sync Slack once | `.\.venv\Scripts\python.exe ingest.py` |
| 🎫 Sync Jira once | `.\.venv\Scripts\python.exe -m integrations.sync jira` |
| 📘 Sync Confluence once | `.\.venv\Scripts\python.exe -m integrations.sync confluence` |
| ✅ Sync ClickUp once | `.\.venv\Scripts\python.exe -m integrations.sync clickup` |
| 🪪 Resolve identities + rebuild canonical records | `.\.venv\Scripts\python.exe -m integrations.identity` |
| 🧠 Run Agents 1–4 on new/changed threads | `.\.venv\Scripts\python.exe -m integrations.skills` |
| 🧪 Verify Slack coverage | `.\.venv\Scripts\python.exe verify.py` |

---

<a id="live-demo"></a>

## 🎬 Live demo

<img src="docs/images/live-demo.svg" alt="27-step incident story across Jira, Confluence and ClickUp, each step by a named person" width="100%">

A production-incident story in 27 steps. Each step is posted **as that person** with their own key, then appears on the dashboard within about 2 seconds.

| Do this | Command |
|---|---|
| ▶️ Presenter mode (Enter per step) | `.\.venv\Scripts\python.exe live_demo.py` |
| ⏩ Hands-free | `.\.venv\Scripts\python.exe live_demo.py --auto 10` |
| 🧹 Remove everything it created | `.\.venv\Scripts\python.exe live_demo.py --cleanup` |
| 🗂️ One-off sample data | `.\.venv\Scripts\python.exe simulate_demo.py` |

---

<a id="project-structure"></a>

## 📁 Project structure

```
slack-message-intelligence/
├── 📊 dashboard.py          live dashboard, filters, /how-it-works, sync workers, webhook receiver
├── 💬 ingest.py             Slack collector (bot + user tokens, permalinks)
├── 🧩 integrations/
│   ├── records.py           shared UnifiedRecord format
│   ├── jira.py              Jira connector
│   ├── confluence.py        Confluence connector
│   ├── clickup.py           ClickUp connector (tasks + chat)
│   ├── identity.py          Phase 1: EMP_ID resolution + canonical activity records
│   ├── skills.py            Phase 2: Agents 1–4 (local Ollama models) → skill graph
│   └── sync.py              stores records · CLI entry point
├── 🗄️ schema.sql            Slack tables
├── 🗄️ unified_schema.sql    unified tables + mapping views
├── 🪪 canonical_schema.sql  employees, person_accounts, activity_records, activity_threads
├── 🧠 skills_schema.sql     skills, skill_evidence, employee_skills, agent runs, expert views
├── 🧠 skills.html           skill graph page (served at /skills)
├── 👥 data/hrms_employees.csv  mock HRMS: EMP_ID, role, declared + resume skills
├── 🎬 live_demo.py          27-step live demo (+ --cleanup)
├── 🎬 simulate_demo.py      one-off sample activity
├── ✅ verify.py             Slack coverage report
├── 📖 how_it_works.html     illustrated explainer (served at /how-it-works)
└── 🖼️ docs/                 images/ + make_diagrams.py · make_skill_graph.py (redraws from live data)
```

---

## 🧱 Scope

| ✅ Built | 🚫 Not collected | ⏭️ Next |
|---|---|---|
| all communication from 4 tools · EMP_ID identity · canonical activity records · Agents 1–4 (local) · skill graph · expert finder | 📞 calls (no transcript API) · 😀 reactions · 📎 attachments · 🗑️ deleted messages | 📧 Outlook · 🟪 Teams (needs Microsoft 365 work tenant) · 🕸️ Neo4j export · 📈 skill growth over time |

<details><summary><b>⚡ Optional: instant Jira/ClickUp updates with webhooks</b></summary>

With `WEBHOOK_TOKEN` set, `dashboard.py` also listens on `127.0.0.1:8001` for `POST /webhooks/jira/<WEBHOOK_TOKEN>` and `POST /webhooks/clickup/<WEBHOOK_TOKEN>`. The dashboard (port 8000) stays private. Expose port 8001 with a tunnel:

```powershell
ngrok http 8001 --url=https://<your-static-domain>.ngrok-free.app
```

- **Jira:** Settings › System › WebHooks: URL `https://<domain>/webhooks/jira/<WEBHOOK_TOKEN>`, Issue and Comment created/updated/deleted, generate a secret into `JIRA_WEBHOOK_SECRET`.
- **ClickUp:** `.\.venv\Scripts\python.exe -m integrations.sync clickup-webhook --url https://<domain>/webhooks/clickup/<WEBHOOK_TOKEN>`, then put the printed secret in `CLICKUP_WEBHOOK_SECRET`.

ClickUp has no task-history API, so *who* changed a status or assignee is only known through webhooks.
</details>

<details><summary><b>📝 Notes</b></summary>

- Re-runs are safe: items are upserted, never duplicated, and every source keeps its native IDs and links.
- ClickUp free plans allow 100 API calls per minute per token; the connector waits and retries.
- The ClickUp Chat API is marked experimental by ClickUp and may change.
- Jira search requires bounded JQL; the default is `updated >= -365d ORDER BY updated DESC`.
</details>
