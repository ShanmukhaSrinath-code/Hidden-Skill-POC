-- Shared cross-platform record and relationship layer. Existing Slack tables remain intact.
-- Each source row keeps its platform ID so normalized records remain traceable.

CREATE TABLE IF NOT EXISTS connected_accounts (
    platform       TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    display_name   TEXT,
    connected_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (platform, account_id)
);

CREATE TABLE IF NOT EXISTS unified_records (
    record_id      BIGSERIAL PRIMARY KEY,
    platform       TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    object_id      TEXT NOT NULL,
    kind           TEXT NOT NULL,
    parent_platform TEXT,
    parent_account_id TEXT,
    parent_type    TEXT,
    parent_id      TEXT,
    actor_id       TEXT,
    title          TEXT,
    body           TEXT,
    source_url     TEXT,
    occurred_at    TIMESTAMPTZ,
    attributes     JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw            JSONB NOT NULL DEFAULT '{}'::jsonb,
    synced_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (platform, account_id, object_type, object_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_records_kind_time
    ON unified_records (kind, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_unified_records_parent
    ON unified_records (parent_platform, parent_account_id, parent_type, parent_id);

CREATE TABLE IF NOT EXISTS unified_record_links (
    link_id        BIGSERIAL PRIMARY KEY,
    left_platform  TEXT NOT NULL,
    left_account_id TEXT NOT NULL,
    left_type      TEXT NOT NULL,
    left_id        TEXT NOT NULL,
    right_platform TEXT NOT NULL,
    right_account_id TEXT NOT NULL,
    right_type     TEXT NOT NULL,
    right_id       TEXT NOT NULL,
    relation       TEXT NOT NULL,
    created_by     TEXT NOT NULL DEFAULT 'system',
    confidence     REAL,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (left_platform, left_account_id, left_type, left_id,
            right_platform, right_account_id, right_type, right_id, relation)
);

CREATE TABLE IF NOT EXISTS unified_sync_checkpoints (
    platform       TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    scope_id       TEXT NOT NULL,
    cursor         TEXT,
    status         TEXT,
    detail         TEXT,
    last_run       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (platform, account_id, scope_id)
);

-- Who is involved in a record (assignee, reporter, watcher, commenter, ...): the recipients of its activity.
CREATE TABLE IF NOT EXISTS unified_participants (
    platform       TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    object_id      TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    role           TEXT NOT NULL,
    PRIMARY KEY (platform, account_id, object_type, object_id, user_id, role)
);

CREATE INDEX IF NOT EXISTS idx_unified_participants_user
    ON unified_participants (platform, account_id, user_id);

-- @mentions inside a record's text.
CREATE TABLE IF NOT EXISTS unified_mentions (
    platform       TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    object_id      TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    PRIMARY KEY (platform, account_id, object_type, object_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_mentions_target
    ON unified_mentions (platform, account_id, target_id);

CREATE TABLE IF NOT EXISTS unified_reactions (
    platform       TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    object_id      TEXT NOT NULL,
    reaction       TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    PRIMARY KEY (platform, account_id, object_type, object_id, reaction, user_id)
);

-- Message mapping for non-Slack platforms: one row per issue/task, comment or change event with
-- its sender, recipients (participants of the parent issue/task other than the sender) and mentions.
-- People are unified_records rows with object_type = 'user'.
CREATE OR REPLACE VIEW unified_message_map AS
SELECT r.platform, r.account_id, r.object_type, r.object_id, r.kind,
       COALESCE(r.parent_type, r.object_type)  AS conversation_type,
       COALESCE(r.parent_id, r.object_id)      AS conversation_id,
       COALESCE(conv.title, r.title)           AS conversation_title,
       r.actor_id                              AS sender_id,
       COALESCE(sender.title, r.actor_id)      AS sender,
       r.title, r.body, r.source_url, r.occurred_at,
       COALESCE((SELECT json_agg(DISTINCT COALESCE(who.title, p.user_id))
                   FROM unified_participants p
                   LEFT JOIN unified_records who
                          ON who.platform = p.platform AND who.account_id = p.account_id
                         AND who.object_type = 'user' AND who.object_id = p.user_id
                  WHERE p.platform = r.platform AND p.account_id = r.account_id
                    AND p.object_type = COALESCE(r.parent_type, r.object_type)
                    AND p.object_id = COALESCE(r.parent_id, r.object_id)
                    AND p.user_id <> COALESCE(r.actor_id, '')), '[]'::json) AS recipients,
       COALESCE((SELECT json_agg(DISTINCT COALESCE(who.title, m.target_id))
                   FROM unified_mentions m
                   LEFT JOIN unified_records who
                          ON who.platform = m.platform AND who.account_id = m.account_id
                         AND who.object_type = 'user' AND who.object_id = m.target_id
                  WHERE m.platform = r.platform AND m.account_id = r.account_id
                    AND m.object_type = r.object_type AND m.object_id = r.object_id), '[]'::json) AS mentions
FROM unified_records r
LEFT JOIN unified_records conv
       ON conv.platform = r.platform AND conv.account_id = r.parent_account_id
      AND conv.object_type = r.parent_type AND conv.object_id = r.parent_id
LEFT JOIN unified_records sender
       ON sender.platform = r.platform AND sender.account_id = r.account_id
      AND sender.object_type = 'user' AND sender.object_id = r.actor_id
WHERE r.kind IN ('task', 'document', 'comment', 'message', 'event');

-- Every record a person is involved in, from their point of view: sent, received or mentioned.
CREATE OR REPLACE VIEW unified_person_messages AS
SELECT m.platform, x.user_id AS person_id, COALESCE(person.title, x.user_id) AS person,
       person.attributes->>'email' AS email, x.direction,
       m.object_type, m.object_id, COALESCE(m.parent_id, m.object_id) AS conversation_id,
       m.title, m.body, m.occurred_at
FROM unified_records m
JOIN LATERAL (
    SELECT m.actor_id AS user_id, 'sent' AS direction
    UNION
    SELECT p.user_id, 'received' FROM unified_participants p
     WHERE p.platform = m.platform AND p.account_id = m.account_id
       AND p.object_type = COALESCE(m.parent_type, m.object_type)
       AND p.object_id = COALESCE(m.parent_id, m.object_id)
       AND p.user_id <> COALESCE(m.actor_id, '')
    UNION
    SELECT t.target_id, 'mentioned' FROM unified_mentions t
     WHERE t.platform = m.platform AND t.account_id = m.account_id
       AND t.object_type = m.object_type AND t.object_id = m.object_id
) x ON x.user_id IS NOT NULL
LEFT JOIN unified_records person
       ON person.platform = m.platform AND person.account_id = m.account_id
      AND person.object_type = 'user' AND person.object_id = x.user_id
WHERE m.kind IN ('task', 'document', 'comment', 'message', 'event');
