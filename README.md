# Agent Messaging Intelligence: message ingestion

This collects Slack users, user groups, channels, private channels, group DMs and DMs. It also collects messages, thread replies, mentions (@user, @group, @here/@channel, #channel) and reactions, and stores them all in PostgreSQL.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install slack_sdk "psycopg[binary]" python-dotenv
```

Write `.env` (it is gitignored):

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_USER_TOKENS=xoxp-...,xoxp-...   # optional, one per member who authorizes DM/group-DM collection
SLACK_APP_TOKEN=xapp-...              # optional; enables Socket Mode event ingestion
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/slack_intelligence
LOOKBACK_DAYS=7                        # optional re-scan window for new thread replies, edits and reactions
```

Start PostgreSQL. With Docker Desktop running:

```powershell
docker run -d --name slack-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=slack_intelligence -p 5432:5432 -v slack-pg-data:/var/lib/postgresql/data postgres:16
```

## Run

```powershell
.\.venv\Scripts\python.exe ingest.py --check   # shows which conversations each token can read
.\.venv\Scripts\python.exe ingest.py           # creates tables (schema.sql), then does a full or incremental sync
```

The script can be re-run safely. Messages are upserted, and each conversation resumes from its checkpoint minus `LOOKBACK_DAYS`. Conversations that can't be read are recorded in `ingestion_checkpoints` along with the reason.

## Live mapping dashboard

Start the dashboard in a second terminal after PostgreSQL is running:

```powershell
.\.venv\Scripts\python.exe dashboard.py
```

Open <http://127.0.0.1:8000>. The dashboard polls PostgreSQL every two seconds. With `SLACK_APP_TOKEN` configured and Slack message event subscriptions enabled, it performs an initial backfill and then stores incoming Socket Mode message and reaction events. Without the app token, it falls back to periodic ingestion every 60 seconds. Both modes use the configured bot and user tokens and only collect conversations those tokens can access. Set `SLACK_SYNC_INTERVAL_SECONDS` to change the fallback interval (minimum 30 seconds), or set `SLACK_AUTO_SYNC=false` to disable Slack collection. Set `DASHBOARD_HOST` or `DASHBOARD_PORT` to change the local bind address or port.

## Slack access model

| Source | Read by | Scopes needed |
| --- | --- | --- |
| Public channels | Bot | `channels:read`, `channels:history`, `channels:join` (lets the bot join automatically) |
| Private channels | Bot (once invited) or member's user token | `groups:read`, `groups:history` |
| Group DMs | Member's user token | `mpim:read`, `mpim:history` |
| 1:1 DMs between people | Member's user token only | `im:read`, `im:history` |
| Users / user groups | Bot | `users:read`, `users:read.email`, `usergroups:read` |
| Reactions | Bot / user | included in history payloads (`reactions:read` for the reactions API) |

A bot token can never read DMs or group DMs between people. To cover those, each member has to authorize the app with user scopes, which gives you one `xoxp-` token per member.

## Separate Jira and ClickUp connectors

Platform code is separated under `integrations/`: `jira.py` handles Jira Cloud, `clickup.py` handles ClickUp, and `records.py` defines the shared record and source-link format. Existing Slack ingestion remains in `ingest.py` and its Slack-specific tables remain unchanged. Cross-platform records and explicit mappings use `unified_records` and `unified_record_links` in `unified_schema.sql`.

Install the existing dependencies, then add credentials to `.env`:

```
JIRA_BASE_URL=https://your-company.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...
CLICKUP_API_TOKEN=...
CLICKUP_TEAM_ID=...
```

### Jira

One admin API token is enough: Jira has no private messages, so every comment, mention and change lives on an issue the admin can see. Each sync collects all site users, the issues matched by JQL, every comment, watchers and the change history. The issue acts as the conversation: its reporter, creator, assignee, watchers and commenters are its participants, so they are the recipients of every comment and change on it. `@mentions` in descriptions and comments are mapped to users.

Run a one-off import (JQL defaults to `$JIRA_JQL`, else `updated >= -365d ORDER BY updated DESC`):

```powershell
.\.venv\Scripts\python.exe -m integrations.sync jira
.\.venv\Scripts\python.exe -m integrations.sync jira --jql "project = DEMO ORDER BY updated DESC"
```

When `JIRA_BASE_URL`, `JIRA_EMAIL` and `JIRA_API_TOKEN` are set, `dashboard.py` also polls Jira every `JIRA_SYNC_INTERVAL_SECONDS` (default 60, minimum 30) and shows Jira issues, comments and changes in the same stream as Slack. Query the mapping directly with the `unified_message_map` (sender, recipients, mentions per item) and `unified_person_messages` (sent / received / mentioned per person) views. Users' emails are only returned when their Atlassian profile sets email visibility to "Anyone".

### Confluence

Confluence uses the same Atlassian site, token and account ids as Jira, so no extra credentials are needed. A page or blog post is the conversation: its author, editors, commenters, inline-task assignees and people @mentioned on it are its participants. Page comments, inline comments (with the highlighted text), replies, edits (one event per version) and inline tasks are mapped.

```
CONFLUENCE_ENABLED=true           # dashboard.py polls Confluence every CONFLUENCE_SYNC_INTERVAL_SECONDS (default 60)
CONFLUENCE_SPACE_KEYS=Agents      # optional; empty = every readable space, including personal spaces
```

```powershell
.\.venv\Scripts\python.exe -m integrations.sync confluence
```

### ClickUp

Tasks and task comments are shared across the workspace, so `CLICKUP_API_TOKEN` (one workspace member's personal `pk_` token) reads them all. ClickUp Chat channels, DMs and group DMs are only visible to their members, so, like Slack user tokens, add each member's personal token to `CLICKUP_USER_TOKENS` to collect their DMs. Each chat channel is read once, by the first token that can see it.

```
CLICKUP_API_TOKEN=pk_...                 # workspace admin
CLICKUP_TEAM_ID=...                      # workspace id: the number after app.clickup.com/ in the URL
CLICKUP_USER_TOKENS=pk_...,pk_...        # optional: other members, for their DMs and group DMs
```

A task is the conversation: its creator, assignees, watchers and commenters are its participants. Comments, threaded replies, `@mentions`, assigned comments and reactions are mapped. Chat messages, replies, tagged users and reactions are mapped, with channel members as recipients. The ClickUp Chat API is marked experimental by ClickUp and may change.

```powershell
.\.venv\Scripts\python.exe -m integrations.sync clickup                 # full read
.\.venv\Scripts\python.exe -m integrations.sync clickup --incremental   # skip unchanged tasks and channels
```

`dashboard.py` polls ClickUp every `CLICKUP_SYNC_INTERVAL_SECONDS` (default 60) incrementally, with a full read every 10th run. ClickUp's free plans allow 100 API requests per minute per token; the connector waits and retries when rate limited. Chat messages are re-read for `CLICKUP_CHAT_LOOKBACK_HOURS` (default 24) behind the newest stored message to pick up late replies and reactions.

ClickUp has no task-history API, so status, assignee, priority and due-date changes, with who made them, are only recorded from webhooks.

### Instant updates with webhooks

When `WEBHOOK_TOKEN` is set, `dashboard.py` starts a separate webhook receiver on `127.0.0.1:WEBHOOK_PORT` (default 8001). It only accepts `POST /webhooks/jira/<WEBHOOK_TOKEN>` and `POST /webhooks/clickup/<WEBHOOK_TOKEN>` and re-reads the affected issue or task from the API (or deletes it). The dashboard on port 8000 is not exposed. Polling keeps running as a fallback.

Jira and ClickUp need a public HTTPS URL, so expose port 8001 with a tunnel, e.g. ngrok with a free static domain:

```powershell
ngrok http 8001 --url=https://<your-static-domain>.ngrok-free.app
```

- **Jira:** Settings > System > WebHooks > Create a WebHook. URL `https://<domain>/webhooks/jira/<WEBHOOK_TOKEN>`; events Issue created/updated/deleted and Comment created/updated/deleted; click "Generate secret" and put it in `JIRA_WEBHOOK_SECRET` (verified against `X-Hub-Signature`).
- **ClickUp:** `.\.venv\Scripts\python.exe -m integrations.sync clickup-webhook --url https://<domain>/webhooks/clickup/<WEBHOOK_TOKEN>` registers a workspace webhook for all events and prints `CLICKUP_WEBHOOK_SECRET` (verified against `X-Signature`).

Restart `dashboard.py` after changing `.env`.

These imports preserve native platform IDs and source URLs so records can be linked without conflating Jira and ClickUp IDs.
