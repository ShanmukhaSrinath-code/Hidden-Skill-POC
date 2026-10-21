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
LIMIT 25;
