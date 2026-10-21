"""Collect Slack users, conversations, messages, threads, mentions and reactions into PostgreSQL.

Usage:
    python ingest.py --check     # report what each token can access (no database needed)
    python ingest.py             # full / incremental ingestion

Tokens (in .env):
    SLACK_BOT_TOKEN   xoxb-... reads public channels (and private channels / group DMs it is invited to)
    SLACK_USER_TOKENS comma-separated xoxp-... tokens; each reads that member's own DMs, group DMs
                      and private channels. Slack never lets a bot token read DMs between people.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

load_dotenv()

TYPE_SCOPES = {
    # conversation type: (list scope, history scope)
    "public_channel": ("channels:read", "channels:history"),
    "private_channel": ("groups:read", "groups:history"),
    "mpim": ("mpim:read", "mpim:history"),
    "im": ("im:read", "im:history"),
}

# Members left out of collection entirely: their profile, DMs with them, their messages, mentions and reactions.
EXCLUDED_USERS = {u.strip() for u in os.getenv("EXCLUDE_USERS", "").split(",") if u.strip()}

# Re-scan this window before the last checkpoint so new thread replies, edits and reactions are picked up.
LOOKBACK = timedelta(days=int(os.getenv("LOOKBACK_DAYS", "7")))

MENTION_PATTERNS = [
    ("user", re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")),
    ("usergroup", re.compile(r"<!subteam\^([A-Z0-9]+)(?:\|[^>]*)?>")),
    ("broadcast", re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")),
    ("channel", re.compile(r"<#([CG][A-Z0-9]+)(?:\|[^>]*)?>")),
    ("email", re.compile(r"<mailto:([^|>\s]+)(?:\|[^>]*)?>")),
]

# Subtypes written by people. System events (channel_join, channel_topic, ...) embed <@U...> but are not mentions.
AUTHORED_SUBTYPES = {None, "thread_broadcast", "file_share", "me_message", "bot_message"}


@dataclass
class Reader:
    label: str  # "bot" or the authorizing user's id
    user_id: str
    client: WebClient
    scopes: set = field(default_factory=set)

    def can(self, scope):
        return scope in self.scopes


WORKSPACE_URL = ""  # e.g. https://acme.slack.com/, from auth.test; used to build message permalinks


def permalink(cid, ts, thread_ts=None):
    if not WORKSPACE_URL:
        return None
    link = f"{WORKSPACE_URL.rstrip('/')}/archives/{cid}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={cid}"
    return link


def make_reader(token, is_bot):
    client = WebClient(token=token)
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=10))
    auth = client.auth_test()
    scopes = set((auth.headers.get("x-oauth-scopes") or "").split(","))
    label = "bot" if is_bot else auth["user_id"]
    return Reader(label, auth["user_id"], client, scopes), auth


def load_readers():
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not bot_token:
        sys.exit("Set SLACK_BOT_TOKEN in .env")
    readers = []
    bot, auth = make_reader(bot_token, is_bot=True)
    global WORKSPACE_URL
    WORKSPACE_URL = auth.get("url") or ""
    print(f"Workspace: {auth['team']} ({auth['team_id']})  bot user: {auth['user_id']}")
    readers.append(bot)
    for token in filter(None, (t.strip() for t in os.getenv("SLACK_USER_TOKENS", "").split(","))):
        try:
            reader, auth = make_reader(token, is_bot=False)
            print(f"User token: {auth['user']} ({auth['user_id']})")
            readers.append(reader)
        except SlackApiError as e:
            print(f"Skipping a user token: {e.response['error']}")
    for r in readers:
        print(f"  [{r.label}] scopes: {', '.join(sorted(r.scopes)) or '(none)'}")
    return readers


def ts_to_dt(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def conversation_type(ch):
    if ch.get("is_im"):
        return "im"
    if ch.get("is_mpim"):
        return "mpim"
    if ch.get("is_private"):
        return "private_channel"
    return "public_channel"


def extract_mentions(text):
    found = set()
    for kind, pattern in MENTION_PATTERNS:
        for target in pattern.findall(text or ""):
            found.add((kind, target.lower() if kind == "email" else target))
    return found


# ---------------------------------------------------------------- Slack reads


def list_conversations(reader):
    """Every conversation this token can list, keyed by id."""
    result = {}
    for ctype, (read_scope, _) in TYPE_SCOPES.items():
        if not reader.can(read_scope):
            continue
        for page in reader.client.conversations_list(types=ctype, exclude_archived=False, limit=200):
            for ch in page["channels"]:
                result[ch["id"]] = ch
    return result


def assign_readers(readers):
    """Pick one token per conversation: the bot for channels it can read, otherwise a member's user token."""
    plan = {}  # conv_id -> (channel dict, reader or None, reason)
    for reader in readers:
        for cid, ch in list_conversations(reader).items():
            if ch.get("user") in EXCLUDED_USERS:
                continue
            ctype = conversation_type(ch)
            history_scope = TYPE_SCOPES[ctype][1]
            # Public channels need bot membership; user tokens can read any public channel.
            needs_join = reader.label == "bot" and ctype == "public_channel" and not ch.get("is_member")
            if not reader.can(history_scope):
                candidate, reason = None, f"missing_scope {history_scope}"
            elif needs_join and not reader.can("channels:join"):
                candidate, reason = None, "not_in_channel (invite the bot or add channels:join)"
            else:
                candidate, reason = reader, "ok"
            current = plan.get(cid)
            if current is None or (current[1] is None and candidate is not None):
                plan[cid] = (ch, candidate, reason)
    return plan


