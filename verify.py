"""Coverage report: proves what was collected and lists every known gap.

Usage:
    python verify.py            # database checks + live comparison against Slack's conversation lists
    python verify.py --recount  # also re-count every conversation's history in Slack and compare to the DB

Prints counts and ids only, never message text.
"""

import argparse
import os
from itertools import combinations

import psycopg
from dotenv import load_dotenv

from ingest import EXCLUDED_USERS, USER_NAMES, assign_readers, conversation_type, describe, load_readers

load_dotenv()


def section(title):
    print(f"\n=== {title} ===")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recount", action="store_true", help="re-page Slack history and compare counts")
    args = parser.parse_args()

    readers = load_readers()
    for page in readers[0].client.users_list(limit=200):
        USER_NAMES.update({u["id"]: u["name"] for u in page["members"]})
    authorized = {r.user_id for r in readers if r.label != "bot"}
    problems = 0

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        # 1. Every conversation Slack shows us right now must be in the DB with status ok.
        section("1. Live Slack conversations vs database")
        plan = assign_readers(readers)
        cur.execute("SELECT conversation_id, status, detail FROM ingestion_checkpoints")
        checkpoints = {cid: (status, detail) for cid, status, detail in cur.fetchall()}
        for cid, (ch, _, _) in sorted(plan.items(), key=lambda kv: conversation_type(kv[1][0])):
            status, detail = checkpoints.get(cid, ("NOT COLLECTED (run ingest.py)", ""))
            flag = "OK  " if status == "ok" else "GAP "
            problems += status != "ok"
            print(f"  {flag} {describe(ch):45} {status} {detail or ''}")

        # 2. Per-conversation totals.
        section("2. Messages per conversation")
        cur.execute(
            """
            SELECT c.conversation_id, c.conversation_type, COALESCE(c.name, u.username, c.dm_user_id),
                   COALESCE(v.username, k.collected_via),
                   COUNT(m.message_ts),
                   COUNT(m.message_ts) FILTER (WHERE m.is_thread_reply),
                   MIN(m.sent_at), MAX(m.sent_at)
            FROM slack_conversations c
            LEFT JOIN slack_users u ON u.user_id = c.dm_user_id
            LEFT JOIN ingestion_checkpoints k ON k.conversation_id = c.conversation_id
            LEFT JOIN slack_users v ON v.user_id = k.collected_via
            LEFT JOIN slack_messages m ON m.conversation_id = c.conversation_id
            GROUP BY 1, 2, 3, 4 ORDER BY 2, 3, 4
            """
        )
        print(f"  {'type':16} {'conversation':20} {'via':12} {'msgs':>6} {'replies':>7}  first .. last")
        for _, ctype, name, via, total, replies, first, last in cur.fetchall():
            span = f"{first:%Y-%m-%d} .. {last:%Y-%m-%d}" if first else "(empty)"
            print(f"  {ctype:16} {str(name):20} {str(via):12} {total:>6} {replies:>7}  {span}")

        # 3. Threads: Slack's reply_count on each parent must match the replies we stored.
        section("3. Thread completeness")
        cur.execute(
            """
            SELECT p.conversation_id, p.message_ts, p.reply_count, COUNT(r.message_ts)
            FROM slack_messages p
            LEFT JOIN slack_messages r
                   ON r.conversation_id = p.conversation_id
                  AND r.thread_ts = p.message_ts AND r.is_thread_reply
            WHERE p.reply_count > 0 AND NOT p.is_thread_reply
            GROUP BY 1, 2, 3
            HAVING COUNT(r.message_ts) <> p.reply_count
            """
        )
        bad_threads = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM slack_messages WHERE reply_count > 0 AND NOT is_thread_reply")
        print(f"  threads: {cur.fetchone()[0]}, mismatched: {len(bad_threads)}")
        for cid, ts, expected, got in bad_threads:
            print(f"  GAP  thread {cid}/{ts}: Slack says {expected} replies, stored {got}")
        problems += len(bad_threads)

        # 4. Every message must map to a known sender.
        section("4. Sender mapping")
        cur.execute(
            """
            SELECT COUNT(*) FILTER (WHERE m.user_id IS NULL AND m.bot_id IS NULL),
                   COUNT(*) FILTER (WHERE m.user_id IS NOT NULL AND u.user_id IS NULL)
            FROM slack_messages m LEFT JOIN slack_users u ON u.user_id = m.user_id
            """
        )
        no_sender, unknown_sender = cur.fetchone()
        print(f"  messages with no sender: {no_sender}, sender not in slack_users: {unknown_sender}")
        problems += bool(unknown_sender)

        # 5. Mentions.
        section("5. Mentions / tags")
        cur.execute("SELECT mention_type, COUNT(*) FROM slack_mentions GROUP BY 1 ORDER BY 1")
        for kind, count in cur.fetchall():
            print(f"  {kind:10} {count}")

        # 6. DM coverage between every pair of human members.
        section("6. 1:1 DM coverage between members")
        cur.execute(
            "SELECT user_id, username FROM slack_users "
            "WHERE NOT is_bot AND NOT is_deleted AND user_id <> 'USLACKBOT' ORDER BY username"
        )
        humans = [h for h in cur.fetchall() if h[0] not in EXCLUDED_USERS]
        print(f"  members who authorized a user token: "
              f"{', '.join(USER_NAMES.get(u, u) for u in authorized) or 'none'}")
        cur.execute(
            "SELECT k.collected_via, c.dm_user_id FROM slack_conversations c "
            "JOIN ingestion_checkpoints k USING (conversation_id) "
            "WHERE c.conversation_type = 'im' AND k.status = 'ok' AND k.collected_via <> 'bot'"
        )
        covered = {frozenset(pair) for pair in cur.fetchall()}
        for (a, an), (b, bn) in combinations(humans, 2):
            ok = frozenset((a, b)) in covered
            reason = "" if ok else f"  <- needs {an} or {bn} to authorize"
            print(f"  {'OK  ' if ok else 'GAP '} {an} <-> {bn}{reason}")
            problems += not ok

        # 7. Optional: recount Slack history and compare.
        if args.recount:
            section("7. Recount: Slack history vs database (top-level messages)")
            for cid, (ch, reader, _) in plan.items():
                if reader is None:
                    continue
                slack_count = sum(
                    len(p["messages"])
                    for p in reader.client.conversations_history(channel=cid, oldest="0", limit=200)
                )
                cur.execute(
                    "SELECT COUNT(*) FROM slack_messages WHERE conversation_id = %s "
                    "AND (NOT is_thread_reply OR subtype = 'thread_broadcast')",
                    (cid,),
                )
                db_count = cur.fetchone()[0]
                ok = slack_count == db_count
                problems += not ok
                print(f"  {'OK  ' if ok else 'GAP '} {describe(ch):45} slack={slack_count} db={db_count}")

    section("Result")
    print(f"  {problems} gap(s) found" if problems else "  No gaps in the conversations this app can see.")
    print("  Not detectable by API: DMs, group DMs and private channels that include no authorized token holder.")


if __name__ == "__main__":
    main()
