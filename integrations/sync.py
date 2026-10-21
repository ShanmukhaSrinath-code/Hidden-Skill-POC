"""Import Jira or ClickUp records into the shared PostgreSQL tables.

The dashboard calls sync_jira() on an interval; this module can also be run directly.
"""

import argparse
import json
import os
from pathlib import Path

from itertools import chain

import psycopg
from dotenv import load_dotenv

from integrations.clickup import ClickUpConnector
from integrations.confluence import ConfluenceConnector
from integrations.jira import JiraConnector

load_dotenv()

SCHEMA = Path(__file__).resolve().parents[1] / "unified_schema.sql"
# The enhanced Jira search API rejects unbounded JQL, so the default is restricted by date.
DEFAULT_JQL = "updated >= -365d ORDER BY updated DESC"


def save_record(cur, record):
    cur.execute(
        """
        INSERT INTO unified_records
            (platform, account_id, object_type, object_id, kind,
             parent_platform, parent_account_id, parent_type, parent_id,
             actor_id, title, body, source_url, occurred_at, attributes, raw, synced_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,NOW())
        ON CONFLICT (platform, account_id, object_type, object_id) DO UPDATE SET
            kind=EXCLUDED.kind, parent_platform=EXCLUDED.parent_platform,
            parent_account_id=EXCLUDED.parent_account_id, parent_type=EXCLUDED.parent_type,
            parent_id=EXCLUDED.parent_id, actor_id=EXCLUDED.actor_id, title=EXCLUDED.title,
            body=EXCLUDED.body, source_url=EXCLUDED.source_url, occurred_at=EXCLUDED.occurred_at,
            attributes=EXCLUDED.attributes, raw=EXCLUDED.raw, synced_at=NOW()
        """,
        (record.source.platform, record.source.account_id, record.source.object_type,
         record.source.object_id, record.kind,
         record.parent.platform if record.parent else None,
         record.parent.account_id if record.parent else None,
         record.parent.object_type if record.parent else None,
         record.parent.object_id if record.parent else None,
         record.actor_id, record.title, record.body, record.source.url, record.occurred_at,
         json.dumps(record.attributes), json.dumps(record.raw)),
    )
    key = (record.source.platform, record.source.account_id,
           record.source.object_type, record.source.object_id)
    for table, column, values in (("unified_participants", "user_id, role", record.participants),
                                  ("unified_mentions", "target_id", [(m,) for m in record.mentions]),
                                  ("unified_reactions", "reaction, user_id", record.reactions)):
        cur.execute(f"DELETE FROM {table} WHERE platform=%s AND account_id=%s "
                    "AND object_type=%s AND object_id=%s", key)
        if values:
            placeholders = ",".join(["%s"] * (4 + len(values[0])))
            cur.executemany(f"INSERT INTO {table} (platform, account_id, object_type, object_id, {column}) "
                            f"VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                            [key + tuple(v) for v in values])


def save_checkpoint(cur, platform, account_id, scope_id, status, detail=None, cursor=None):
    cur.execute(
        """
        INSERT INTO unified_sync_checkpoints (platform, account_id, scope_id, cursor, status, detail, last_run)
        VALUES (%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (platform, account_id, scope_id) DO UPDATE SET
            cursor=COALESCE(EXCLUDED.cursor, unified_sync_checkpoints.cursor),
            status=EXCLUDED.status, detail=EXCLUDED.detail, last_run=NOW()
        """,
        (platform, account_id, scope_id, cursor, status, detail),
    )


_SCHEMA_APPLIED = set()


def connect(database_url=None):
    """Open a connection, applying unified_schema.sql once per database per process."""
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL in .env")
    conn = psycopg.connect(database_url)
    if database_url not in _SCHEMA_APPLIED:
        with conn.cursor() as cur:
            cur.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
        _SCHEMA_APPLIED.add(database_url)
    return conn


def store(platform, account_id, scope_id, records, database_url=None, after=None):
    """Upsert every record in one transaction and record the outcome as a sync checkpoint.

    `after(cur)` runs inside the same transaction once every record is saved.
    """
    with connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO connected_accounts (platform, account_id) VALUES (%s,%s) "
                    "ON CONFLICT DO NOTHING", (platform, account_id))
        conn.commit()
        count = 0
        try:
            for record in records:
                save_record(cur, record)
                count += 1
            if after:
                after(cur)
        except Exception as exc:
            conn.rollback()
            save_checkpoint(cur, platform, account_id, scope_id, "error", str(exc)[:500])
            conn.commit()
            raise
        save_checkpoint(cur, platform, account_id, scope_id, "ok", f"{count} records")
        conn.commit()
    return count