def ensure_member(reader, ch):
    if reader.label == "bot" and conversation_type(ch) == "public_channel" and not ch.get("is_member"):
        reader.client.conversations_join(channel=ch["id"])
        print(f"  joined #{ch.get('name')}")


def fetch_members(reader, ch):
    if conversation_type(ch) == "im":
        return {reader.user_id, ch["user"]}
    members = set()
    for page in reader.client.conversations_members(channel=ch["id"], limit=200):
        members.update(page["members"])
    return members


def fetch_messages(reader, cid, oldest):
    """Yield top-level messages and all thread replies since `oldest`."""
    for page in reader.client.conversations_history(channel=cid, oldest=oldest or "0", limit=200):
        for msg in page["messages"]:
            yield msg
            if msg.get("reply_count") and msg.get("thread_ts") == msg["ts"]:
                for rpage in reader.client.conversations_replies(channel=cid, ts=msg["ts"], limit=200):
                    for reply in rpage["messages"]:
                        if reply["ts"] != msg["ts"]:
                            yield reply


# ---------------------------------------------------------------- Database writes


def save_users(cur, client):
    count = 0
    for page in client.users_list(limit=200):
        for u in page["members"]:
            if u["id"] in EXCLUDED_USERS:
                continue
            p = u.get("profile") or {}
            cur.execute(
                """
                INSERT INTO slack_users (user_id, team_id, username, real_name, display_name, email,
                                         is_bot, is_deleted, profile, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    team_id = EXCLUDED.team_id, username = EXCLUDED.username,
                    real_name = EXCLUDED.real_name, display_name = EXCLUDED.display_name,
                    email = EXCLUDED.email, is_bot = EXCLUDED.is_bot, is_deleted = EXCLUDED.is_deleted,
                    profile = EXCLUDED.profile, updated_at = NOW()
                """,
                (u["id"], u.get("team_id"), u.get("name"), p.get("real_name"), p.get("display_name"),
                 p.get("email"), u.get("is_bot", False), u.get("deleted", False), json.dumps(p)),
            )
            count += 1
    # Slack's own system notices are sent as "USLACK", which users.list never returns.
    cur.execute(
        "INSERT INTO slack_users (user_id, username, real_name, is_bot) "
        "VALUES ('USLACK', 'slack-system', 'Slack system', TRUE) ON CONFLICT (user_id) DO NOTHING"
    )
    return count


def save_usergroups(cur, reader):
    if not reader.can("usergroups:read"):
        return 0
    groups = reader.client.usergroups_list(include_users=True, include_disabled=True)["usergroups"]
    for g in groups:
        cur.execute(
            """
            INSERT INTO slack_usergroups (usergroup_id, handle, name, member_ids, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, NOW())
            ON CONFLICT (usergroup_id) DO UPDATE SET
                handle = EXCLUDED.handle, name = EXCLUDED.name,
                member_ids = EXCLUDED.member_ids, updated_at = NOW()
            """,
            (g["id"], g.get("handle"), g.get("name"), json.dumps(g.get("users", []))),
        )
    return len(groups)


