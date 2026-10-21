"""ClickUp connector boundary. ClickUp auth and API calls live only in this module.

Tasks and task comments are shared across the workspace, so one token reads them all. ClickUp Chat
channels, DMs and group DMs are only visible to their members, so chat is read with every member
token provided (like Slack user tokens), and each channel is read once by the first token that sees it.
ClickUp has no task-history API: who changed a status or assignee arrives only through webhooks.
"""

import json
import os
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from integrations.records import SourceRef, UnifiedRecord

API = "https://api.clickup.com/api"
# Re-read chat messages this far behind the newest one already stored, to pick up late reactions/replies.
CHAT_LOOKBACK_MS = int(os.getenv("CLICKUP_CHAT_LOOKBACK_HOURS", "24")) * 3600 * 1000


class ClickUpConnector:
    def __init__(self, token=None, team_id=None, user_tokens=None):
        self.token = token or os.getenv("CLICKUP_API_TOKEN", "")
        self.team_id = str(team_id or os.getenv("CLICKUP_TEAM_ID", ""))
        if not self.token or not self.team_id:
            raise ValueError("Set CLICKUP_API_TOKEN and CLICKUP_TEAM_ID")
        self.account_id = self.team_id
        if user_tokens is None:
            # CLICKUP_USER_TOKENS=pk_a,pk_b and/or one CLICKUP_USER_TOKEN_<NAME>=pk_... line per member.
            user_tokens = os.getenv("CLICKUP_USER_TOKENS", "").split(",")
            user_tokens += [v for k, v in sorted(os.environ.items()) if k.startswith("CLICKUP_USER_TOKEN_")]
            user_tokens = [t.strip() for t in user_tokens if t.strip()]
        self.chat_tokens = list(dict.fromkeys([self.token, *user_tokens]))
        self.users = {}  # user id -> display name, filled by list_users() and chat members

    def _request(self, path, params=None, token=None, method="GET", body=None):
        url = f"{API}{path}"
        if params:
            url += "?" + urlencode(params, doseq=True)
        headers = {"Authorization": token or self.token, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        for attempt in range(4):
            try:
                with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    # Free plans allow 100 requests per minute per token; wait for the window to reset.
                    reset = exc.headers.get("X-RateLimit-Reset")
                    wait = float(reset) - time.time() if reset else 10
                    time.sleep(min(max(wait, 1), 60))
                    continue
                detail = exc.read()[:300].decode(errors="replace")
                raise RuntimeError(f"ClickUp API {method} {path} failed: {exc.code} {detail}") from exc
            except URLError as exc:
                raise RuntimeError(f"ClickUp API request failed: {exc}") from exc

    def _cursor(self, path, params=None, token=None):
        """Yield items from ClickUp v3 cursor-paged endpoints ({"data": [...], "next_cursor": ...})."""
        cursor = None
        while True:
            page = {**(params or {}), "limit": 100}
            if cursor:
                page["cursor"] = cursor
            data = self._request(path, page, token=token)
            items = data.get("data") or []
            yield from items
            cursor = data.get("next_cursor")
            if not cursor or not items:
                break

    # ------------------------------------------------------------------ people

    def list_users(self):
        """Yield every workspace member as a person record."""
        teams = self._request("/v2/team").get("teams", [])
        team = next((t for t in teams if str(t.get("id")) == self.team_id), None)
        if team is None:
            raise RuntimeError(f"CLICKUP_TEAM_ID {self.team_id} is not a workspace this token can access")
        for member in team.get("members", []):
            user = member.get("user") or {}
            if user.get("id") is None:
                continue
            uid = str(user["id"])
            self.users[uid] = user.get("username") or user.get("email") or uid
            yield UnifiedRecord(
                source=SourceRef("clickup", self.account_id, "user", uid), kind="person",
                title=self.users[uid],
                attributes={"email": (user.get("email") or "").lower() or None, "is_bot": False,
                            "role": user.get("role")},
                raw=member,
            )

    # ------------------------------------------------------------------ tasks

    def list_tasks(self, known=None):
        """Yield task, comment and reply records for every task in the workspace.

        `known` maps task id -> date_updated already stored; tasks with the same date_updated are skipped.
        """
        page = 0
        while True:
            data = self._request(f"/v2/team/{self.team_id}/task", {
                "page": page, "include_closed": "true", "subtasks": "true", "order_by": "updated"})
            tasks = data.get("tasks", [])
            for task in tasks:
                if known is not None and known.get(str(task["id"])) == str(task.get("date_updated")):
                    continue
                yield from self.task_records(task)
            if data.get("last_page", len(tasks) < 100) or not tasks:
                break
            page += 1

    def get_task(self, task_id):
        return self.task_records(self._request(f"/v2/task/{task_id}", {"include_subtasks": "true"}))

    def task_records(self, task):
        """A task is the conversation: creator, assignees, watchers and commenters are its participants."""
        task_id = str(task["id"])
        task_ref = SourceRef("clickup", self.account_id, "task", task_id, task.get("url"))
        comments = []
        for comment in self._comments(f"/v2/task/{task_id}/comment"):
            comments.append((comment, None))
            if int(comment.get("reply_count") or 0):
                for reply in self._request(f"/v2/comment/{comment['id']}/reply").get("comments", []):
                    comments.append((reply, str(comment["id"])))

        participants = [(_uid(task.get("creator")), "creator")]
        participants += [(_uid(u), "assignee") for u in task.get("assignees") or []]
        participants += [(_uid(u), "watcher") for u in task.get("watchers") or []]
        participants += [(_uid(c.get("user")), "commenter") for c, _ in comments]
        participants = list(dict.fromkeys((u, role) for u, role in participants if u))

        yield UnifiedRecord(
            source=task_ref, kind="task", title=task.get("name"),
            body=task.get("text_content") or task.get("description"),
            occurred_at=_timestamp(task.get("date_created")), actor_id=_uid(task.get("creator")),
            attributes={"status": (task.get("status") or {}).get("status"),
                        "priority": (task.get("priority") or {}).get("priority"),
                        "due_date": _date(task.get("due_date")),
                        "labels": [t.get("name") for t in task.get("tags") or []],
                        "parent_key": task.get("parent"),
                        "list_name": (task.get("list") or {}).get("name"),
                        "date_updated": str(task.get("date_updated")),
                        "date_closed": _date(task.get("date_closed"))},
            raw=task, participants=participants,
        )
        for comment, reply_to in comments:
            comment_id = str(comment["id"])
            mentions = [_uid(seg.get("user")) for seg in comment.get("comment") or []
                        if isinstance(seg, dict) and isinstance(seg.get("user"), dict)]
            # An "assigned comment" is directed at one person, like a mention.
            mentions.append(_uid(comment.get("assignee")))
            yield UnifiedRecord(
                source=SourceRef("clickup", self.account_id, "comment", comment_id, task.get("url")),
                kind="comment", actor_id=_uid(comment.get("user")), parent=task_ref,
                # The segments keep @mentions as "@Name"; comment_text drops them.
                body="".join(seg.get("text", "") for seg in comment.get("comment") or [] if isinstance(seg, dict))
                     or comment.get("comment_text") or comment.get("text_content"),
                occurred_at=_timestamp(comment.get("date")),
                attributes={"reply_to": reply_to, "resolved": comment.get("resolved")}, raw=comment,
                mentions=list(dict.fromkeys(m for m in mentions if m)),
                reactions=_reactions(comment.get("reactions")),
            )

    def _comments(self, path):
        """Task comments come newest first, 25 per page, paged with the last comment's date and id."""
        params, seen = {}, set()
        while True:
            comments = [c for c in self._request(path, params).get("comments", []) if str(c["id"]) not in seen]
            yield from comments
            seen.update(str(c["id"]) for c in comments)
            if len(comments) < 25:
                break
            params = {"start": comments[-1]["date"], "start_id": comments[-1]["id"]}

    def history_records(self, task_id, history_items):
        """Change events from a webhook payload: who changed which task field, from what to what."""
        task_ref = SourceRef("clickup", self.account_id, "task", str(task_id))
        for item in history_items:
            field = item.get("field")
            if field == "comment" or not item.get("id"):
                continue  # comments are re-read from the API with their mentions and reactions
            yield UnifiedRecord(
                source=SourceRef("clickup", self.account_id, "change", str(item["id"])),
                kind="event", parent=task_ref, actor_id=_uid(item.get("user")),
                occurred_at=_timestamp(item.get("date")),
                body=f"{field}: {_describe(item.get('before'))} -> {_describe(item.get('after'))}",
                attributes={"field": field}, raw=item,
            )

    # ------------------------------------------------------------------ chat

    def chat_records(self, state):
        """Yield chat channel and message records visible to any configured token.

        `state` maps channel id -> {"latest": ..., "max_date": ms} from the previous sync and is updated
        in place. Channels whose latest activity is unchanged only have their membership refreshed.
        """
        seen = set()
        for token in self.chat_tokens:
            try:
                me = str(self._request("/v2/user", token=token)["user"]["id"])
                channels = list(self._cursor(f"/v3/workspaces/{self.team_id}/chat/channels",
                                             {"include_closed": "true"}, token))
            except RuntimeError as exc:
                print(f"ClickUp chat skipped for one token: {exc}", flush=True)
                continue
            for channel in channels:
                if channel["id"] in seen:
                    continue
                seen.add(channel["id"])
                yield from self._channel_records(channel, token, me, state)

    def channel_records(self, channel_id, token):
        """Read one chat channel/DM (all its messages) with a member's token, e.g. right after posting."""
        reader = str(self._request("/v2/user", token=token)["user"]["id"])
        channel = self._request(f"/v3/workspaces/{self.team_id}/chat/channels/{channel_id}", token=token)
        return self._channel_records(channel.get("data") or channel, token, reader, {})

    def _channel_records(self, channel, token, reader, state):
        cid = str(channel["id"])
        ctype = channel.get("type") or "CHANNEL"  # CHANNEL | DM | GROUP_DM
        base = f"/v3/workspaces/{self.team_id}/chat"
        members = list(self._cursor(f"{base}/channels/{cid}/members", token=token))
        for m in members:
            self.users.setdefault(str(m["id"]), m.get("username") or m.get("name") or m.get("email"))
        member_ids = [str(m["id"]) for m in members]
        title = channel.get("name")
        if ctype not in ("CHANNEL", "DM", "GROUP_DM"):
            # e.g. ASK_AI: a member's private chat with ClickUp's assistant.
            title = f"{ctype.replace('_', ' ').title()}: {self.users.get(reader) or reader}"
        elif ctype in ("DM", "GROUP_DM") or not title:
            names = ", ".join(sorted(str(self.users.get(i) or i) for i in member_ids))
            title = f"{'DM' if ctype == 'DM' else 'Group DM'}: {names}"
        channel_ref = SourceRef("clickup", self.account_id, "chat_channel", cid)
        latest = str(channel.get("latest_comment_at") or "")
        yield UnifiedRecord(
            source=channel_ref, kind="conversation", title=title, actor_id=_uid(channel.get("creator")),
            occurred_at=_timestamp(channel.get("created_at")),
            attributes={"channel_type": ctype, "visibility": channel.get("visibility"),
                        "collected_via": reader, "latest_comment_at": latest},
            raw=channel, participants=[(i, "member") for i in member_ids],
        )

        previous = state.get(cid) or {}
        if previous and previous.get("latest") == latest:
            return
        floor = previous.get("max_date", 0) - CHAT_LOOKBACK_MS if previous else 0
        max_date = previous.get("max_date", 0)
        for message in self._cursor(f"{base}/channels/{cid}/messages", {"content_format": "text/md"}, token):
            date = int(message.get("date") or 0)
            if date < floor:
                break  # newest first: everything after this is older than the re-read window
            max_date = max(max_date, date)
            yield from self._system_user(message.get("user_id"))
            yield self._message_record(message, channel_ref, ctype, token, reader)
            if int(message.get("replies_count") or 0):
                for reply in self._cursor(f"{base}/messages/{message['id']}/replies",
                                          {"content_format": "text/md"}, token):
                    max_date = max(max_date, int(reply.get("date") or 0))
                    yield from self._system_user(reply.get("user_id"))
                    yield self._message_record(reply, channel_ref, ctype, token, reader)
        state[cid] = {"latest": latest, "max_date": max_date}

    def _system_user(self, user_id):
        """ClickUp's own bots/assistant post with negative user ids; record them once as bot people."""
        uid = str(user_id or "")
        if uid.startswith("-") and uid not in self.users:
            self.users[uid] = "ClickUp Bot"
            yield UnifiedRecord(source=SourceRef("clickup", self.account_id, "user", uid), kind="person",
                                title="ClickUp Bot", attributes={"is_bot": True})

    def _message_record(self, message, channel_ref, ctype, token, reader):
        mid = str(message["id"])
        base = f"/v3/workspaces/{self.team_id}/chat/messages/{mid}"
        mentions = [str(u["id"]) for u in self._cursor(f"{base}/tagged_users", token=token) if u.get("id")]
        reactions = [(r.get("reaction"), str(r.get("user_id")))
                     for r in self._cursor(f"{base}/reactions", token=token)
                     if r.get("reaction") and r.get("user_id")]
        return UnifiedRecord(
            source=SourceRef("clickup", self.account_id, "chat_message", mid), kind="message",
            parent=channel_ref, actor_id=str(message["user_id"]) if message.get("user_id") else None,
            body=message.get("content"), occurred_at=_timestamp(message.get("date")),
            attributes={"channel_type": ctype, "reply_to": message.get("parent_message"),
                        "message_type": message.get("type"), "collected_via": reader},
            raw=message, mentions=list(dict.fromkeys(mentions)), reactions=list(dict.fromkeys(reactions)),
        )

    # ------------------------------------------------------------------ webhooks

    def ensure_webhook(self, endpoint):
        """Register a workspace webhook for all events, reusing one already pointing at `endpoint`."""
        hooks = self._request(f"/v2/team/{self.team_id}/webhook").get("webhooks", [])
        for hook in hooks:
            if hook.get("endpoint") == endpoint:
                return hook, False
        created = self._request(f"/v2/team/{self.team_id}/webhook", method="POST",
                                body={"endpoint": endpoint, "events": ["*"]})
        return created.get("webhook", created), True


def _uid(user):
    if isinstance(user, dict) and user.get("id") is not None:
        return str(user["id"])
    if isinstance(user, (int, str)) and str(user):
        return str(user)
    return None


def _reactions(value):
    """ClickUp comment reactions as (reaction, user id) pairs; tolerant of the undocumented item shape."""
    pairs = []
    for r in value or []:
        if isinstance(r, dict):
            user = _uid(r.get("user")) or _uid(r.get("userid")) or _uid(r.get("user_id"))
            if r.get("reaction") and user:
                pairs.append((r["reaction"], user))
    return list(dict.fromkeys(pairs))


def _describe(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, list):
        return ", ".join(_describe(v) for v in value) or "-"
    if isinstance(value, dict):
        for key in ("status", "username", "priority", "name", "email", "id"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return json.dumps(value)[:80]
    if isinstance(value, str) and value.isdigit() and len(value) == 13:
        return _date(value)
    return str(value)


def _timestamp(value):
    """ClickUp dates are Unix milliseconds (often as strings); tolerate ISO strings too."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None


def _date(value):
    stamp = _timestamp(value)
    return stamp.date().isoformat() if stamp else None
