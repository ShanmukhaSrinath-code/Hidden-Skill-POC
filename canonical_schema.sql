-- Phase 1: canonical identity and canonical activity records.
-- Needs schema.sql (Slack) and unified_schema.sql (Jira / Confluence / ClickUp). Safe to re-run.

-- One row per employee, keyed by a canonical EMP_ID (from HRMS). Email is the join key to platform accounts.
CREATE TABLE IF NOT EXISTS employees (
    emp_id           TEXT PRIMARY KEY,
    email            TEXT NOT NULL UNIQUE,
    full_name        TEXT NOT NULL,
    role             TEXT,
    department       TEXT,
    manager_emp_id   TEXT,
    job_description  TEXT,
    source           TEXT NOT NULL DEFAULT 'hrms',
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Explicit (declared) skills: the baseline the discovered skills are compared against.
CREATE TABLE IF NOT EXISTS employee_declared_skills (
    emp_id   TEXT NOT NULL REFERENCES employees(emp_id) ON DELETE CASCADE,
    skill    TEXT NOT NULL,
    source   TEXT NOT NULL,              -- hrms | resume
    PRIMARY KEY (emp_id, skill, source)
);

-- Every account on every platform, linked to an employee when resolved.
CREATE TABLE IF NOT EXISTS person_accounts (
    platform          TEXT NOT NULL,      -- slack | jira | confluence | clickup
    platform_user_id  TEXT NOT NULL,
    account_id        TEXT,               -- workspace / site / team the account belongs to
    email             TEXT,
    display_name      TEXT,
    emp_id            TEXT REFERENCES employees(emp_id) ON DELETE SET NULL,
    match_method      TEXT NOT NULL,      -- email | manual | unmatched
    updated_at        TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (platform, platform_user_id)
);
CREATE INDEX IF NOT EXISTS idx_person_accounts_emp ON person_accounts (emp_id);

-- Every collected item from every source, in one shape (computed live from the source tables).
CREATE OR REPLACE VIEW canonical_activity_source AS
SELECT 'slack:' || m.conversation_id || ':' || m.message_ts                    AS activity_id,
       'slack'                                                                  AS source,
       CASE WHEN m.is_thread_reply THEN 'reply' ELSE 'message' END              AS activity_type,
       m.user_id                                                                AS actor_user_id,
       'slack:conversation:' || m.conversation_id                               AS conversation_key,
       COALESCE(c.conversation_type, 'channel')                                 AS conversation_type,
       CASE c.conversation_type WHEN 'im' THEN 'DM' WHEN 'mpim' THEN 'Group DM'
            ELSE '#' || COALESCE(c.name, m.conversation_id) END                 AS conversation_title,
       -- Threads group by thread_ts; un-threaded messages group by conversation + day.
       'slack:' || m.conversation_id || ':' ||
           COALESCE(m.thread_ts, to_char(m.sent_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')) AS thread_key,
       m.sent_at                                                                AS occurred_at,
       m.message_text                                                           AS text,
       m.permalink                                                              AS source_url,
       ARRAY(SELECT cm.user_id FROM conversation_members cm
              WHERE cm.conversation_id = m.conversation_id
                AND cm.user_id <> COALESCE(m.user_id, ''))                      AS recipient_user_ids,
       ARRAY(SELECT sm.target_id FROM slack_mentions sm
              WHERE sm.conversation_id = m.conversation_id AND sm.message_ts = m.message_ts
                AND sm.mention_type = 'user')                                   AS mention_user_ids,
       jsonb_strip_nulls(jsonb_build_object('conversation_type', c.conversation_type,
                                            'thread_ts', m.thread_ts, 'subtype', m.subtype)) AS context
FROM slack_messages m
LEFT JOIN slack_conversations c ON c.conversation_id = m.conversation_id
LEFT JOIN slack_users su ON su.user_id = m.user_id
-- people only: no join notices or other system subtypes, no bots
WHERE (m.subtype IS NULL OR m.subtype IN ('thread_broadcast', 'file_share', 'me_message'))
  AND NOT COALESCE(su.is_bot, false) AND m.user_id IS NOT NULL AND m.user_id <> 'USLACKBOT'
UNION ALL
SELECT r.platform || ':' || r.object_type || ':' || r.object_id                 AS activity_id,
       r.platform                                                               AS source,
       CASE r.object_type
            WHEN 'issue' THEN 'issue_created' WHEN 'task' THEN 'task_created'
            WHEN 'page' THEN 'page_created' WHEN 'blogpost' THEN 'blogpost_created'
            WHEN 'comment' THEN CASE WHEN r.attributes->>'reply_to' IS NOT NULL THEN 'reply' ELSE 'comment' END
            WHEN 'inline_comment' THEN 'inline_comment'
            WHEN 'chat_message' THEN CASE WHEN r.attributes->>'reply_to' IS NOT NULL THEN 'reply' ELSE 'message' END
            WHEN 'change' THEN 'field_change' WHEN 'edit' THEN 'page_edit' WHEN 'task_item' THEN 'task_assignment'
            ELSE r.object_type END                                              AS activity_type,
       r.actor_id                                                               AS actor_user_id,
       r.platform || ':' || COALESCE(r.parent_type, r.object_type) || ':' || COALESCE(r.parent_id, r.object_id) AS conversation_key,
       COALESCE(r.parent_type, r.object_type)                                   AS conversation_type,
       COALESCE(conv.title, r.title)                                            AS conversation_title,
       CASE WHEN r.object_type = 'chat_message'
            THEN r.platform || ':chat:' || r.parent_id || ':' || to_char(r.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')
            ELSE r.platform || ':' || COALESCE(r.parent_type, r.object_type) || ':' || COALESCE(r.parent_id, r.object_id)
       END                                                                      AS thread_key,
       r.occurred_at,
       CASE WHEN r.kind IN ('task', 'document') THEN concat_ws(' — ', r.title, NULLIF(r.body, '')) ELSE r.body END AS text,
       r.source_url,
       ARRAY(SELECT DISTINCT p.user_id FROM unified_participants p
              WHERE p.platform = r.platform AND p.account_id = r.account_id
                AND p.object_type = COALESCE(r.parent_type, r.object_type)
                AND p.object_id = COALESCE(r.parent_id, r.object_id)
                AND p.user_id <> COALESCE(r.actor_id, ''))                     AS recipient_user_ids,
       ARRAY(SELECT mn.target_id FROM unified_mentions mn
              WHERE mn.platform = r.platform AND mn.account_id = r.account_id
                AND mn.object_type = r.object_type AND mn.object_id = r.object_id) AS mention_user_ids,
       jsonb_strip_nulls(jsonb_build_object('item', r.attributes - 'raw', 'conversation', conv.attributes)) AS context
FROM unified_records r
LEFT JOIN unified_records conv
       ON conv.platform = r.platform AND conv.account_id = r.parent_account_id
      AND conv.object_type = r.parent_type AND conv.object_id = r.parent_id
LEFT JOIN unified_records actor
       ON actor.platform = r.platform AND actor.account_id = r.account_id
      AND actor.object_type = 'user' AND actor.object_id = r.actor_id
WHERE r.kind IN ('task', 'document', 'comment', 'message', 'event')
  -- people only (no bots / system users), and no empty template checkboxes
  AND NOT COALESCE((actor.attributes->>'is_bot')::boolean, false)
  AND NOT (r.object_type = 'task_item' AND r.body ~ '^task \([^)]*\):\s*$');

-- Canonical activity records: the Phase 1 contract (who · did what · where · when · business context),
-- with people resolved to EMP_IDs. Rebuilt idempotently from canonical_activity_source.
CREATE TABLE IF NOT EXISTS activity_records (
    activity_id         TEXT PRIMARY KEY,                 -- source:type:id (native ids kept)
    dedup_hash          TEXT NOT NULL UNIQUE,             -- md5(activity_id)
    source              TEXT NOT NULL,
    activity_type       TEXT NOT NULL,
    actor_emp_id        TEXT,
    actor_user_id       TEXT,
    actor_name          TEXT,
    conversation_key    TEXT NOT NULL,
    conversation_type   TEXT,
    conversation_title  TEXT,
    thread_key          TEXT NOT NULL,
    occurred_at         TIMESTAMPTZ,
    text                TEXT,
    source_url          TEXT,
    recipient_emp_ids   TEXT[] NOT NULL DEFAULT '{}',
    recipient_user_ids  TEXT[] NOT NULL DEFAULT '{}',
    mention_emp_ids     TEXT[] NOT NULL DEFAULT '{}',
    mention_user_ids    TEXT[] NOT NULL DEFAULT '{}',
    context             JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_activity_actor  ON activity_records (actor_emp_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_activity_thread ON activity_records (thread_key, occurred_at);
CREATE INDEX IF NOT EXISTS idx_activity_recipients ON activity_records USING GIN (recipient_emp_ids);
CREATE INDEX IF NOT EXISTS idx_activity_mentions   ON activity_records USING GIN (mention_emp_ids);

-- Threads: the unit Agent 1 reads (one help exchange = one thread).
CREATE OR REPLACE VIEW activity_threads AS
SELECT thread_key,
       min(source)                                                     AS source,
       min(conversation_key)                                           AS conversation_key,
       min(conversation_title)                                         AS conversation_title,
       count(*)                                                        AS activity_count,
       min(occurred_at)                                                AS started_at,
       max(occurred_at)                                                AS last_activity_at,
       array_agg(DISTINCT actor_emp_id) FILTER (WHERE actor_emp_id IS NOT NULL) AS participant_emp_ids,
       string_agg(COALESCE(actor_name, actor_user_id, '?') || ' (' || activity_type || '): ' || COALESCE(text, ''),
                  E'\n' ORDER BY occurred_at)                          AS transcript,
       array_agg(source_url ORDER BY occurred_at) FILTER (WHERE source_url IS NOT NULL) AS evidence_links
FROM activity_records
GROUP BY thread_key;

-- Per employee and source: how many items they sent, received or were mentioned in.
CREATE OR REPLACE VIEW employee_activity_summary AS
SELECT e.emp_id, e.full_name, e.role, a.source,
       count(*) FILTER (WHERE a.actor_emp_id = e.emp_id)              AS sent,
       count(*) FILTER (WHERE e.emp_id = ANY (a.recipient_emp_ids) AND a.actor_emp_id IS DISTINCT FROM e.emp_id) AS received,
       count(*) FILTER (WHERE e.emp_id = ANY (a.mention_emp_ids))     AS mentioned,
       count(DISTINCT a.thread_key)                                    AS threads
FROM employees e
JOIN activity_records a
  ON a.actor_emp_id = e.emp_id OR e.emp_id = ANY (a.recipient_emp_ids) OR e.emp_id = ANY (a.mention_emp_ids)
GROUP BY e.emp_id, e.full_name, e.role, a.source;