def save_conversation(cur, ch, members):
    created = ts_to_dt(ch["created"]) if ch.get("created") else None
    cur.execute(
        """
        INSERT INTO slack_conversations (conversation_id, conversation_type, name, dm_user_id,
                                         is_archived, topic, purpose, created_at, raw, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (conversation_id) DO UPDATE SET
            conversation_type = EXCLUDED.conversation_type, name = EXCLUDED.name,
            dm_user_id = EXCLUDED.dm_user_id, is_archived = EXCLUDED.is_archived,
            topic = EXCLUDED.topic, purpose = EXCLUDED.purpose, raw = EXCLUDED.raw, updated_at = NOW()
        """,
        (ch["id"], conversation_type(ch), ch.get("name"), ch.get("user"), ch.get("is_archived", False),
         (ch.get("topic") or {}).get("value"), (ch.get("purpose") or {}).get("value"), created,
         json.dumps(ch)),
    )
    if members is not None:
        cur.execute("DELETE FROM conversation_members WHERE conversation_id = %s", (ch["id"],))
        for uid in members - EXCLUDED_USERS:
            cur.execute(
                "INSERT INTO conversation_members (conversation_id, user_id) VALUES (%s, %s)",
                (ch["id"], uid),
            )


def save_message(cur, cid, msg, collected_via):
    ts = msg["ts"]
    thread_ts = msg.get("thread_ts")
    cur.execute(
        """
        INSERT INTO slack_messages (conversation_id, message_ts, user_id, bot_id, subtype, message_text,
                                    thread_ts, is_thread_reply, parent_user_id, reply_count,
                                    reply_users_count, latest_reply, edited_ts, has_files, sent_at,
                                    raw_message, collected_via, permalink, collected_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW())
        ON CONFLICT (conversation_id, message_ts) DO UPDATE SET
            user_id = EXCLUDED.user_id, subtype = EXCLUDED.subtype, message_text = EXCLUDED.message_text,
            reply_count = EXCLUDED.reply_count, reply_users_count = EXCLUDED.reply_users_count,
            latest_reply = EXCLUDED.latest_reply, edited_ts = EXCLUDED.edited_ts,
            has_files = EXCLUDED.has_files, raw_message = EXCLUDED.raw_message,
            collected_via = EXCLUDED.collected_via,
            permalink = COALESCE(EXCLUDED.permalink, slack_messages.permalink), collected_at = NOW()
        """,
        (cid, ts, msg.get("user"), msg.get("bot_id"), msg.get("subtype"), msg.get("text"),
         thread_ts, bool(thread_ts and thread_ts != ts), msg.get("parent_user_id"),
         msg.get("reply_count", 0), msg.get("reply_users_count", 0), msg.get("latest_reply"),
         (msg.get("edited") or {}).get("ts"), bool(msg.get("files")), ts_to_dt(ts),
         json.dumps(msg), collected_via, permalink(cid, ts, thread_ts)),
    )

    cur.execute("DELETE FROM slack_mentions WHERE conversation_id = %s AND message_ts = %s", (cid, ts))
    mentions = extract_mentions(msg.get("text")) if msg.get("subtype") in AUTHORED_SUBTYPES else set()
    for kind, target in mentions:
        if target in EXCLUDED_USERS:
            continue
        cur.execute(
            "INSERT INTO slack_mentions (conversation_id, message_ts, mention_type, target_id) "
            "VALUES (%s, %s, %s, %s)",
            (cid, ts, kind, target),
        )

    cur.execute("DELETE FROM slack_reactions WHERE conversation_id = %s AND message_ts = %s", (cid, ts))
    for reaction in msg.get("reactions", []):
        for uid in set(reaction.get("users", [])) - EXCLUDED_USERS:
            cur.execute(
                "INSERT INTO slack_reactions (conversation_id, message_ts, reaction, user_id) "
                "VALUES (%s, %s, %s, %s)",
                (cid, ts, reaction["name"], uid),
            )


