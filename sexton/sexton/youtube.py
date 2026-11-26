"""Client for the YouTube Live Streaming API (part of the YouTube Data API v3).

A YouTube live video is a *broadcast* bound to a *stream* (the ingest key).
Sexton keeps one reusable stream titled "sexton" on the channel and creates a
fresh broadcast per service, so every service is its own video.

Calls used (developers.google.com/youtube/v3/live/docs), 1 quota unit each:
  GET    liveBroadcasts?broadcastStatus=active|upcoming   our live / pending broadcasts
  POST   liveBroadcasts                                   create (auto-start, no auto-stop)
  POST   liveBroadcasts/bind?id=&streamId=                attach the stream key
  POST   liveBroadcasts/transition?broadcastStatus=complete   end a live broadcast
  DELETE liveBroadcasts?id=                               drop one that never went live
  GET    liveStreams?mine=true / ?id=                     find the reusable stream
  POST   liveStreams                                      create it the first time

The destination's token is an OAuth refresh token; access tokens are fetched
from Google's token endpoint and cached until shortly before they expire.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from .http import ApiError, AuthError, RateLimitError, json_body
from .log import event
from .facebook import LiveVideo

log = logging.getLogger("sexton.youtube")

API_URL = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube"
STREAM_TITLE = "sexton"
DRY_RUN_VIDEO_ID = "dry-run"
DRY_RUN_URL = "rtmps://dry-run.invalid/live2/dry-run"

# lifeCycleStatus values that mean "on air" vs "never went on air yet".
ON_AIR = {"live", "liveStarting", "testing", "testStarting"}

RATE_LIMIT_REASONS = {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded",
                      "userRequestsExceedRateLimit", "dailyLimitExceeded"}
AUTH_REASONS = {"authError", "insufficientPermissions", "forbidden", "liveStreamingNotEnabled",
                "insufficientLivePermissions", "livePermissionBlocked"}
# A transition YouTube already made or is making on its own: the goal is met.
DONE_REASONS = {"redundantTransition", "liveBroadcastNotFound"}


class YouTubeClient:
    """Creates, inspects and ends broadcasts on the authorised channel.

    Same shape as GraphClient, so the reconciler treats both alike. `target`
    is always "mine" (the channel the refresh token belongs to)."""

    def __init__(self, http, client_id, client_secret, clock=time.time):
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self.clock = clock
        self._access = {}  # refresh token -> (access token, expires_at)
        self._stream = {}  # refresh token -> (stream id, ingest url)

    # -- auth --------------------------------------------------------------------

    def _access_token(self, refresh_token):
        cached = self._access.get(refresh_token)
        if cached and self.clock() < cached[1]:
            return cached[0]
        resp = self.http.request(
            "POST",
            TOKEN_URL,
            mutating=False,  # minting an access token changes nothing on the channel
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        body = json_body(resp)
        if not isinstance(body, dict):
            body = {}
        if not resp.ok or not body.get("access_token"):
            err = body.get("error", "") if isinstance(body, dict) else ""
            message = f"Google token refresh: HTTP {resp.status_code} {err}: {body.get('error_description', '')}"
            if err == "invalid_grant" or resp.status_code in (400, 401):
                # Revoked, expired (7-day "Testing" apps), or wrong client: needs a human.
                raise AuthError(message + " (re-run python -m sexton.youtube_auth)", status=resp.status_code)
            raise ApiError(message, status=resp.status_code)
        token = body["access_token"]
        self._access[refresh_token] = (token, self.clock() + float(body.get("expires_in", 3600)) - 120)
        return token

    def _call(self, method, path, refresh_token, *, mutating, params=None, json=None):
        for attempt in (1, 2):
            resp = self.http.request(
                method,
                f"{API_URL}/{path}",
                mutating=mutating,
                headers={"Authorization": f"Bearer {self._access_token(refresh_token)}"},
                params=params,
                json=json,
            )
            if resp is None:  # dry-run skipped it
                return None
            if resp.status_code == 401 and attempt == 1:
                self._access.pop(refresh_token, None)  # access token went stale early
                continue
            return check_response(resp, f"{method} /{path}")
        raise AuthError(f"YouTube rejected fresh credentials for {method} /{path}")

    # -- reconciler interface ----------------------------------------------------

    def active_live_videos(self, target, token):  # pylint: disable=unused-argument  # always "mine"
        """Our channel's event broadcasts that are on air *or* still pending.

        Pending (created/ready) ones are included so that a broadcast waiting
        for its first frames isn't duplicated, and so a leftover one gets
        cleaned up after the service. Persistent "Stream now" broadcasts are
        excluded by broadcastType=event."""
        videos = []
        for status in ("active", "upcoming"):
            body = self._call(
                "GET",
                "liveBroadcasts",
                token,
                mutating=False,
                params={"part": "id,snippet,status", "broadcastStatus": status,
                        "broadcastType": "event", "maxResults": 50},
            )
            for item in body.get("items", []):
                life = (item.get("status") or {}).get("lifeCycleStatus", "")
                if life in ("complete", "revoked"):
                    continue
                videos.append(LiveVideo(item["id"], "LIVE" if life == "live" else life,
                                        (item.get("snippet") or {}).get("title", "")))
        return videos

    def create_live_video(self, target, token, title, description, privacy=None):  # pylint: disable=unused-argument
        """Creates a broadcast bound to the reusable stream. Returns (id, ingest url).
        It goes live by itself when the egress starts sending (enableAutoStart)."""
        stream_id, url = self._reusable_stream(token)
        event(log, logging.INFO, "create_broadcast", title=title, privacy=privacy or "public")
        start = datetime.fromtimestamp(self.clock(), timezone.utc) + timedelta(seconds=60)
        body = self._call(
            "POST",
            "liveBroadcasts",
            token,
            mutating=True,
            params={"part": "id,snippet,status,contentDetails"},
            json={
                "snippet": {"title": title[:100], "description": description,
                            "scheduledStartTime": start.strftime("%Y-%m-%dT%H:%M:%SZ")},
                "status": {"privacyStatus": privacy or "public", "selfDeclaredMadeForKids": False},
                "contentDetails": {
                    "enableAutoStart": True,
                    # Sexton ends it after its own grace period; YouTube's ~1 min
                    # auto-stop would split a service on a short drop.
                    "enableAutoStop": False,
                    "monitorStream": {"enableMonitorStream": False},
                },
            },
        )
        if body is None:
            return DRY_RUN_VIDEO_ID, DRY_RUN_URL
        if not body.get("id"):
            raise ApiError("liveBroadcasts.insert returned no id")
        self._call("POST", "liveBroadcasts/bind", token, mutating=True,
                   params={"id": body["id"], "streamId": stream_id, "part": "id"})
        return body["id"], url

    def ingest_url(self, video_id, token):
        """Ingest URL of the stream an existing broadcast is bound to."""
        if video_id == DRY_RUN_VIDEO_ID:
            return DRY_RUN_URL
        body = self._call("GET", "liveBroadcasts", token, mutating=False,
                          params={"part": "contentDetails", "id": video_id})
        items = body.get("items") or []
        stream_id = ((items[0].get("contentDetails") or {}).get("boundStreamId")) if items else None
        if not stream_id:
            # Not bound (e.g. bind failed mid-create): bind it to ours.
            stream_id, url = self._reusable_stream(token)
            self._call("POST", "liveBroadcasts/bind", token, mutating=True,
                       params={"id": video_id, "streamId": stream_id, "part": "id"})
            return url
        body = self._call("GET", "liveStreams", token, mutating=False,
                          params={"part": "id,cdn", "id": stream_id})
        items = body.get("items") or []
        if not items:
            raise ApiError(f"broadcast {video_id} is bound to stream {stream_id}, which was not found")
        return _ingest(items[0])

    def end_live_video(self, video_id, token):
        """Completes an on-air broadcast (it stays as a video); deletes one that never aired."""
        event(log, logging.INFO, "end_broadcast", video_id=video_id)
        body = self._call("GET", "liveBroadcasts", token, mutating=False,
                          params={"part": "status", "id": video_id})
        items = body.get("items") or []
        if not items:
            return
        life = (items[0].get("status") or {}).get("lifeCycleStatus", "")
        try:
            if life in ON_AIR:
                self._call("POST", "liveBroadcasts/transition", token, mutating=True,
                           params={"broadcastStatus": "complete", "id": video_id, "part": "id"})
            elif life not in ("complete", "revoked"):
                self._call("DELETE", "liveBroadcasts", token, mutating=True, params={"id": video_id})
        except ApiError as err:
            if err.code not in DONE_REASONS:
                raise

    # -- helpers -----------------------------------------------------------------

    def _reusable_stream(self, token):
        """(id, ingest url) of the channel's "sexton" stream, creating it once."""
        if token in self._stream:
            return self._stream[token]
        page = None
        while True:
            params = {"part": "id,snippet,cdn", "mine": "true", "maxResults": 50}
            if page:
                params["pageToken"] = page
            body = self._call("GET", "liveStreams", token, mutating=False, params=params)
            for item in body.get("items", []):
                if (item.get("snippet") or {}).get("title") == STREAM_TITLE:
                    self._stream[token] = (item["id"], _ingest(item))
                    return self._stream[token]
            page = body.get("nextPageToken")
            if not page:
                break
        event(log, logging.INFO, "create_stream", title=STREAM_TITLE)
        body = self._call(
            "POST",
            "liveStreams",
            token,
            mutating=True,
            params={"part": "id,snippet,cdn,contentDetails"},
            json={
                "snippet": {"title": STREAM_TITLE, "description": "Managed by Sexton; reused by every service."},
                "cdn": {"ingestionType": "rtmp", "resolution": "variable", "frameRate": "variable"},
                "contentDetails": {"isReusable": True},
            },
        )
        if body is None:
            return DRY_RUN_VIDEO_ID, DRY_RUN_URL
        self._stream[token] = (body["id"], _ingest(body))
        return self._stream[token]


