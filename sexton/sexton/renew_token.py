"""Renews the Facebook user token (about every 60 days).

Facebook requires a person to log in for every new user token, so this can't be
fully automatic. Get a short-lived token from the Graph API Explorer, then run:

  docker exec -it sexton python -m sexton.renew_token

It exchanges that for a long-lived (~60 day) token, checks its permissions and
expiry, and prints it plus fresh Page tokens. Paste them into the container's
variables (Unraid template or sexton.env) and restart the container.

FB_APP_ID / FB_APP_SECRET are read from the environment if set, else prompted for.
"""

from __future__ import annotations

import getpass
import os
import sys
import time

from .facebook import GRAPH_URL, check_response
from .http import ApiError, Http

EXPLORER_URL = "https://developers.facebook.com/tools/explorer/"
REQUIRED_SCOPES = ("publish_video", "pages_show_list", "pages_read_engagement", "pages_manage_posts")


def graph_get(http, path, params):
    """GETs a Graph path and returns the checked JSON body."""
    resp = http.request("GET", f"{GRAPH_URL}/{path}", mutating=False, params=params)
    return check_response(resp, f"GET /{path}")


def exchange(http, app_id, app_secret, short_token):
    """Swaps a short-lived user token for a long-lived one."""
    body = graph_get(
        http,
        "oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
    )
    if not body.get("access_token"):
        raise ApiError("token exchange returned no access_token")
    return body["access_token"]


def inspect(http, app_id, app_secret, token):
    """Returns (scopes, expires_at) for token, as Facebook reports them."""
    body = graph_get(
        http, "debug_token", {"input_token": token, "access_token": f"{app_id}|{app_secret}"}
    )
    data = body.get("data") or {}
    if not data.get("is_valid"):
        raise ApiError(f"Facebook says the new token is not valid: {data.get('error', data)}")
    return set(data.get("scopes") or []), data.get("expires_at") or 0


def pages(http, token):
    """The Pages this user manages, with their Page tokens."""
    body = graph_get(http, "me/accounts", {"fields": "id,name,access_token", "access_token": token})
    return body.get("data") or []


def prompt(name, label, secret=False):
    """An env var's value, or a prompt for it (hidden when secret)."""
    value = os.environ.get(name)
    if value:
        return value
    value = getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")
    return value.strip()


def main():
    """Interactive renewal. Prints the new tokens; changes nothing by itself."""
    print(f"1. Open {EXPLORER_URL}")
    print("2. Pick your app, choose User Token, add these permissions:")
    print("   " + ", ".join(REQUIRED_SCOPES))
    print("3. Generate Access Token (approve both Pages), copy it, paste it below.\n")

    app_id = prompt("FB_APP_ID", "App ID")
    app_secret = prompt("FB_APP_SECRET", "App secret", secret=True)
    short_token = getpass.getpass("Short-lived token: ").strip()

    http = Http()
    try:
        token = exchange(http, app_id, app_secret, short_token)
        scopes, expires_at = inspect(http, app_id, app_secret, token)
        page_list = pages(http, token)
    except ApiError as err:
        print(f"\nFailed: {err}", file=sys.stderr)
        print("A code=190 error usually means the short-lived token expired; generate a new one.", file=sys.stderr)
        return 1

    missing = [s for s in REQUIRED_SCOPES if s not in scopes]
    expiry = time.strftime("%Y-%m-%d", time.localtime(expires_at)) if expires_at else "never"

    print(f"\nFB_TOKEN_USER={token}")
    print(f"  expires: {expiry}  (set a reminder about 10 days before)")
    for page in page_list:
        print(f"\n# {page.get('name')}  (page id {page.get('id')})")
        print(f"page token: {page.get('access_token')}")
    if missing:
        print(f"\nWARNING: token is missing permissions: {', '.join(missing)}", file=sys.stderr)
        return 1
    print("\nPaste these into the container's variables, then restart it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
