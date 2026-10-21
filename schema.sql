-- Agent Messaging Intelligence: message mapping schema.
-- Safe to re-run; every statement is idempotent.

CREATE TABLE IF NOT EXISTS slack_users (
    user_id       TEXT PRIMARY KEY,
    team_id       TEXT,
    username      TEXT,
    real_name     TEXT,
    display_name  TEXT,
    email         TEXT,
    is_bot        BOOLEAN DEFAULT FALSE,
    is_deleted    BOOLEAN DEFAULT FALSE,
    profile       JSONB,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS slack_usergroups (
    usergroup_id  TEXT PRIMARY KEY,
    handle        TEXT,
    name          TEXT,
    member_ids    JSONB,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- One row per channel, private channel, group DM (mpim) or DM (im).
CREATE TABLE IF NOT EXISTS slack_conversations (
    conversation_id    TEXT PRIMARY KEY,
    conversation_type  TEXT NOT NULL,   -- public_channel | private_channel | mpim | im
    name               TEXT,
    dm_user_id         TEXT,            -- im only: the other participant
    is_archived        BOOLEAN DEFAULT FALSE,
    topic              TEXT,
    purpose            TEXT,
    created_at         TIMESTAMPTZ,
    raw                JSONB,
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_members (
    conversation_id  TEXT REFERENCES slack_conversations(conversation_id) ON DELETE CASCADE,
    user_id          TEXT,
    PRIMARY KEY (conversation_id, user_id)
);

-- Top-level messages and thread replies. message_ts is Slack's unique id within a conversation.
CREATE TABLE IF NOT EXISTS slack_messages (
    conversation_id    TEXT NOT NULL REFERENCES slack_conversations(conversation_id) ON DELETE CASCADE,
    message_ts         TEXT NOT NULL,
    user_id            TEXT,
    bot_id             TEXT,
    subtype            TEXT,
    message_text       TEXT,
    thread_ts          TEXT,
    is_thread_reply    BOOLEAN DEFAULT FALSE,
    parent_user_id     TEXT,
    reply_count        INTEGER DEFAULT 0,
    reply_users_count  INTEGER DEFAULT 0,
    latest_reply       TEXT,
    edited_ts          TEXT,
    has_files          BOOLEAN DEFAULT FALSE,
    sent_at            TIMESTAMPTZ NOT NULL,
    raw_message        JSONB,
    collected_via      TEXT,            -- which token (bot / user id) read this message
    collected_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (conversation_id, message_ts)
);

-- Link back to the message in Slack (https://<workspace>.slack.com/archives/<channel>/p<ts>).
ALTER TABLE slack_messages ADD COLUMN IF NOT EXISTS permalink TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_user      ON slack_messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv_time ON slack_messages(conversation_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_messages_thread    ON slack_messages(conversation_id, thread_ts);

-- @user, @usergroup, @here/@channel/@everyone and #channel references.
CREATE TABLE IF NOT EXISTS slack_mentions (
    conversation_id  TEXT NOT NULL,
    message_ts       TEXT NOT NULL,
    mention_type     TEXT NOT NULL,     -- user | usergroup | broadcast | channel
    target_id        TEXT NOT NULL,     -- user id, usergroup id, here/channel/everyone, channel id
    PRIMARY KEY (conversation_id, message_ts, mention_type, target_id),
    FOREIGN KEY (conversation_id, message_ts)
        REFERENCES slack_messages(conversation_id, message_ts) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mentions_target ON slack_mentions(mention_type, target_id);

CREATE TABLE IF NOT EXISTS slack_reactions (
    conversation_id  TEXT NOT NULL,
    message_ts       TEXT NOT NULL,
    reaction         TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    PRIMARY KEY (conversation_id, message_ts, reaction, user_id),
    FOREIGN KEY (conversation_id, message_ts)
        REFERENCES slack_messages(conversation_id, message_ts) ON DELETE CASCADE
);

-- Sync progress and coverage: records inaccessible conversations instead of silently skipping them.
CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
    conversation_id  TEXT PRIMARY KEY,
    collected_via    TEXT,
    last_ts          TEXT,
    status           TEXT,              -- ok | error code from Slack (not_in_channel, missing_scope, ...)
    detail           TEXT,
    last_run         TIMESTAMPTZ DEFAULT NOW()
);
