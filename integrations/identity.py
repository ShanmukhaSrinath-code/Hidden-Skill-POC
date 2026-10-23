"""Phase 1: resolve every platform account to a canonical EMP_ID and build canonical activity records.

Deterministic (no LLM):
1. load employees + declared skills from the HRMS file (HRMS_FILE, default data/hrms_employees.csv)
2. link every Slack / Jira / Confluence / ClickUp account to an employee by email
   (data/identity_overrides.csv can force a link: platform,platform_user_id,emp_id)
3. rebuild activity_records from all sources, with sender / recipients / mentions as EMP_IDs

    python -m integrations.identity            # run all three steps and print a summary
"""

import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = [ROOT / "schema.sql", ROOT / "unified_schema.sql", ROOT / "canonical_schema.sql"]


def _path(env, default):
    value = os.getenv(env, "").strip()
    return Path(value) if value else ROOT / default


def load_hrms(cur, path=None):
    """Upsert employees and replace their declared skills from the HRMS CSV."""
    path = path or _path("HRMS_FILE", "data/hrms_employees.csv")
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [r for r in csv.DictReader(handle) if (r.get("emp_id") or "").strip()]
    for row in rows:
        emp_id = row["emp_id"].strip()
        cur.execute(
            """
            INSERT INTO employees (emp_id, email, full_name, role, department, manager_emp_id, job_description, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (emp_id) DO UPDATE SET email = EXCLUDED.email, full_name = EXCLUDED.full_name,
                role = EXCLUDED.role, department = EXCLUDED.department, manager_emp_id = EXCLUDED.manager_emp_id,
                job_description = EXCLUDED.job_description, updated_at = NOW()
            """,
            (emp_id, row["email"].strip().lower(), row["full_name"].strip(), row.get("role"), row.get("department"),
             (row.get("manager_emp_id") or "").strip() or None, row.get("job_description")),
        )
        cur.execute("DELETE FROM employee_declared_skills WHERE emp_id = %s", (emp_id,))
        for column, source in (("declared_skills", "hrms"), ("resume_skills", "resume")):
            for skill in filter(None, (s.strip() for s in (row.get(column) or "").split(";"))):
                cur.execute("INSERT INTO employee_declared_skills (emp_id, skill, source) VALUES (%s, %s, %s) "
                            "ON CONFLICT DO NOTHING", (emp_id, skill, source))
    return len(rows)


def resolve_accounts(cur):
    """Link every human account on every platform to an employee by email; manual links are kept."""
    cur.execute(
        """
        WITH accounts AS (
            SELECT 'slack' AS platform, user_id AS platform_user_id, team_id AS account_id,
                   NULLIF(lower(email), '') AS email,
                   COALESCE(NULLIF(display_name, ''), NULLIF(real_name, ''), username) AS display_name
            FROM slack_users WHERE NOT is_bot AND NOT is_deleted AND user_id <> 'USLACKBOT'
            UNION ALL
            SELECT platform, object_id, account_id, NULLIF(lower(attributes->>'email'), ''), title
            FROM unified_records
            WHERE kind = 'person' AND NOT COALESCE((attributes->>'is_bot')::boolean, false)
        )
        INSERT INTO person_accounts (platform, platform_user_id, account_id, email, display_name, emp_id, match_method, updated_at)
        SELECT a.platform, a.platform_user_id, a.account_id, a.email, a.display_name, e.emp_id,
               CASE WHEN e.emp_id IS NOT NULL THEN 'email' ELSE 'unmatched' END, NOW()
        FROM accounts a LEFT JOIN employees e ON e.email = a.email
        ON CONFLICT (platform, platform_user_id) DO UPDATE SET
            account_id = EXCLUDED.account_id, email = EXCLUDED.email, display_name = EXCLUDED.display_name,
            emp_id = CASE WHEN person_accounts.match_method = 'manual' THEN person_accounts.emp_id ELSE EXCLUDED.emp_id END,
            match_method = CASE WHEN person_accounts.match_method = 'manual' THEN 'manual' ELSE EXCLUDED.match_method END,
            updated_at = NOW()
        """
    )
    overrides = _path("IDENTITY_OVERRIDES_FILE", "data/identity_overrides.csv")
    if overrides.exists():
        with overrides.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if (row.get("platform") or "").strip() and (row.get("platform_user_id") or "").strip():
                    cur.execute(
                        """
                        INSERT INTO person_accounts (platform, platform_user_id, emp_id, match_method, updated_at)
                        VALUES (%s, %s, %s, 'manual', NOW())
                        ON CONFLICT (platform, platform_user_id) DO UPDATE SET
                            emp_id = EXCLUDED.emp_id, match_method = 'manual', updated_at = NOW()
                        """,
                        (row["platform"].strip(), row["platform_user_id"].strip(), (row.get("emp_id") or "").strip() or None),
                    )


