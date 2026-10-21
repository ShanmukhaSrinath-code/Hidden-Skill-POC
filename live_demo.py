"""Live demo: play a production-incident story across Jira, Confluence and ClickUp, one step at a time.

Every step is performed with the acting person's own token, then that item is synced into PostgreSQL
immediately, so it appears on the dashboard (http://127.0.0.1:8000) within ~2 seconds.

    python live_demo.py                  # press Enter before each step (presenter mode)
    python live_demo.py --auto 10        # run hands-free, 10 seconds between steps
    python live_demo.py --cleanup        # delete everything a previous run created (Jira, ClickUp, database)

Jira/Confluence actions by people other than phani need JIRA_USER_TOKEN_<NAME>=<email>:<api token> in .env
(one Atlassian API token covers both); without one, that step is skipped, never posted under someone else's name.
"""

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from integrations import sync  # noqa: E402
from integrations.clickup import ClickUpConnector  # noqa: E402
from integrations.confluence import ConfluenceConnector  # noqa: E402
from integrations.jira import JiraConnector  # noqa: E402
from simulate_demo import ClickUp, Jira, call  # noqa: E402

LABEL = "live-demo"
LIST_NAME = "Live Demo"
STATE = Path(__file__).with_name("live_demo_state.json")

# Each step: (platform, actor, action, details). "ref" names an item so later steps can use it.
STEPS = [
    ("jira", "phani", "create_issue", {
        "ref": "bug", "type": "Bug", "summary": "Login latency spikes to 8s after release 2.3",
        "description": "Users report slow logins since this morning's release. @Tharun please take a look; @Ramu FYI.",
        "assignee": "tharun", "watchers": ["ramu", "pradeep"], "labels": ["incident", "auth"], "due": 1}),
    ("jira", "phani", "comment", {"ref": "bug",
        "text": "@Tharun can you check whether the new auth-service pods are being CPU throttled?"}),
    ("jira", "phani", "transition", {"ref": "bug", "status": "In Progress"}),
    ("jira", "tharun", "comment", {"ref": "bug",
        "text": "CPU limit was 200m and the pods were throttled. Raised it to 1 core; p95 dropped from 8s to 2s, but Redis timeouts remain."}),
    ("clickup", "tharun", "chat", {"ref": "dm_tr", "with": ["ramu"],
        "text": "Ramu, login is still slow and I see Redis timeouts in the auth service. Have you seen this before?"}),
    ("clickup", "ramu", "chat", {"ref": "dm_tr",
        "text": "Yes. The session cache runs KEYS * on every login, which blocks Redis. Switch to SCAN or a per-user key; that fixed the same issue in payments."}),
    ("clickup", "tharun", "chat", {"ref": "dm_tr", "text": "Makes sense. Can you take the fix? I'll pair with you on the load test."}),
    ("clickup", "ramu", "create_task", {
        "ref": "fix", "name": "Replace KEYS with SCAN in the session cache", "assignees": ["ramu"],
        "priority": 1, "due": 1, "tags": ["redis", "backend"],
        "description": "KEYS * blocks Redis under load and causes login timeouts."}),
    ("clickup", "tharun", "comment", {"ref": "fix", "key": "fix_thread",
        "text": "@Ramu thanks for picking this up. I'll run the load test as soon as it is deployed."}),
    ("clickup", "ramu", "status", {"ref": "fix", "status": "progress"}),
    ("clickup", "ramu", "reply", {"ref": "fix_thread",
        "text": "Deployed: per-user session keys plus SCAN for the admin view. Redis CPU dropped from 85% to 12%."}),
    ("clickup", "tharun", "reply", {"ref": "fix_thread", "text": "Load test at 2,000 logins per minute: p95 is 280ms. Great fix!"}),
    ("clickup", "ramu", "status", {"ref": "fix", "status": "done"}),
    ("jira", "phani", "comment", {"ref": "bug",
        "text": "Root cause was Redis KEYS in the session cache. Great catch @Ramu. @Pradeep please add this to the incident SOP."}),
    ("jira", "phani", "transition", {"ref": "bug", "status": "Done"}),
    ("confluence", "phani", "create_page", {"ref": "runbook", "title": "Runbook: login latency incident (Redis KEYS)",
        "paragraphs": [
            "Summary: logins slowed to 8s after release 2.3. @Tharun fixed the CPU throttling and @Ramu found the root cause.",
            "Root cause: the session cache ran KEYS * on every login, which blocked Redis.",
            "Fix: per-user session keys and SCAN for the admin view. Redis CPU dropped from 85% to 12%."]}),
    ("confluence", "phani", "comment", {"ref": "runbook", "key": "runbook_comment",
        "text": "@Pradeep please link this runbook from the incident SOP."}),
    ("confluence", "pradeep", "reply", {"ref": "runbook_comment", "text": "Linked it from the SOP and the on-call checklist."}),
    ("confluence", "phani", "inline_comment", {"ref": "runbook", "selection": "KEYS *",
        "text": "@Ramu should we also alert on Redis SLOWLOG entries?"}),
    ("confluence", "phani", "edit_page", {"ref": "runbook", "message": "Added prevention section",
        "append": "Prevention: alert when Redis SLOWLOG grows or CPU stays above 70% for 5 minutes."}),
    ("clickup", "phani", "chat", {"ref": "group", "with": ["nikhil", "pradeep"],
        "text": "Team, can we generate an LLM summary of incidents like today's for the weekly report? @Nikhil what do you think?"}),
    ("clickup", "nikhil", "chat", {"ref": "group",
        "text": "Yes. A small model can summarise the Jira and ClickUp threads and keep links to the sources. Draft by Friday."}),
    ("clickup", "pradeep", "chat_reply", {"ref": "group", "reply_to": 1,
        "text": "I'll add the summaries to the evaluation dashboard."}),
    ("jira", "phani", "create_issue", {
        "ref": "story", "type": "Story", "summary": "LLM-generated weekly incident summaries",
        "description": "Summarise incident threads from Jira and ClickUp with source links. @nikhil owns this.",
        "assignee": "nikhil", "watchers": ["pradeep"], "labels": ["llm", "reporting"], "due": 4}),
    ("clickup", "pradeep", "create_task", {
        "ref": "sop", "name": "Update incident SOP with the Redis KEYS finding", "assignees": ["pradeep"],
        "priority": 3, "due": 3, "tags": ["docs"], "description": "Add detection and fix steps from today's login incident."}),
    ("clickup", "ramu", "comment", {"ref": "sop", "key": "sop_thread",
        "text": "@Pradeep please include the SCAN vs KEYS note and the Redis CPU graph."}),
    ("clickup", "pradeep", "reply", {"ref": "sop_thread", "text": "Added both, with a link to the Jira bug."}),
]


