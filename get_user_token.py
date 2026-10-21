"""Authorize the app as one Slack account and add that account's user token to .env.

Run once per account:
    python get_user_token.py

1. Open the printed link in a browser window signed in to Slack as that account (use a private window).
2. Click Allow. The browser lands on https://localhost/?code=... and shows a "can't connect" page; that is expected.
3. Copy the full address from the address bar and paste it here.

Needs SLACK_CLIENT_ID and SLACK_CLIENT_SECRET in .env, and https://localhost/ listed as a redirect URL.
"""

import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)

REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI", "https://localhost/")
USER_SCOPES = [
    "channels:history", "groups:read", "groups:history", "mpim:read",
    "mpim:history", "im:read", "im:history", "users:read",
]


def add_token_to_env(token):
    text = ENV_PATH.read_text()
    match = re.search(r"^SLACK_USER_TOKENS=(.*)$", text, flags=re.M)
    tokens = [t.strip() for t in (match.group(1) if match else "").split(",") if t.strip()]
    if token in tokens:
        return False
    line = "SLACK_USER_TOKENS=" + ",".join(tokens + [token])
    text = text.replace(match.group(0), line) if match else text.rstrip("\n") + "\n" + line + "\n"
    ENV_PATH.write_text(text)
    return True


def main():
    client_id = os.getenv("SLACK_CLIENT_ID")
    client_secret = os.getenv("SLACK_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET in .env (app settings > Basic Information).")

    state = secrets.token_urlsafe(16)
    url = "https://slack.com/oauth/v2/authorize?" + urlencode({
        "client_id": client_id,
        "user_scope": ",".join(USER_SCOPES),
        "redirect_uri": REDIRECT_URI,
        "state": state,
    })
    print("Open this link while signed in to Slack as the account to authorize:\n")
    print(url)
    pasted = input("\nAfter clicking Allow, paste the full address from the browser: ").strip()

    query = parse_qs(urlparse(pasted).query)
    if "error" in query:
        sys.exit(f"Slack returned: {query['error'][0]}")
    if query.get("state", [None])[0] != state:
        sys.exit("State mismatch: paste the address from this run's link.")
    code = query.get("code", [None])[0]
    if not code:
        sys.exit("No code found in the pasted address.")

    try:
        resp = WebClient().oauth_v2_access(
            client_id=client_id, client_secret=client_secret, code=code, redirect_uri=REDIRECT_URI
        )
    except SlackApiError as e:
        sys.exit(f"Token exchange failed: {e.response['error']}")

    authed = resp["authed_user"]
    token = authed["access_token"]
    user = WebClient(token=token).auth_test()
    added = add_token_to_env(token)
    print(f"\nAuthorized as {user['user']} ({user['user_id']}). "
          f"{'Added to' if added else 'Already in'} SLACK_USER_TOKENS.")


if __name__ == "__main__":
    main()