def build_activity(cur):
    """Upsert canonical activity records from every source and drop records whose source item is gone."""
    cur.execute(
        """
        INSERT INTO activity_records (
            activity_id, dedup_hash, source, activity_type, actor_emp_id, actor_user_id, actor_name,
            conversation_key, conversation_type, conversation_title, thread_key, occurred_at, text, source_url,
            recipient_emp_ids, recipient_user_ids, mention_emp_ids, mention_user_ids, context, updated_at)
        SELECT s.activity_id, md5(s.activity_id), s.source, s.activity_type, actor.emp_id, s.actor_user_id,
               COALESCE(e.full_name, actor.display_name, s.actor_user_id),
               s.conversation_key, s.conversation_type, s.conversation_title, s.thread_key, s.occurred_at, s.text,
               s.source_url,
               ARRAY(SELECT DISTINCT pa.emp_id FROM person_accounts pa
                      WHERE pa.platform = s.source AND pa.platform_user_id = ANY (s.recipient_user_ids)
                        AND pa.emp_id IS NOT NULL AND pa.emp_id IS DISTINCT FROM actor.emp_id),
               s.recipient_user_ids,
               ARRAY(SELECT DISTINCT pa.emp_id FROM person_accounts pa
                      WHERE pa.platform = s.source AND pa.platform_user_id = ANY (s.mention_user_ids)
                        AND pa.emp_id IS NOT NULL),
               s.mention_user_ids, s.context, NOW()
        FROM canonical_activity_source s
        LEFT JOIN person_accounts actor ON actor.platform = s.source AND actor.platform_user_id = s.actor_user_id
        LEFT JOIN employees e ON e.emp_id = actor.emp_id
        ON CONFLICT (activity_id) DO UPDATE SET
            activity_type = EXCLUDED.activity_type, actor_emp_id = EXCLUDED.actor_emp_id,
            actor_user_id = EXCLUDED.actor_user_id, actor_name = EXCLUDED.actor_name,
            conversation_key = EXCLUDED.conversation_key, conversation_type = EXCLUDED.conversation_type,
            conversation_title = EXCLUDED.conversation_title, thread_key = EXCLUDED.thread_key,
            occurred_at = EXCLUDED.occurred_at, text = EXCLUDED.text, source_url = EXCLUDED.source_url,
            recipient_emp_ids = EXCLUDED.recipient_emp_ids, recipient_user_ids = EXCLUDED.recipient_user_ids,
            mention_emp_ids = EXCLUDED.mention_emp_ids, mention_user_ids = EXCLUDED.mention_user_ids,
            context = EXCLUDED.context, updated_at = NOW()
        """
    )
    upserted = cur.rowcount
    cur.execute("DELETE FROM activity_records a WHERE NOT EXISTS "
                "(SELECT 1 FROM canonical_activity_source s WHERE s.activity_id = a.activity_id)")
    return upserted, cur.rowcount


def run(database_url=None):
    """Run the whole Phase 1 identity + canonical-record step in one transaction; returns a summary."""
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL in .env")
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        for schema in SCHEMAS:
            cur.execute(schema.read_text(encoding="utf-8"))
        employees = load_hrms(cur)
        resolve_accounts(cur)
        upserted, removed = build_activity(cur)
        conn.commit()
        cur.execute("""SELECT platform, count(*) FILTER (WHERE emp_id IS NOT NULL), count(*)
                       FROM person_accounts GROUP BY 1 ORDER BY 1""")
        accounts = {p: {"matched": m, "total": t} for p, m, t in cur.fetchall()}
        cur.execute("SELECT source, count(*), count(actor_emp_id) FROM activity_records GROUP BY 1 ORDER BY 1")
        activity = {s: {"records": n, "with_emp_id": k} for s, n, k in cur.fetchall()}
        cur.execute("SELECT count(*) FROM activity_threads")
        threads = cur.fetchone()[0]
    return {"employees": employees, "accounts": accounts, "activity": activity, "threads": threads,
            "upserted": upserted, "removed": removed}


def main():
    summary = run()
    print(f"Employees loaded from HRMS: {summary['employees']}")
    print("Accounts linked to an EMP_ID:")
    for platform, counts in summary["accounts"].items():
        print(f"  {platform:11} {counts['matched']}/{counts['total']}")
    print("Canonical activity records:")
    for source, counts in summary["activity"].items():
        print(f"  {source:11} {counts['records']} records ({counts['with_emp_id']} with a resolved sender)")
    print(f"Threads ready for Agent 1: {summary['threads']}")


if __name__ == "__main__":
    main()
