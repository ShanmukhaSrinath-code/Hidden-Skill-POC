-- Per-person message mapping.
-- Run:  docker exec -i slack-pg psql -U postgres -d slack_intelligence < queries.sql
-- Change the username in the "person" CTEs below to look at someone else.

-- Every message a person can see, from their point of view: sent or received, by conversation type.
CREATE OR REPLACE VIEW person_messages AS
SELECT
    p.user_id                                   AS person_id,
    p.username                                  AS person,
    CASE WHEN m.user_id = p.user_id THEN 'sent' ELSE 'received' END AS direction,
    c.conversation_type,
    CASE c.conversation_type
        WHEN 'im' THEN 'DM with ' || COALESCE(other.username, c.dm_user_id)
        ELSE COALESCE(c.name, c.conversation_id)
    END                                         AS conversation,
    sender.username                             AS sender,
    m.message_text,
    m.is_thread_reply,
    m.sent_at,
    m.conversation_id,
    m.message_ts
FROM slack_users p
JOIN conversation_members cm ON cm.user_id = p.user_id
JOIN slack_conversations c   ON c.conversation_id = cm.conversation_id
JOIN slack_messages m        ON m.conversation_id = c.conversation_id
LEFT JOIN slack_users sender ON sender.user_id = m.user_id
-- For a DM, name the other participant (dm_user_id is relative to whichever token listed it).
LEFT JOIN conversation_members cm2
       ON c.conversation_type = 'im' AND cm2.conversation_id = c.conversation_id AND cm2.user_id <> p.user_id
LEFT JOIN slack_users other  ON other.user_id = cm2.user_id
WHERE NOT p.is_bot
  AND (m.subtype IS NULL OR m.subtype IN ('thread_broadcast', 'file_share', 'me_message'));

-- 1. Summary: sent / received per person and conversation type.
SELECT person, conversation_type, direction, COUNT(*) AS messages
FROM person_messages
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

-- 2. Who talks to whom in DMs (sender -> recipient).
SELECT sender.username AS sender, recipient.username AS recipient, COUNT(*) AS messages
FROM slack_messages m
JOIN slack_conversations c ON c.conversation_id = m.conversation_id AND c.conversation_type IN ('im', 'mpim')
JOIN conversation_members cm ON cm.conversation_id = c.conversation_id AND cm.user_id <> m.user_id
JOIN slack_users sender    ON sender.user_id = m.user_id
JOIN slack_users recipient ON recipient.user_id = cm.user_id
WHERE m.subtype IS NULL AND NOT sender.is_bot
GROUP BY 1, 2
ORDER BY 1, 2;

-- 3. Who mentions whom.
SELECT author.username AS mentioned_by, target.username AS mentioned, COUNT(*) AS times
FROM slack_mentions x
JOIN slack_messages m ON m.conversation_id = x.conversation_id AND m.message_ts = x.message_ts
JOIN slack_users author ON author.user_id = m.user_id
JOIN slack_users target ON target.user_id = x.target_id
WHERE x.mention_type = 'user'
GROUP BY 1, 2
ORDER BY 3 DESC;

-- 3b. Email addresses written in messages, matched to a workspace member when the address belongs to one.
SELECT author.username AS written_by,
       c.conversation_type,
       COALESCE(c.name, 'DM') AS conversation,
       x.target_id AS email,
       COALESCE(owner.username, '(not a member)') AS email_belongs_to,
       m.sent_at
FROM slack_mentions x
JOIN slack_messages m      ON m.conversation_id = x.conversation_id AND m.message_ts = x.message_ts
JOIN slack_conversations c ON c.conversation_id = m.conversation_id
LEFT JOIN slack_users author ON author.user_id = m.user_id
LEFT JOIN slack_users owner  ON LOWER(owner.email) = x.target_id
WHERE x.mention_type = 'email'
ORDER BY m.sent_at DESC;

-- 3c. Message mapping: one row per message with sender, recipients and mentions, across every conversation type.
SELECT m.sent_at,
       c.conversation_type,
       CASE WHEN c.conversation_type IN ('im', 'mpim') THEN '(private)' ELSE '#' || c.name END AS conversation,
       CASE WHEN m.is_thread_reply THEN 'reply' ELSE 'message' END AS kind,
       sender.username AS sender,
       (SELECT string_agg(r.username, ', ' ORDER BY r.username)
          FROM conversation_members cm JOIN slack_users r ON r.user_id = cm.user_id
         WHERE cm.conversation_id = m.conversation_id AND cm.user_id <> m.user_id AND NOT r.is_bot) AS recipients,
       (SELECT string_agg(x.mention_type || ':' || COALESCE(t.username, x.target_id), ', ')
          FROM slack_mentions x LEFT JOIN slack_users t ON t.user_id = x.target_id
         WHERE x.conversation_id = m.conversation_id AND x.message_ts = m.message_ts) AS mentions
FROM slack_messages m
JOIN slack_conversations c ON c.conversation_id = m.conversation_id
JOIN slack_users sender    ON sender.user_id = m.user_id
WHERE NOT sender.is_bot
  AND (m.subtype IS NULL OR m.subtype IN ('thread_broadcast', 'file_share', 'me_message'))
ORDER BY m.sent_at DESC
LIMIT 50;

-- 4. Full timeline for one person (sent and received, every conversation type).
SELECT sent_at, direction, conversation_type, conversation, sender, LEFT(message_text, 80) AS message
FROM person_messages
WHERE person = 'ramudhatla'
ORDER BY sent_at DESC
LIMIT 100;
