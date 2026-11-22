"""The reconcile loop. Each tick reads what Restreamer Core and Facebook report
and makes the smallest change that moves them toward what should be true:

  pushing & no live video & no egress -> create a LIVE_NOW video + egress
  pushing & live video & no egress    -> recreate egress (e.g. after a restart)
  not pushing for > grace             -> end our live video, delete egress

Core and Facebook are the source of truth. The only memory kept between ticks is
when each input stopped pushing (lost on restart, which can only delay an end),
a short read cache for Facebook, retry backoff, and alert deduplication.

Facebook calls are rationed: a failing destination backs off exponentially, a
rate-limit error pauses all Facebook work, and a destination that has been
cleaned up after its push ended makes no calls until the next push.
"""

from __future__ import annotations

import logging
import time

from .core import Egress, egress_id
from .http import RateLimitError
from .log import event

log = logging.getLogger("sexton.reconcile")

# A destination that keeps failing is retried after tick, 2x tick, 4x tick ...
# up to this. Two broken destinations then cost ~24 calls/hour, not ~720.
MAX_BACKOFF_SECONDS = 300
# Facebook's rate limits are measured over a rolling hour; a quarter of that
# gives usage room to drain. A running egress keeps streaming meanwhile.
RATE_LIMIT_PAUSE_SECONDS = 900


