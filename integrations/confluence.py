"""Confluence Cloud connector boundary. Confluence auth and API calls live only in this module.

Confluence shares the Atlassian site, API token and account ids with Jira (JIRA_BASE_URL, JIRA_EMAIL,
JIRA_API_TOKEN). A page or blog post is the conversation: its author, editors, commenters and inline-task
assignees are its participants, so they are the recipients of every comment and edit on it.
"""

import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from integrations.jira import _jira_text, _mentions, _timestamp
from integrations.records import SourceRef, UnifiedRecord


class ConfluenceConnector:
    def __init__(self, base_url=None, email=None, api_token=None, space_keys=None):
        self.base_url = (base_url or os.getenv("JIRA_BASE_URL", "")).rstrip("/")
        email = email or os.getenv("JIRA_EMAIL", "")
        api_token = api_token or os.getenv("JIRA_API_TOKEN", "")
        if not all((self.base_url, email, api_token)):
            raise ValueError("Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN (Confluence uses the same site)")
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
        self.account_id = self.base_url.lower()
        keys = space_keys if space_keys is not None else os.getenv("CONFLUENCE_SPACE_KEYS", "")
        self.space_keys = {k.strip() for k in keys.split(",") if k.strip()} if isinstance(keys, str) else set(keys)
        self.users = {}  # account id -> display name, to name @mentions Confluence stores without text

    def _request(self, path, params=None):
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        try:
            with urlopen(Request(url, headers=self.headers), timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Confluence API request failed: {path}: {exc}") from exc

    def _paged(self, path, params=None):
        """Follow v2 cursor pagination (`_links.next` is a site-relative URL)."""
        data = self._request(path, {**(params or {}), "limit": 250})
        while True:
            yield from data.get("results", [])
            following = (data.get("_links") or {}).get("next")
            if not following:
                break
            data = self._request(following)

    def _link(self, links):
        webui = (links or {}).get("webui")
        return f"{self.base_url}/wiki{webui}" if webui else None

    # ------------------------------------------------------------------ people

    def list_users(self):
        """Confluence and Jira share Atlassian accounts; read them once from the site's user directory."""
        start = 0
        while True:
            users = self._request("/rest/api/3/users/search", {"startAt": start, "maxResults": 100})
            for user in users:
                self.users[user["accountId"]] = user.get("displayName")
                yield UnifiedRecord(
                    source=SourceRef("confluence", self.account_id, "user", user["accountId"]),
                    kind="person", title=user.get("displayName"),
                    attributes={"email": (user.get("emailAddress") or "").lower() or None,
                                "account_type": user.get("accountType"),
                                "is_bot": user.get("accountType") != "atlassian"},
                    raw=user,
                )
            if len(users) < 100:
                break
            start += len(users)

    # ------------------------------------------------------------------ content

    def list_content(self):
        """Yield page/blog post, comment, edit and inline-task records for every readable space."""
        for space in self._paged("/wiki/api/v2/spaces"):
            if self.space_keys and space["key"] not in self.space_keys:
                continue
            for kind, path in (("page", "pages"), ("blogpost", "blogposts")):
                try:
                    items = list(self._paged(f"/wiki/api/v2/spaces/{space['id']}/{path}",
                                             {"body-format": "atlas_doc_format"}))
                except RuntimeError as exc:
                    print(f"Confluence space {space['key']} skipped: {exc}", flush=True)
                    break
                for item in items:
                    yield from self.content_records(item, kind, space)

    def _adf(self, item):
        """Parse an atlas_doc_format body and give @mentions their display name ("@Ramu")."""
        value = (((item.get("body") or {}).get("atlas_doc_format")) or {}).get("value")
        try:
            doc = json.loads(value) if value else None
        except ValueError:
            return None
        if doc is not None and not self.users:
            list(self.list_users())

        def walk(node):
            if isinstance(node, list):
                for child in node:
                    walk(child)
            elif isinstance(node, dict):
                attrs = node.get("attrs") or {}
                if node.get("type") == "mention" and not attrs.get("text") and attrs.get("id") in self.users:
                    attrs["text"] = f"@{self.users[attrs['id']]}"
                walk(node.get("content", []))

        walk(doc)
        return doc

    def get_page(self, page_id):
        page = self._request(f"/wiki/api/v2/pages/{page_id}", {"body-format": "atlas_doc_format"})
        space = self._request(f"/wiki/api/v2/spaces/{page['spaceId']}")
        return self.content_records(page, "page", space)

    def content_records(self, item, kind, space):
        item_id = str(item["id"])
        plural = "pages" if kind == "page" else "blogposts"
        ref = SourceRef("confluence", self.account_id, kind, item_id, self._link(item.get("_links")))

        comments = []  # (comment, object_type, reply_to)
        for object_type, path in (("comment", "footer-comments"), ("inline_comment", "inline-comments")):
            for comment in self._paged(f"/wiki/api/v2/{plural}/{item_id}/{path}", {"body-format": "atlas_doc_format"}):
                comments.append((comment, object_type, None))
                for reply in self._paged(f"/wiki/api/v2/{path}/{comment['id']}/children",
                                         {"body-format": "atlas_doc_format"}):
                    comments.append((reply, object_type, str(comment["id"])))
        versions = list(self._paged(f"/wiki/api/v2/{plural}/{item_id}/versions"))
        tasks = list(self._paged("/wiki/api/v2/tasks", {f"{kind}-id": item_id, "body-format": "atlas_doc_format"})) \
            if kind == "page" else []

        participants = [(item.get("authorId"), "author"), (item.get("ownerId"), "owner")]
        participants += [(v.get("authorId"), "editor") for v in versions]
        participants += [((c.get("version") or {}).get("authorId"), "commenter") for c, _, _ in comments]
        participants += [(t.get("assignedTo"), "assignee") for t in tasks]
        # People @mentioned on the page or in its comments are notified, so they are recipients too.
        doc = self._adf(item)
        participants += [(uid, "mentioned") for uid in _mentions(doc)]
        participants += [(uid, "mentioned") for c, _, _ in comments for uid in _mentions(self._adf(c))]
        participants = list(dict.fromkeys((uid, role) for uid, role in participants if uid))

        yield UnifiedRecord(
            source=ref, kind="document", title=item.get("title"), body=_jira_text(doc),
            occurred_at=_timestamp(item.get("createdAt")), actor_id=item.get("authorId"),
            attributes={"status": item.get("status"), "space_key": space.get("key"), "space_name": space.get("name"),
                        "version": (item.get("version") or {}).get("number"), "parent_key": item.get("parentId"),
                        "content_type": kind},
            raw={k: v for k, v in item.items() if k != "body"}, participants=participants, mentions=_mentions(doc),
        )
        for comment, object_type, reply_to in comments:
            body = self._adf(comment)
            props = comment.get("properties") or {}
            yield UnifiedRecord(
                source=SourceRef("confluence", self.account_id, object_type, str(comment["id"]),
                                 self._link(comment.get("_links")) or ref.url),
                kind="comment", parent=ref, actor_id=(comment.get("version") or {}).get("authorId"),
                occurred_at=_timestamp((comment.get("version") or {}).get("createdAt")), body=_jira_text(body),
                attributes={"reply_to": reply_to, "resolution": comment.get("resolutionStatus"),
                            "selection": props.get("inlineOriginalSelection") or props.get("inline-original-selection")},
                raw={k: v for k, v in comment.items() if k != "body"}, mentions=_mentions(body),
            )
        for version in versions:
            if version.get("number", 1) <= 1:
                continue  # version 1 is the page creation itself
            note = f": {version['message']}" if version.get("message") else ""
            yield UnifiedRecord(
                source=SourceRef("confluence", self.account_id, "edit", f"{item_id}:{version['number']}", ref.url),
                kind="event", parent=ref, actor_id=version.get("authorId"),
                occurred_at=_timestamp(version.get("createdAt")),
                body=f"edited the {kind} (version {version['number']}){note}",
                attributes={"version": version["number"], "minor_edit": version.get("minorEdit")}, raw=version,
            )
        for task in tasks:
            body = self._adf(task)
            assignee = task.get("assignedTo")
            yield UnifiedRecord(
                source=SourceRef("confluence", self.account_id, "task_item", f"{item_id}:{task['id']}", ref.url),
                kind="event", parent=ref, actor_id=task.get("createdBy"),
                occurred_at=_timestamp(task.get("createdAt")),
                body=f"task ({task.get('status', 'incomplete')}): {_jira_text(body) or ''}".strip(),
                attributes={"status": task.get("status"), "assignee_id": assignee, "due": task.get("dueAt"),
                            "completed_by": task.get("completedBy")},
                raw={k: v for k, v in task.items() if k != "body"},
                mentions=list(dict.fromkeys([*_mentions(body), *([assignee] if assignee else [])])),
            )
