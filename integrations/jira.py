"""Jira Cloud connector boundary. Jira auth and API calls live only in this module."""

import base64
import json
import os
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from integrations.records import SourceRef, UnifiedRecord


class JiraConnector:
    def __init__(self, base_url=None, email=None, api_token=None):
        self.base_url = (base_url or os.getenv("JIRA_BASE_URL", "")).rstrip("/")
        self.email = email or os.getenv("JIRA_EMAIL", "")
        self.api_token = api_token or os.getenv("JIRA_API_TOKEN", "")
        if not all((self.base_url, self.email, self.api_token)):
            raise ValueError("Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN")
        token = base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
        self.account_id = self.base_url.lower()

    def _request(self, path, params=None, body=None):
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urlencode(params)
        headers = dict(self.headers)
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode()
        request = Request(url, data=payload, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Jira API request failed: {exc}") from exc

    def _paged(self, path, key, params=None):
        """Yield items from Jira's startAt/maxResults paged GET endpoints."""
        start = 0
        while True:
            data = self._request(path, {**(params or {}), "startAt": start, "maxResults": 100})
            values = data.get(key, [])
            yield from values
            start += len(values)
            if not values or data.get("isLast") or start >= data.get("total", start):
                break

    def list_users(self):
        """Yield every user on the Jira site as a person record (email only if their profile shares it)."""
        start = 0
        while True:
            users = self._request("/rest/api/3/users/search", {"startAt": start, "maxResults": 100})
            for user in users:
                yield UnifiedRecord(
                    source=SourceRef("jira", self.account_id, "user", user["accountId"]),
                    kind="person", title=user.get("displayName"),
                    attributes={"email": (user.get("emailAddress") or "").lower() or None,
                                "account_type": user.get("accountType"), "active": user.get("active"),
                                "is_bot": user.get("accountType") != "atlassian"},
                    raw=user,
                )
            if len(users) < 100:
                break
            start += len(users)

    def search_issues(self, jql, max_results=100):
        """Yield issue, comment and change-history records for issues matched by JQL.

        An issue is the conversation: its reporter, creator, assignee, watchers and commenters
        are participants, so they are the recipients of every comment and change on it.
        """
        next_page_token = None
        while True:
            body = {"jql": jql, "maxResults": min(max_results, 100),
                    "fields": ["summary", "description", "status", "assignee", "reporter", "creator",
                               "project", "issuetype", "priority", "duedate", "labels", "parent",
                               "created", "updated"]}
            if next_page_token:
                body["nextPageToken"] = next_page_token
            data = self._request("/rest/api/3/search/jql", body=body)
            issues = data.get("issues", [])
            for issue in issues:
                yield from self._issue_records(issue)
            next_page_token = data.get("nextPageToken")
            if not issues or data.get("isLast", not next_page_token) or not next_page_token:
                break

    def _issue_records(self, issue):
        key = issue["key"]
        fields = issue.get("fields", {})
        issue_ref = SourceRef("jira", self.account_id, "issue", key, f"{self.base_url}/browse/{key}")
        comments = list(self._paged(f"/rest/api/3/issue/{key}/comment", "comments", {"orderBy": "created"}))
        history = list(self._paged(f"/rest/api/3/issue/{key}/changelog", "values"))
        watchers = self._request(f"/rest/api/3/issue/{key}/watchers").get("watchers", [])

        participants = [((fields.get(role) or {}).get("accountId"), role)
                        for role in ("reporter", "creator", "assignee")]
        participants += [(w.get("accountId"), "watcher") for w in watchers]
        participants += [((c.get("author") or {}).get("accountId"), "commenter") for c in comments]
        participants = list(dict.fromkeys((uid, role) for uid, role in participants if uid))

        reporter = fields.get("reporter") or fields.get("creator") or {}
        yield UnifiedRecord(
            source=issue_ref, kind="task", title=fields.get("summary"),
            body=_jira_text(fields.get("description")),
            occurred_at=_timestamp(fields.get("created")), actor_id=reporter.get("accountId"),
            attributes={"status": (fields.get("status") or {}).get("name"),
                        "issue_type": (fields.get("issuetype") or {}).get("name"),
                        "priority": (fields.get("priority") or {}).get("name"),
                        "project_key": (fields.get("project") or {}).get("key"),
                        "assignee_id": (fields.get("assignee") or {}).get("accountId"),
                        "due_date": fields.get("duedate"), "labels": fields.get("labels") or [],
                        "parent_key": (fields.get("parent") or {}).get("key"),
                        "created": fields.get("created"), "updated": fields.get("updated")},
            raw=issue, participants=participants, mentions=_mentions(fields.get("description")),
        )
        for comment in comments:
            comment_id = str(comment["id"])
            yield UnifiedRecord(
                source=SourceRef("jira", self.account_id, "comment", comment_id,
                                 f"{self.base_url}/browse/{key}?focusedCommentId={comment_id}"),
                kind="comment", actor_id=(comment.get("author") or {}).get("accountId"),
                occurred_at=_timestamp(comment.get("created")), parent=issue_ref,
                body=_jira_text(comment.get("body")), attributes={"updated": comment.get("updated")},
                raw=comment, mentions=_mentions(comment.get("body")),
            )
        for change in history:
            # Jira logs no-op entries such as "status: To Do -> To Do" when an issue is created.
            items = [i for i in change.get("items", [])
                     if (i.get("from"), i.get("fromString")) != (i.get("to"), i.get("toString"))]
            if not items:
                continue
            yield UnifiedRecord(
                source=SourceRef("jira", self.account_id, "change", str(change["id"]),
                                 f"{self.base_url}/browse/{key}"),
                kind="event", actor_id=(change.get("author") or {}).get("accountId"),
                occurred_at=_timestamp(change.get("created")), parent=issue_ref,
                body="; ".join(f"{i.get('field')}: {i.get('fromString') or '-'} -> {i.get('toString') or '-'}"
                               for i in items),
                attributes={"fields": [i.get("field") for i in items]}, raw=change,
            )


def _jira_text(value):
    """Flatten Jira's Atlassian Document Format into readable text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("type") == "text":
            return value.get("text", "")
        if value.get("type") == "mention":
            return (value.get("attrs") or {}).get("text")
        return " ".join(filter(None, (_jira_text(child) for child in value.get("content", []))))
    if isinstance(value, list):
        return " ".join(filter(None, (_jira_text(child) for child in value)))
    return None


def _mentions(value):
    """Account ids of @mentions in Atlassian Document Format, in order of first appearance."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            if node.get("type") == "mention" and (node.get("attrs") or {}).get("id"):
                found.append(node["attrs"]["id"])
            walk(node.get("content", []))

    walk(value)
    return list(dict.fromkeys(found))


def _timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