def save_checkpoint(cur, cid, collected_via, last_ts, status, detail=None):
    cur.execute(
        """
        INSERT INTO ingestion_checkpoints (conversation_id, collected_via, last_ts, status, detail, last_run)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (conversation_id) DO UPDATE SET
            collected_via = EXCLUDED.collected_via,
            last_ts = COALESCE(EXCLUDED.last_ts, ingestion_checkpoints.last_ts),
            status = EXCLUDED.status, detail = EXCLUDED.detail, last_run = NOW()
        """,
        (cid, collected_via, last_ts, status, detail),
    )


def get_checkpoint(cur, cid):
    cur.execute("SELECT last_ts FROM ingestion_checkpoints WHERE conversation_id = %s", (cid,))
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    return str((ts_to_dt(row[0]) - LOOKBACK).timestamp())


# ---------------------------------------------------------------- Entry points


USER_NAMES = {"USLACK": "slack-system"}


def describe(ch):
    ctype = conversation_type(ch)
    if ctype == "im":
        return f"DM with {USER_NAMES.get(ch.get('user'), ch.get('user'))}"
    return f"{'#' if ctype == 'public_channel' else ''}{ch.get('name')} ({ctype})"


def check(readers):
    plan = assign_readers(readers)
    print(f"\nConversations visible: {len(plan)}")
    for cid, (ch, reader, reason) in sorted(plan.items(), key=lambda kv: conversation_type(kv[1][0])):
        via = reader.label if reader else "-"
        print(f"  {cid}  {describe(ch):45} via={via:12} {reason}")
    readable = sum(1 for _, r, _ in plan.values() if r)
    print(f"\nReadable now: {readable}/{len(plan)}")


def ingest(readers):
    import psycopg

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        sys.exit("Set DATABASE_URL in .env")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(Path(__file__).with_name("schema.sql").read_text())
            conn.commit()

            bot = readers[0]
            print(f"\nUsers synced: {save_users(cur, bot.client)}")
            print(f"User groups synced: {save_usergroups(cur, bot)}")
            conn.commit()

            plan = assign_readers(readers)
            print(f"Conversations visible: {len(plan)}\n")

            totals = {"messages": 0, "ok": 0, "skipped": 0}
            for cid, (ch, reader, reason) in plan.items():
                if reader is None:
                    save_conversation(cur, ch, None)
                    save_checkpoint(cur, cid, None, None, "skipped", reason)
                    conn.commit()
                    print(f"SKIP {describe(ch)}: {reason}")
                    totals["skipped"] += 1
                    continue
                try:
                    ensure_member(reader, ch)
                    save_conversation(cur, ch, fetch_members(reader, ch))
                    oldest = get_checkpoint(cur, cid)
                    count, last_ts = 0, None
                    for msg in fetch_messages(reader, cid, oldest):
                        if msg.get("user") in EXCLUDED_USERS:
                            continue
                        save_message(cur, cid, msg, reader.label)
                        count += 1
                        if not last_ts or float(msg["ts"]) > float(last_ts):
                            last_ts = msg["ts"]
                    save_checkpoint(cur, cid, reader.label, last_ts, "ok")
                    conn.commit()
                    print(f"READ {describe(ch)} via {reader.label}: {count} messages")
                    totals["messages"] += count
                    totals["ok"] += 1
                except SlackApiError as e:
                    conn.rollback()
                    error = e.response["error"]
                    save_conversation(cur, ch, None)
                    save_checkpoint(cur, cid, reader.label, None, error, str(e.response.get("needed") or ""))
                    conn.commit()
                    print(f"FAIL {describe(ch)} via {reader.label}: {error}")
                    totals["skipped"] += 1

    print(f"\nDone. Conversations read: {totals['ok']}, skipped: {totals['skipped']}, "
          f"messages stored/updated: {totals['messages']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report access only; no database writes")
    args = parser.parse_args()

    readers = load_readers()
    for page in readers[0].client.users_list(limit=200):
        USER_NAMES.update({u["id"]: u["name"] for u in page["members"]})
    if args.check:
        check(readers)
    else:
        ingest(readers)


if __name__ == "__main__":
    main()
