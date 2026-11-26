"""Gets the YouTube refresh token Sexton uses (one-time; repeat only if revoked).

  docker exec -it sexton python -m sexton.youtube_auth

Open the printed link in any browser, sign in as the account that manages the
YouTube channel (choose the channel if it is a Brand Account), and approve.
Google then sends the browser to http://127.0.0.1/..., which won't load; that's
expected. Copy that whole address from the address bar and paste it here.

Prints YT_REFRESH_TOKEN plus the channel it belongs to. Put it in the
container's variables and restart the container. Changes nothing by itself.

The OAuth client must be a "Desktop app" client, and the consent screen must be
"In production" (or Internal): refresh tokens from a "Testing" app die in 7 days.
YT_CLIENT_ID / YT_CLIENT_SECRET are read from the environment if set, else prompted for.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

from .http import ApiError, Http, json_body
from .renew_token import prompt
from .youtube import API_URL, SCOPE, TOKEN_URL, check_response

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REDIRECT_URI = "http://127.0.0.1:8765/"


def auth_url(client_id, state, challenge):
    """The consent page link. offline + consent guarantees a refresh token."""
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def code_from(pasted, state):
    """Pulls the authorization code out of the pasted redirect address."""
    query = parse_qs(urlparse(pasted.strip()).query)
    if "error" in query:
        raise ApiError(f"Google returned an error: {query['error'][0]}")
    if query.get("state", [None])[0] != state:
        raise ApiError("that address is not from this sign-in (state mismatch); start again")
    if not query.get("code"):
        raise ApiError("no code= in that address; paste the whole address bar contents")
    return query["code"][0]


def exchange(http, client_id, client_secret, code, verifier):
    """Swaps the authorization code for tokens; returns the refresh token."""
    resp = http.request("POST", TOKEN_URL, mutating=False, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })
    body = json_body(resp) or {}
    if not resp.ok:
        raise ApiError(f"token exchange failed: HTTP {resp.status_code} {body.get('error')}: "
                       f"{body.get('error_description', '')}")
    if not body.get("refresh_token"):
        raise ApiError("Google returned no refresh token; remove the app's access at "
                       "myaccount.google.com/permissions and try again")
    return body["refresh_token"], body.get("access_token")


def channel_title(http, access_token):
    """Name of the channel the token acts as, to confirm the right one was picked."""
    resp = http.request("GET", f"{API_URL}/channels", mutating=False,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params={"part": "snippet", "mine": "true"})
    items = check_response(resp, "GET /channels").get("items") or []
    return items[0]["snippet"]["title"] if items else None


def main():
    """Interactive sign-in. Prints the refresh token; changes nothing by itself."""
    client_id = prompt("YT_CLIENT_ID", "OAuth client ID")
    client_secret = prompt("YT_CLIENT_SECRET", "OAuth client secret", secret=True)

    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    print("\n1. Open this link in a browser and sign in as the YouTube channel's manager:\n")
    print(auth_url(client_id, state, challenge))
    print("\n2. If Google warns the app is unverified: Advanced -> Go to (your app). It's yours.")
    print("3. After approving, the page fails to load at 127.0.0.1. Copy the full address.\n")
    pasted = input("Paste the address here: ")

    http = Http()
    try:
        refresh_token, access_token = exchange(http, client_id, client_secret, code_from(pasted, state), verifier)
        channel = channel_title(http, access_token)
    except ApiError as err:
        print(f"\nFailed: {err}", file=sys.stderr)
        return 1

    print(f"\nChannel: {channel or '(none: this account has no YouTube channel)'}")
    print(f"YT_REFRESH_TOKEN={refresh_token}")
    print("\nPut that in the container's variables, then restart it.")
    return 0 if channel else 1


if __name__ == "__main__":
    sys.exit(main())
