"""Client for the Facebook Graph API live video endpoints.

Calls used (Live Video API guide, developers.facebook.com/docs/live-video-api):
  GET  /{target}/live_videos            find the target's active live video
  POST /{target}/live_videos            status=LIVE_NOW -> id + secure_stream_url
  GET  /{live_video_id}?fields=ingest_streams   stream URL of an existing video
  POST /{live_video_id}  end_live_video=true    ends the broadcast (status -> VOD)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .http import ApiError, AuthError, RateLimitError
from .log import event

log = logging.getLogger("sexton.facebook")

# Latest version in the Graph API changelog table as of 2026-11 (released 2026-07-29).
GRAPH_API_VERSION = "v26.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Statuses that count as "this target is live". A LIVE_NOW video may report a
# different status before its first frames arrive (unverified, see README); the
# reconciler treats a young egress as "pending" so that gap never causes a duplicate.
ACTIVE_STATUSES = ("LIVE",)

AUTH_ERROR_CODES = {102, 190, 200, 10}
RATE_LIMIT_ERROR_CODES = {4, 17, 32, 613}

DRY_RUN_VIDEO_ID = "dry-run"


@dataclass(frozen=True)
class LiveVideo:
    """The parts of a Graph LiveVideo the reconciler cares about."""

    id: str
    status: str
    title: str


class GraphClient:
    """Creates, inspects and ends live videos on Pages and the profile."""

    def __init__(self, http):
        self.http = http

    def _call(self, method, path, token, *, mutating, params=None, data=None):
        resp = self.http.request(
            method,
            f"{GRAPH_URL}/{path}",
            mutating=mutating,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            data=data,
        )
        if resp is None:  # dry-run skipped it
            return None
        return check_response(resp, f"{method} /{path}")

    def active_live_videos(self, target, token):
        """Live videos on target whose status is in ACTIVE_STATUSES."""
        body = self._call(
            "GET",
            f"{target}/live_videos",
            token,
            mutating=False,
            params={
                "broadcast_status": json.dumps(list(ACTIVE_STATUSES)),
                "fields": "id,status,title",
                "limit": 25,
            },
        )
        videos = []
        for item in body.get("data", []):
            # Filter again client-side so a missing/ignored server filter can't
            # make an ended video look live.
            if item.get("status") in ACTIVE_STATUSES:
                videos.append(LiveVideo(item["id"], item["status"], item.get("title", "")))
        return videos

    def create_live_video(self, target, token, title, description, privacy=None):
        """Creates a LIVE_NOW video. Returns (video_id, secure_stream_url)."""
        data = {"status": "LIVE_NOW", "title": title, "description": description}
        if privacy:
            data["privacy"] = json.dumps(privacy)
        event(log, logging.INFO, "create_live_video", target=target, title=title)
        body = self._call("POST", f"{target}/live_videos", token, mutating=True, data=data)
        if body is None:
            return DRY_RUN_VIDEO_ID, "rtmps://dry-run.invalid/rtmp/dry-run"
        if not body.get("id") or not body.get("secure_stream_url"):
            raise ApiError(f"live video create on {target} returned no id/secure_stream_url")
        return body["id"], body["secure_stream_url"]

    def ingest_url(self, video_id, token):
        """The secure RTMP URL to stream an existing live video to."""
        if video_id == DRY_RUN_VIDEO_ID:
            return "rtmps://dry-run.invalid/rtmp/dry-run"
        body = self._call("GET", video_id, token, mutating=False, params={"fields": "ingest_streams"})
        streams = body.get("ingest_streams") or []
        streams = sorted(streams, key=lambda s: not s.get("is_master", False))
        for stream in streams:
            if stream.get("secure_stream_url"):
                return stream["secure_stream_url"]
        raise ApiError(f"live video {video_id} has no ingest stream with a secure_stream_url")

    def end_live_video(self, video_id, token):
        """Ends the broadcast; Facebook keeps it as a VOD."""
        event(log, logging.INFO, "end_live_video", video_id=video_id)
        self._call("POST", video_id, token, mutating=True, data={"end_live_video": "true"})


def check_response(resp, what):
    """Returns the JSON body of a Graph response, or raises a typed ApiError."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.ok and isinstance(body, dict) and "error" not in body:
        return body

    error = body.get("error", {}) if isinstance(body, dict) else {}
    code = error.get("code")
    message = f"Graph {what}: HTTP {resp.status_code} code={code}: {error.get('message', resp.text[:200])}"
    if code in AUTH_ERROR_CODES or resp.status_code in (401, 403):
        raise AuthError(message, status=resp.status_code, code=code)
    if code in RATE_LIMIT_ERROR_CODES:
        raise RateLimitError(message, status=resp.status_code, code=code)
    raise ApiError(message, status=resp.status_code, code=code)
