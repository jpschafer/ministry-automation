"""Shared HTTP plumbing: one session, timeouts on everything, typed errors,
and the dry-run guard that keeps mutating calls from leaving the building."""

from __future__ import annotations

import logging

import requests

from .log import event

log = logging.getLogger("sexton.http")

# (connect, read) seconds. Every call gets a timeout; nothing is fire-and-forget.
DEFAULT_TIMEOUT = (5, 20)


class ApiError(Exception):
    """An API call failed (non-2xx, error body, or unusable response)."""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class AuthError(ApiError):
    """The API rejected our credentials or permissions."""


class RateLimitError(ApiError):
    """The API says we are calling it too often."""


class Http:
    """Thin wrapper around a requests.Session that honours --dry-run."""

    def __init__(self, dry_run=False, timeout=DEFAULT_TIMEOUT):
        self.dry_run = dry_run
        self.timeout = timeout
        self.session = requests.Session()

    def request(self, method, url, *, mutating, **kwargs):
        """Sends a request. In dry-run mode a mutating request is logged
        and skipped, returning None; read-only requests always go out."""
        if mutating and self.dry_run:
            event(log, logging.INFO, "dry_run_skip", method=method, url=redact(url))
            return None
        try:
            return self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as err:
            raise ApiError(f"{method} {redact(url)} failed: {type(err).__name__}: {err}") from err


def redact(url):
    """Strips query strings so tokens and stream keys never reach the logs."""
    return url.split("?", 1)[0]