def _ingest(stream):
    info = (stream.get("cdn") or {}).get("ingestionInfo") or {}
    base = info.get("rtmpsIngestionAddress") or info.get("ingestionAddress")
    name = info.get("streamName")
    if not base or not name:
        raise ApiError(f"stream {stream.get('id')} has no ingestion address/stream name")
    return f"{base.rstrip('/')}/{name}"


def check_response(resp, what):
    """Returns the JSON body of a YouTube response, or raises a typed ApiError.
    The error's `code` is YouTube's reason string (e.g. "quotaExceeded")."""
    if resp.status_code == 204:
        return {}
    body = json_body(resp)
    if resp.ok and isinstance(body, dict) and "error" not in body:
        return body

    error = body.get("error", {}) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        error = {}
    reasons = [e.get("reason") for e in error.get("errors") or [] if isinstance(e, dict)]
    reason = reasons[0] if reasons else None
    message = f"YouTube {what}: HTTP {resp.status_code} {reason}: {error.get('message', resp.text[:200])}"
    if reason in RATE_LIMIT_REASONS or resp.status_code == 429:
        raise RateLimitError(message, status=resp.status_code, code=reason)
    if reason in AUTH_REASONS or resp.status_code == 401:
        raise AuthError(message, status=resp.status_code, code=reason)
    raise ApiError(message, status=resp.status_code, code=reason)