def delete_records(platform, account_id, object_type, object_id, database_url=None):
    """Remove a record, its children (comments, changes) and their participants/mentions/reactions."""
    with connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT object_type, object_id FROM unified_records WHERE platform=%s AND account_id=%s "
                    "AND ((object_type=%s AND object_id=%s) OR (parent_type=%s AND parent_id=%s))",
                    (platform, account_id, object_type, object_id, object_type, object_id))
        keys = set(cur.fetchall()) | {(object_type, object_id)}
        for table in ("unified_participants", "unified_mentions", "unified_reactions", "unified_records"):
            cur.executemany(f"DELETE FROM {table} WHERE platform=%s AND account_id=%s "
                            "AND object_type=%s AND object_id=%s",
                            [(platform, account_id, t, i) for t, i in keys])
        conn.commit()
    return len(keys)


# ---------------------------------------------------------------- Jira


def sync_jira(jql=None, database_url=None):
    jql = jql or os.getenv("JIRA_JQL") or DEFAULT_JQL
    connector = JiraConnector()
    records = chain(connector.list_users(), connector.search_issues(jql))
    return store("jira", connector.account_id, jql, records, database_url)


def apply_jira_webhook(payload, database_url=None):
    """Handle a Jira webhook by re-reading the affected issue (or deleting it) from the API."""
    event = payload.get("webhookEvent", "")
    key = (payload.get("issue") or {}).get("key")
    comment_id = (payload.get("comment") or {}).get("id")
    connector = JiraConnector()
    if event == "jira:issue_deleted" and key:
        delete_records("jira", connector.account_id, "issue", key, database_url)
        return f"{event} {key}"
    if event == "comment_deleted" and comment_id:
        delete_records("jira", connector.account_id, "comment", str(comment_id), database_url)
    if key:
        store("jira", connector.account_id, "webhook", connector.search_issues(f'key = "{key}"'), database_url)
    return f"{event} {key or ''}".strip()


# ---------------------------------------------------------------- Confluence


def sync_confluence(database_url=None):
    """Sync site users and every page/blog post with its comments, edits and inline tasks."""
    connector = ConfluenceConnector()
    records = chain(connector.list_users(), connector.list_content())
    scope = ",".join(sorted(connector.space_keys)) or "all spaces"
    return store("confluence", connector.account_id, scope, records, database_url)


# ---------------------------------------------------------------- ClickUp


def sync_clickup(full=True, database_url=None):
    """Sync members, tasks/comments and chat. Incremental runs skip tasks and channels with no new activity."""
    connector = ClickUpConnector()
    account_id = connector.account_id
    known, chat_state = None, {}
    if not full:
        with connect(database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT object_id, attributes->>'date_updated' FROM unified_records "
                        "WHERE platform='clickup' AND account_id=%s AND object_type='task'", (account_id,))
            known = dict(cur.fetchall())
            cur.execute("SELECT scope_id, cursor FROM unified_sync_checkpoints WHERE platform='clickup' "
                        "AND account_id=%s AND scope_id LIKE 'chat:%%' AND cursor IS NOT NULL", (account_id,))
            chat_state = {scope[5:]: json.loads(cursor) for scope, cursor in cur.fetchall()}

    def save_chat_state(cur):
        for channel_id, value in chat_state.items():
            save_checkpoint(cur, "clickup", account_id, f"chat:{channel_id}", "ok", None, json.dumps(value))

    records = chain(connector.list_users(), connector.list_tasks(known), connector.chat_records(chat_state))
    return store("clickup", account_id, "workspace", records, database_url, after=save_chat_state)


def apply_clickup_webhook(payload, database_url=None):
    """Store who changed what from the webhook's history items, then re-read the task from the API."""
    event = payload.get("event", "")
    task_id = payload.get("task_id")
    if not task_id:
        return f"{event} (no task; ignored)"
    connector = ClickUpConnector()
    if event == "taskDeleted":
        delete_records("clickup", connector.account_id, "task", str(task_id), database_url)
        return f"{event} {task_id}"
    records = chain(connector.history_records(task_id, payload.get("history_items") or []),
                    connector.get_task(task_id))
    store("clickup", connector.account_id, "webhook", records, database_url)
    return f"{event} {task_id}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("jira", "confluence", "clickup", "clickup-webhook"))
    parser.add_argument("--jql", help=f"Jira query; default $JIRA_JQL or '{DEFAULT_JQL}'")
    parser.add_argument("--incremental", action="store_true",
                        help="ClickUp: skip tasks and chat channels with no new activity")
    parser.add_argument("--url", help="clickup-webhook: public URL ending in /webhooks/clickup/<WEBHOOK_TOKEN>")
    args = parser.parse_args()

    if args.platform == "jira":
        count = sync_jira(args.jql)
    elif args.platform == "confluence":
        count = sync_confluence()
    elif args.platform == "clickup":
        count = sync_clickup(full=not args.incremental)
    else:
        if not args.url:
            parser.error("clickup-webhook requires --url")
        hook, created = ClickUpConnector().ensure_webhook(args.url)
        print(f"{'Created' if created else 'Already registered'}: webhook {hook.get('id')} -> {hook.get('endpoint')}")
        if hook.get("secret"):
            print(f"Add to .env:  CLICKUP_WEBHOOK_SECRET={hook['secret']}")
        return
    print(f"{args.platform}: stored {count} normalized records")


if __name__ == "__main__":
    main()