def load_state():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    for key, empty in (("jira", []), ("confluence", []), ("tasks", []), ("messages", []), ("list_id", None)):
        state.setdefault(key, empty)
    return state


def save_state(state):
    STATE.write_text(json.dumps(state, indent=2))


class Demo:
    def __init__(self):
        self.jira, self.clickup = Jira(), ClickUp()
        self.jira_sync, self.clickup_sync, self.confluence_sync = JiraConnector(), ClickUpConnector(), ConfluenceConnector()
        list(self.clickup_sync.list_users())  # names for DM titles
        self.state = load_state()
        self.refs = {}  # ref -> Jira key / ClickUp task id / comment id / (channel id, [message ids])
        self.statuses = {}
        self.chat = f"{ClickUp.V3}/workspaces/{self.clickup.team}/chat"

    # ------------------------------------------------------------ syncing one item

    def _store(self, platform, account_id, records):
        for attempt in range(2):
            try:
                return sync.store(platform, account_id, LABEL, records() )
            except Exception as exc:  # e.g. a concurrent dashboard poll touching the same rows
                if attempt:
                    print(f"    (sync failed: {exc}; the dashboard's next poll will pick it up)")
                time.sleep(2)

    def sync_issue(self, key):
        self._store("jira", self.jira_sync.account_id, lambda: self.jira_sync.search_issues(f'key = "{key}"'))

    def sync_task(self, task_id):
        self._store("clickup", self.clickup_sync.account_id, lambda: self.clickup_sync.get_task(task_id))

    def sync_page(self, page_id):
        self._store("confluence", self.confluence_sync.account_id, lambda: self.confluence_sync.get_page(page_id))

    def sync_channel(self, channel_id, who):
        token = self.clickup.tokens[who]
        self._store("clickup", self.clickup_sync.account_id, lambda: self.clickup_sync.channel_records(channel_id, token))

    # ------------------------------------------------------------ Jira actions

    def jira_step(self, actor, action, d):
        j = self.jira
        if action == "create_issue":
            fields = {"project": {"key": j.project}, "summary": d["summary"], "issuetype": {"name": d["type"]},
                      "description": j.adf(d["description"]), "assignee": {"accountId": j.accounts[d["assignee"]]},
                      "labels": [LABEL, *d["labels"]],
                      "duedate": (datetime.now(timezone.utc) + timedelta(days=d["due"])).date().isoformat()}
            try:
                key = j.post("/rest/api/3/issue", {"fields": fields}, who=actor)["key"]
            except RuntimeError:
                fields["issuetype"] = {"name": "Task"}
                key = j.post("/rest/api/3/issue", {"fields": fields}, who=actor)["key"]
            for watcher in d["watchers"]:
                j.post(f"/rest/api/3/issue/{key}/watchers", j.accounts[watcher])
            self.refs[d["ref"]] = key
            self.state["jira"].append(key)
            return f"created {key} \"{d['summary']}\", assigned to {d['assignee']}, watchers {', '.join(d['watchers'])}"
        key = self.refs[d["ref"]]
        if action == "comment":
            if actor not in j.auth:
                return f"SKIPPED: {actor} has no JIRA_USER_TOKEN_{actor.upper()} in .env (would have commented on {key})"
            j.post(f"/rest/api/3/issue/{key}/comment", {"body": j.adf(d["text"])}, who=actor)
            return f"commented on {key}: \"{d['text']}\""
        if action == "transition":
            options = j.get(f"/rest/api/3/issue/{key}/transitions", who=actor)["transitions"]
            match = next((t for t in options if d["status"].lower() in (t["name"].lower(), t["to"]["name"].lower())), None)
            if not match:
                return f"no '{d['status']}' transition available on {key}"
            j.post(f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": match["id"]}}, who=actor)
            return f"moved {key} to {d['status']}"
        raise ValueError(action)

    # ------------------------------------------------------------ Confluence actions

    def confluence_step(self, actor, action, d):
        j = self.jira
        if actor not in j.auth:
            return f"SKIPPED: {actor} has no JIRA_USER_TOKEN_{actor.upper()} in .env (an Atlassian token covers Confluence too)"
        wiki, auth = f"{j.base}/wiki/api/v2", j.auth[actor]

        def body(*texts):
            doc = {"type": "doc", "version": 1, "content": [j.adf(t)["content"][0] for t in texts]}
            return {"representation": "atlas_doc_format", "value": json.dumps(doc)}

        if action == "create_page":
            space_key = (os.getenv("CONFLUENCE_SPACE_KEYS") or "Agents").split(",")[0].strip()
            space = call("GET", f"{wiki}/spaces?keys={space_key}", auth)["results"][0]
            page = call("POST", f"{wiki}/pages", auth, {"spaceId": space["id"], "status": "current",
                                                        "title": d["title"], "body": body(*d["paragraphs"])})
            self.refs[d["ref"]] = {"id": page["id"], "title": d["title"], "paragraphs": list(d["paragraphs"]), "version": 1}
            self.state["confluence"].append(page["id"])
            result = f"created page \"{d['title']}\" in space {space_key}"
        elif action == "edit_page":
            page = self.refs[d["ref"]]
            page["paragraphs"].append(d["append"])
            page["version"] += 1
            call("PUT", f"{wiki}/pages/{page['id']}", auth, {
                "id": page["id"], "status": "current", "title": page["title"], "body": body(*page["paragraphs"]),
                "version": {"number": page["version"], "message": d["message"]}})
            result = f"edited the page (version {page['version']}): \"{d['append']}\""
        elif action == "comment":
            page = self.refs[d["ref"]]
            comment = call("POST", f"{wiki}/footer-comments", auth, {"pageId": page["id"], "body": body(d["text"])})
            self.refs[d["key"]] = {"comment": comment["id"], "page": page}
            result = f"commented on the page: \"{d['text']}\""
        elif action == "reply":
            parent = self.refs[d["ref"]]
            page = parent["page"]
            call("POST", f"{wiki}/footer-comments", auth, {"parentCommentId": parent["comment"], "body": body(d["text"])})
            result = f"replied to the comment: \"{d['text']}\""
        elif action == "inline_comment":
            page = self.refs[d["ref"]]
            call("POST", f"{wiki}/inline-comments", auth, {
                "pageId": page["id"], "body": body(d["text"]),
                "inlineCommentProperties": {"textSelection": d["selection"], "textSelectionMatchCount": 1,
                                            "textSelectionMatchIndex": 0}})
            result = f"added an inline comment on \"{d['selection']}\": \"{d['text']}\""
        else:
            raise ValueError(action)
        self.sync_page(page["id"])
        return result

    # ------------------------------------------------------------ ClickUp actions

    def demo_list(self):
        c = self.clickup
        if not self.state.get("list_id"):
            spaces = c.req("GET", f"{c.V2}/team/{c.team}/space?archived=false", "phani")["spaces"]
            created = c.req("POST", f"{c.V2}/space/{spaces[0]['id']}/list", "phani", {"name": LIST_NAME})
            self.state["list_id"] = created["id"]
        if not self.statuses:
            statuses = c.req("GET", f"{c.V2}/list/{self.state['list_id']}", "phani").get("statuses", [])
            names = [s["status"] for s in statuses]
            self.statuses = {
                "progress": next((n for n in names if "progress" in n.lower()), names[1] if len(names) > 1 else names[0]),
                "done": next((s["status"] for s in statuses if s.get("type") in ("closed", "done")), names[-1]),
            }
        return self.state["list_id"]

    def clickup_step(self, actor, action, d):
        c = self.clickup
        if action == "create_task":
            task = c.req("POST", f"{c.V2}/list/{self.demo_list()}/task", actor, {
                "name": d["name"], "description": d["description"], "priority": d["priority"],
                "assignees": [c.ids[a] for a in d["assignees"]], "tags": d["tags"],
                "due_date": int((datetime.now(timezone.utc) + timedelta(days=d["due"])).timestamp() * 1000)})
            self.refs[d["ref"]] = task["id"]
            self.state["tasks"].append(task["id"])
            self.sync_task(task["id"])
            return f"created task \"{d['name']}\", assigned to {', '.join(d['assignees'])}"
        if action == "comment":
            task_id = self.refs[d["ref"]]
            comment = c.req("POST", f"{c.V2}/task/{task_id}/comment", actor, c.comment_body(d["text"]))
            self.refs[d["key"]] = (comment["id"], task_id)
            self.sync_task(task_id)
            return f"commented on task: \"{d['text']}\""
        if action == "reply":
            comment_id, task_id = self.refs[d["ref"]]
            c.req("POST", f"{c.V2}/comment/{comment_id}/reply", actor, c.comment_body(d["text"]))
            self.sync_task(task_id)
            return f"replied in the comment thread: \"{d['text']}\""
        if action == "status":
            task_id = self.refs[d["ref"]]
            self.demo_list()
            c.req("PUT", f"{c.V2}/task/{task_id}", actor, {"status": self.statuses[d["status"]]})
            self.sync_task(task_id)
            return f"moved task to '{self.statuses[d['status']]}'"
        if action in ("chat", "chat_reply"):
            if d["ref"] not in self.refs:
                channel = c.req("POST", f"{self.chat}/channels/direct_message", actor,
                                {"user_ids": [str(c.ids[o]) for o in d["with"]]})
                self.refs[d["ref"]] = ((channel.get("data") or channel)["id"], [])
            channel_id, posted = self.refs[d["ref"]]
            body = {"type": "message", "content": d["text"], "content_format": "text/md"}
            if action == "chat":
                message = c.req("POST", f"{self.chat}/channels/{channel_id}/messages", actor, body)
            else:
                message = c.req("POST", f"{self.chat}/messages/{posted[d['reply_to']]}/replies", actor, body)
            posted.append(message["id"])
            self.state["messages"].append({"id": message["id"], "author": actor})
            self.sync_channel(channel_id, actor)
            kind = "thread reply" if action == "chat_reply" else "message"
            return f"sent a chat {kind}: \"{d['text']}\""
        raise ValueError(action)

    # ------------------------------------------------------------ run

    def run(self, auto):
        print(f"Live demo: {len(STEPS)} steps. Open http://127.0.0.1:8000 to watch them arrive.\n")
        for number, (platform, actor, action, details) in enumerate(STEPS, 1):
            label = f"[{number}/{len(STEPS)}] {platform.upper():10} {actor}"
            if auto is None:
                input(f"{label}: press Enter for '{action}'... ")
            try:
                if platform == "jira":
                    result = self.jira_step(actor, action, details)
                    if details.get("ref") in self.refs:
                        self.sync_issue(self.refs[details["ref"]])
                elif platform == "confluence":
                    result = self.confluence_step(actor, action, details)
                else:
                    result = self.clickup_step(actor, action, details)
                print(f"{label} {result}\n")
            except Exception as exc:
                print(f"{label} FAILED: {exc}\n")
            finally:
                save_state(self.state)
            if auto is not None and number < len(STEPS):
                time.sleep(auto)
        print("Done. Everything above is now on the dashboard; reset with: python live_demo.py --cleanup")


def cleanup():
    state = load_state()
    jira, clickup = Jira(), ClickUp()
    jira_account, clickup_account = JiraConnector().account_id, ClickUpConnector().account_id
    found = jira.post("/rest/api/3/search/jql", {"jql": f"labels = {LABEL}", "maxResults": 100, "fields": ["summary"]})
    keys = sorted({i["key"] for i in found.get("issues", [])} | set(state["jira"]))
    for key in keys:
        try:
            call("DELETE", f"{jira.base}/rest/api/3/issue/{key}?deleteSubtasks=true", jira.auth["phani"])
        except RuntimeError as exc:
            print(f"  Jira {key}: {exc}")
        sync.delete_records("jira", jira_account, "issue", key)
    print(f"Jira: deleted {len(keys)} issues labelled {LABEL}")
    confluence_account = ConfluenceConnector().account_id
    for page_id in state["confluence"]:
        try:
            call("DELETE", f"{jira.base}/wiki/api/v2/pages/{page_id}", jira.auth["phani"])
        except RuntimeError as exc:
            print(f"  Confluence page {page_id}: {exc}")
        sync.delete_records("confluence", confluence_account, "page", page_id)
    print(f"Confluence: deleted {len(state['confluence'])} pages (moved to the space trash)")
    for message in state["messages"]:
        try:
            clickup.req("DELETE", f"{ClickUp.V3}/workspaces/{clickup.team}/chat/messages/{message['id']}", message["author"])
        except RuntimeError as exc:
            print(f"  ClickUp chat message {message['id']}: {exc}")
        sync.delete_records("clickup", clickup_account, "chat_message", message["id"])
    for task_id in state["tasks"]:
        sync.delete_records("clickup", clickup_account, "task", task_id)
    if state.get("list_id"):
        try:
            clickup.req("DELETE", f"{ClickUp.V2}/list/{state['list_id']}", "phani")
        except RuntimeError as exc:
            print(f"  ClickUp list: {exc}")
    print(f"ClickUp: deleted list '{LIST_NAME}' ({len(state['tasks'])} tasks) and {len(state['messages'])} chat messages")
    STATE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--auto", type=float, metavar="SECONDS", help="run without pausing, waiting SECONDS between steps")
    parser.add_argument("--cleanup", action="store_true", help="delete everything created by previous runs")
    args = parser.parse_args()
    if args.cleanup:
        cleanup()
    else:
        Demo().run(args.auto)


if __name__ == "__main__":
    main()