class Reconciler:
    """Drives every input -> destination pair toward the desired state."""

    def __init__(self, config, core, graph, alerter, clock=time.time):
        self.config = config
        self.core = core
        self.graph = graph
        self.alerter = alerter
        self.clock = clock
        self._owner_by_title = {i.title: i.name for i in config.inputs}
        self._not_pushing_since = {}
        self._settled = set()  # "input:dest" keys cleaned up since their push ended
        self._fb_cache = {}  # target -> (fetched_at, [LiveVideo])
        self._failures = {}  # "input:dest" -> consecutive failure count
        self._retry_at = {}  # "input:dest" -> earliest time to try again
        self._fb_paused_until = 0.0

    def tick(self):
        """Runs one reconcile pass. Returns False if any step raised."""
        now = self.clock()
        try:
            publishing = self.core.publishing()
            egresses = self.core.egresses()
        except Exception as err:  # pylint: disable=broad-exception-caught
            event(log, logging.ERROR, "core_read_failed", error=describe(err))
            self.alerter.problem("core", "Sexton: Restreamer Core unreachable", describe(err))
            return False
        self.alerter.resolved("core")

        pushing = {i.name for i in self.config.inputs if i.rtmp_name in publishing}
        for inp in self.config.inputs:
            if inp.name in pushing:
                self._not_pushing_since.pop(inp.name, None)
                self._settled -= {f"{inp.name}:{d.name}" for d in inp.destinations}
            else:
                self._not_pushing_since.setdefault(inp.name, now)

        ok = True
        for inp in self.config.inputs:
            for dest in inp.destinations:
                ok = self._run_destination(inp, dest, pushing, egresses, now) and ok
        return ok

    def _run_destination(self, inp, dest, pushing, egresses, now):
        """Reconciles one input -> destination, isolating and rationing failures.
        Returns False if it failed or is waiting out a backoff/pause."""
        key = f"{inp.name}:{dest.name}"
        if key in self._settled:
            return True
        if now < self._fb_paused_until or now < self._retry_at.get(key, 0):
            return False
        try:
            if inp.name in pushing:
                self._reconcile_pushing(inp, dest, pushing, egresses, now)
            else:
                self._reconcile_idle(inp, dest, egresses, now)
        except RateLimitError as err:
            self._fb_paused_until = now + RATE_LIMIT_PAUSE_SECONDS
            event(log, logging.ERROR, "rate_limited", key=key, pause_seconds=RATE_LIMIT_PAUSE_SECONDS,
                  error=describe(err))
            self.alerter.problem(
                "facebook:rate_limit",
                "Sexton: Facebook rate limit",
                f"Pausing all Facebook calls for {RATE_LIMIT_PAUSE_SECONDS // 60} min "
                f"(running streams continue). {describe(err)}",
            )
            return False
        except Exception as err:  # pylint: disable=broad-exception-caught
            # One destination failing must never stop the others.
            failures = self._failures.get(key, 0) + 1
            self._failures[key] = failures
            delay = min(self.config.tick_seconds * 2 ** (failures - 1), MAX_BACKOFF_SECONDS)
            self._retry_at[key] = now + delay
            event(log, logging.ERROR, "reconcile_failed", key=key, failures=failures,
                  retry_in=delay, error=describe(err))
            self.alerter.problem(f"{key}:error", f"Sexton: {key} failed", describe(err))
            return False
        self._failures.pop(key, None)
        self._retry_at.pop(key, None)
        self.alerter.resolved(f"{key}:error")
        self.alerter.resolved("facebook:rate_limit")
        if inp.name not in pushing and self._grace_over(inp, now):
            self._settled.add(key)
        return True

    # -- pushing ---------------------------------------------------------------

    def _reconcile_pushing(self, inp, dest, pushing, egresses, now):
        key = f"{inp.name}:{dest.name}"
        pid = egress_id(inp.name, dest.name)
        egress = egresses.get(pid)
        # Read fresh only while verifying a new egress; otherwise (steady stream,
        # contention, a foreign video) every fb_poll_seconds is plenty.
        verifying = egress is not None and now - egress.created_at <= self.config.verify_seconds
        videos = self._videos(dest, now, 0 if verifying else self.config.fb_poll_seconds)

        owned, others, foreign = [], [], []
        for video in videos:
            owner = self._owner_by_title.get(video.title)
            (owned if owner == inp.name else others if owner else foreign).append(video)

        for video in others:
            owner = self._owner_by_title[video.title]
            if owner in pushing:
                self.alerter.problem(
                    f"{dest.target}:contention",
                    "Sexton: two inputs want the same target",
                    f"{inp.name} is pushing but {owner} is still live on {dest.target}; leaving {owner} alone.",
                )
                return
            # A previous church's video is still live on a shared target (the
            # profile): end it so this church can take over.
            event(log, logging.INFO, "handoff", target=dest.target, from_input=owner, to_input=inp.name)
            self.graph.end_live_video(video.id, dest.token)
            self._fb_cache.pop(dest.target, None)
            self._delete_egress(self._egress_for_target(owner, dest.target), egresses)
        self.alerter.resolved(f"{dest.target}:contention")

        if foreign and not owned:
            self.alerter.problem(
                f"{key}:foreign",
                f"Sexton: unknown live video on {dest.target}",
                f"{dest.target} is already live with {foreign[0].title!r} (not started by sexton); "
                f"not streaming {inp.name} there.",
            )
            return
        self.alerter.resolved(f"{key}:foreign")

        live = owned[0] if owned else None
        if len(owned) > 1:
            event(log, logging.WARNING, "multiple_live_videos", key=key, ids=[v.id for v in owned])

        if live is None:
            if egress is not None:
                age = now - egress.created_at
                if age <= self.config.verify_seconds:
                    return  # just started; Facebook may not report it as live yet
                self.alerter.problem(
                    f"{key}:not_live",
                    f"Sexton: {key} not live",
                    f"No live video on {dest.target} {int(age)}s after egress start; restarting it.",
                )
                self._delete_egress(pid, egresses)
                return
            self._start(inp, dest, pid, egresses, now)
            return

        if egress is None:
            event(log, logging.INFO, "recreate_egress", key=key, video_id=live.id)
            url = self.graph.ingest_url(live.id, dest.token)
            self._create_egress(inp, dest, pid, live.id, url, egresses, now)
            return

        if live.status == "LIVE":
            self.alerter.resolved(f"{key}:not_live")
        elif now - egress.created_at > self.config.verify_seconds:
            self.alerter.problem(
                f"{key}:not_live",
                f"Sexton: {key} not live",
                f"Video {live.id} is {live.status}, {int(now - egress.created_at)}s after egress start.",
            )

    def _start(self, inp, dest, pid, egresses, now):
        event(log, logging.INFO, "go_live", key=f"{inp.name}:{dest.name}", target=dest.target)
        video_id, url = self.graph.create_live_video(
            dest.target, dest.token, inp.title, inp.description, dest.privacy
        )
        self._fb_cache.pop(dest.target, None)
        # If this fails the video exists without a feed; next tick sees
        # "live & no egress" and recreates the egress from the video's URL.
        self._create_egress(inp, dest, pid, video_id, url, egresses, now)

    def _create_egress(self, inp, dest, pid, video_id, url, egresses, now):
        self.core.create_egress(pid, inp.rtmp_name, url)
        egresses[pid] = Egress(pid, now, url)
        self.core.set_metadata(pid, {"live_video_id": video_id, "target": dest.target})

    # -- not pushing -----------------------------------------------------------

    def _reconcile_idle(self, inp, dest, egresses, now):
        if not self._grace_over(inp, now):
            return
        pid = egress_id(inp.name, dest.name)
        for video in self._videos(dest, now, 0):
            if self._owner_by_title.get(video.title) == inp.name:
                event(log, logging.INFO, "end_live", key=f"{inp.name}:{dest.name}", video_id=video.id)
                self.graph.end_live_video(video.id, dest.token)
                self._fb_cache.pop(dest.target, None)
        if pid in egresses:
            self._delete_egress(pid, egresses)
        self.alerter.resolved(f"{inp.name}:{dest.name}:not_live")

    # -- helpers ---------------------------------------------------------------

    def _grace_over(self, inp, now):
        since = self._not_pushing_since.get(inp.name)
        return since is not None and now - since > self.config.grace_seconds

    def _videos(self, dest, now, max_age):
        """Active live videos on dest.target, reusing a read at most max_age old.
        max_age=0 still shares one read between inputs within the same tick."""
        cached = self._fb_cache.get(dest.target)
        if cached and now - cached[0] <= max_age:
            return cached[1]
        videos = self.graph.active_live_videos(dest.target, dest.token)
        self._fb_cache[dest.target] = (now, videos)
        return videos

    def _egress_for_target(self, input_name, target):
        for inp in self.config.inputs:
            if inp.name == input_name:
                for dest in inp.destinations:
                    if dest.target == target:
                        return egress_id(inp.name, dest.name)
        return None

    def _delete_egress(self, pid, egresses):
        if pid and pid in egresses:
            self.core.delete_egress(pid)
            egresses.pop(pid, None)


def describe(err):
    """A short, log-safe description of an exception."""
    return f"{type(err).__name__}: {err}"
