"""Alerting (ntfy) and liveness (Healthchecks.io).

Problems are keyed so a condition that persists across ticks alerts once,
re-alerts at most every RESEND_SECONDS, and sends a "recovered" note when it clears.
"""

from __future__ import annotations

import logging
import time

from .http import ApiError
from .log import event

log = logging.getLogger("sexton.alerts")

RESEND_SECONDS = 3600


class Alerter:
    """Sends deduplicated alerts to an ntfy topic URL."""

    def __init__(self, http, url, token=None, clock=time.time):
        self.http = http
        self.url = url
        self.token = token
        self.clock = clock
        self._active = {}  # key -> last sent timestamp

    def problem(self, key, title, message):
        """Reports a problem. Sent now unless the same key was sent recently."""
        now = self.clock()
        last = self._active.get(key)
        event(log, logging.ERROR, "problem", key=key, title=title, message=message)
        if last is not None and now - last < RESEND_SECONDS:
            return
        if self._send(title, message, priority="high", tags="rotating_light"):
            self._active[key] = now
        elif last is None:
            # Keep it marked active so recovery still gets reported, but leave the
            # timestamp old enough that the next tick retries the send.
            self._active[key] = now - RESEND_SECONDS

    def resolved(self, key):
        """Clears a problem, announcing recovery if it had been reported."""
        if self._active.pop(key, None) is None:
            return
        event(log, logging.INFO, "resolved", key=key)
        self._send(f"Recovered: {key}", f"{key} is back to normal.", priority="default", tags="white_check_mark")

    def _send(self, title, message, priority, tags):
        if not self.url:
            event(log, logging.WARNING, "alert_not_sent", reason="NTFY_URL not set", title=title)
            return False
        headers = {"Title": title, "Priority": priority, "Tags": tags}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = self.http.request(
                "POST", self.url, mutating=True, data=message.encode("utf-8"), headers=headers
            )
        except ApiError as err:
            event(log, logging.ERROR, "alert_send_failed", title=title, error=str(err))
            return False
        if resp is None:  # dry-run
            return True
        if not resp.ok:
            event(log, logging.ERROR, "alert_send_failed", title=title, status=resp.status_code)
            return False
        return True


class Healthcheck:
    """Pings a Healthchecks.io check every loop so a dead Sexton is noticed."""

    def __init__(self, http, url):
        self.http = http
        self.url = url.rstrip("/") if url else None

    def ping(self, ok):
        """Pings success, or /fail if the tick hit errors. Returns True if delivered."""
        if not self.url:
            return False
        url = self.url if ok else f"{self.url}/fail"
        try:
            resp = self.http.request("POST", url, mutating=True)
        except ApiError as err:
            event(log, logging.ERROR, "healthcheck_failed", error=str(err))
            return False
        if resp is not None and not resp.ok:
            event(log, logging.ERROR, "healthcheck_failed", status=resp.status_code)
            return False
        return True
