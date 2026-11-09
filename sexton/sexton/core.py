"""Client for the datarhei Restreamer Core API (v3).

Endpoints used (from the Core swagger, docs/swagger.json):
  POST   /api/login                          username/password -> access_token
  GET    /api/v3/rtmp                        channels currently publishing
  GET    /api/v3/process                     list processes
  POST   /api/v3/process                     create a process
  DELETE /api/v3/process/{id}                delete a process
  PUT    /api/v3/process/{id}/metadata/{key} store JSON with a process
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .http import ApiError, AuthError
from .log import event

log = logging.getLogger("sexton.core")

# Every process Sexton owns starts with this. Anything else (the Restreamer UI's
# own ingest and YouTube egress) is never created, changed or deleted.
EGRESS_PREFIX = "sexton:"
METADATA_KEY = "sexton"


@dataclass(frozen=True)
class Egress:
    """A Sexton-owned egress process as Core reports it."""

    id: str
    created_at: float
    output: str


def egress_id(input_name, dest_name):
    """The Core process id for one input -> destination egress."""
    return f"{EGRESS_PREFIX}{input_name}:{dest_name}"


def _require_owned(process_id):
    if not process_id.startswith(EGRESS_PREFIX):
        raise ValueError(f"refusing to touch process {process_id!r}: not owned by sexton")


def channel_name(path):
    """Reduces an RTMP channel path like /live/<id>.stream to <id>.stream."""
    return path.rstrip("/").rsplit("/", 1)[-1]


class CoreClient:
    """Reads publishing state and manages Sexton's egress processes."""

    def __init__(self, config, http):
        self.config = config
        self.http = http
        self._access_token = None

    def _login(self):
        resp = self.http.request(
            "POST",
            f"{self.config.url}/api/login",
            mutating=False,  # logging in changes nothing, so it runs in dry-run too
            json={"username": self.config.username, "password": self.config.password},
        )
        body = _check(resp, "login")
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise AuthError("Core login returned no access_token")
        self._access_token = token

    def _request(self, method, path, *, mutating=False, allow_404=False, **kwargs):
        uses_auth = self.config.username is not None
        if uses_auth and self._access_token is None:
            self._login()
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access_token}"} if uses_auth else {}
            resp = self.http.request(
                method, f"{self.config.url}{path}", mutating=mutating, headers=headers, **kwargs
            )
            if resp is None:  # dry-run skipped it
                return None
            if resp.status_code == 401 and uses_auth and attempt == 1:
                # Access tokens expire; log in again once and retry.
                self._login()
                continue
            if resp.status_code == 404 and allow_404:
                return None
            return _check(resp, f"{method} {path}")
        raise AuthError(f"Core rejected credentials for {method} {path}")

    def publishing(self):
        """Names of the RTMP channels with an active push, e.g. {'<uuid>.stream'}."""
        body = self._request("GET", "/api/v3/rtmp")
        if not isinstance(body, list):
            raise ApiError(f"unexpected /api/v3/rtmp response: {body!r:.200}")
        return {channel_name(c["name"]) for c in body if isinstance(c, dict) and c.get("name")}

    def egresses(self):
        """Sexton-owned egress processes, keyed by process id."""
        body = self._request("GET", "/api/v3/process", params={"filter": "config"})
        if not isinstance(body, list):
            raise ApiError(f"unexpected /api/v3/process response: {body!r:.200}")
        result = {}
        for proc in body:
            pid = proc.get("id", "")
            if not pid.startswith(EGRESS_PREFIX):
                continue
            if proc.get("created_at") is None:
                # Age drives the go-live verification; guessing it could tear down
                # a healthy stream, so fail loudly instead.
                raise ApiError(f"process {pid} has no created_at")
            outputs = (proc.get("config") or {}).get("output") or [{}]
            result[pid] = Egress(pid, float(proc["created_at"]), outputs[0].get("address", ""))
        return result

    def create_egress(self, process_id, rtmp_name, stream_url):
        """Creates a stream-copy process from the input channel to stream_url."""
        _require_owned(process_id)
        process = {
            "type": "ffmpeg",
            "id": process_id,
            "reference": "sexton",
            "input": [{"id": "input_0", "address": f"{{rtmp,name={rtmp_name}}}", "options": []}],
            "output": [
                {"id": "output_0", "address": stream_url, "options": ["-c", "copy", "-f", "flv"]}
            ],
            "options": ["-loglevel", "level+info", "-err_detect", "ignore_err"],
            "autostart": True,
            "reconnect": True,
            "reconnect_delay_seconds": 5,
            "stale_timeout_seconds": 30,
        }
        event(log, logging.INFO, "create_egress", process_id=process_id, input=rtmp_name)
        self._request("POST", "/api/v3/process", mutating=True, json=process)

    def delete_egress(self, process_id):
        """Deletes a Sexton-owned process. Already gone counts as success."""
        _require_owned(process_id)
        event(log, logging.INFO, "delete_egress", process_id=process_id)
        self._request("DELETE", f"/api/v3/process/{process_id}", mutating=True, allow_404=True)

    def set_metadata(self, process_id, data):
        """Stores debugging info with the process. Never read back for decisions."""
        _require_owned(process_id)
        self._request(
            "PUT", f"/api/v3/process/{process_id}/metadata/{METADATA_KEY}", mutating=True, json=data
        )


def _check(resp, what):
    if resp.status_code in (401, 403):
        raise AuthError(f"Core {what}: HTTP {resp.status_code}", status=resp.status_code)
    if not resp.ok:
        raise ApiError(f"Core {what}: HTTP {resp.status_code}: {resp.text[:200]}", status=resp.status_code)
    try:
        return resp.json()
    except ValueError:
        # Some endpoints (DELETE, command) answer with a bare string.
        return resp.text
