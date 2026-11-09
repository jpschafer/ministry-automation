"""Reconcile logic against fake Core and Graph clients."""

from __future__ import annotations

import pytest

from sexton.http import ApiError, AuthError
from sexton.reconcile import Reconciler
from tests.fakes import PROFILE, FakeAlerter, FakeClock, FakeCore, FakeGraph, make_config

TICK = 10


class World:
    """A reconciler wired to fakes, with helpers to drive time."""

    def __init__(self, new_status="LIVE"):
        self.clock = FakeClock()
        self.core = FakeCore(self.clock)
        self.graph = FakeGraph(new_status)
        self.alerter = FakeAlerter()
        self.config = make_config()
        self.brain = Reconciler(self.config, self.core, self.graph, self.alerter, self.clock)

    def push(self, name, on=True):
        """Starts or stops an input's push."""
        channel = f"{name}-uuid.stream"
        if on:
            self.core.publishing_names.add(channel)
        else:
            self.core.publishing_names.discard(channel)

    def run(self, seconds=TICK):
        """Ticks every TICK seconds for `seconds`, starting with a tick now."""
        results = []
        for _ in range(max(1, int(seconds // TICK))):
            results.append(self.brain.tick())
            self.clock.advance(TICK)
        return all(results)

    def egress_ids(self):
        return sorted(self.core.egresses())

    def creates(self, target):
        return [c for c in self.graph.calls if c == ("create", target)]


def test_fresh_start_goes_live_on_page_and_profile_in_one_tick():
    w = World()
    w.push("colfax")
    assert w.run()
    assert len(w.graph.live("colfax-page")) == 1
    assert len(w.graph.live(PROFILE)) == 1
    assert w.egress_ids() == ["sexton:colfax:page", "sexton:colfax:profile"]
    assert w.graph.live("levels-page") == []
    # Steady state afterwards: no duplicate videos.
    assert w.run(300)
    assert len(w.creates("colfax-page")) == 1
    assert len(w.creates(PROFILE)) == 1
    assert not w.alerter.problems


def test_restart_mid_stream_recreates_egress_without_new_video():
    w = World()
    w.push("colfax")
    page_vid = w.graph.add("colfax-page", "Colfax Worship")
    profile_vid = w.graph.add(PROFILE, "Colfax Worship")
    assert w.run()
    assert w.creates("colfax-page") == [] and w.creates(PROFILE) == []
    assert ("ingest_url", page_vid) in w.graph.calls
    assert ("ingest_url", profile_vid) in w.graph.calls
    assert w.core.processes["sexton:colfax:page"].output == f"rtmps://fb/{page_vid}"
    assert w.egress_ids() == ["sexton:colfax:page", "sexton:colfax:profile"]


def test_push_drop_shorter_than_grace_keeps_everything():
    w = World()
    w.push("colfax")
    w.run(60)
    w.push("colfax", on=False)
    w.run(170)
    w.push("colfax")
    w.run(60)
    assert len(w.graph.live("colfax-page")) == 1
    assert len(w.graph.live(PROFILE)) == 1
    assert not [c for c in w.graph.calls if c[0] == "end"]
    assert not [c for c in w.core.calls if c[0] == "delete_egress"]
    assert len(w.creates("colfax-page")) == 1


def test_push_drop_longer_than_grace_ends_both_and_deletes_egress():
    w = World()
    w.push("colfax")
    w.run(60)
    w.push("colfax", on=False)
    w.run(180)
    assert len(w.graph.live("colfax-page")) == 1  # 180s is not *more* than grace yet
    w.run(20)
    assert w.graph.live("colfax-page") == []
    assert w.graph.live(PROFILE) == []
    assert w.egress_ids() == []
    # Settled: no more Facebook reads while idle.
    reads = len(w.graph.calls)
    w.run(600)
    assert len(w.graph.calls) == reads


def test_one_destination_failing_does_not_affect_the_other_and_retries():
    w = World()
    w.graph.fail[("create", "colfax-page")] = ApiError("boom")
    w.push("colfax")
    assert not w.run()
    assert w.graph.live("colfax-page") == []
    assert len(w.graph.live(PROFILE)) == 1
    assert "colfax:page:error" in w.alerter.active
    # Failure clears: next tick recovers on its own and the alert resolves.
    del w.graph.fail[("create", "colfax-page")]
    assert w.run()
    assert len(w.graph.live("colfax-page")) == 1
    assert "colfax:page:error" not in w.alerter.active
    assert len(w.creates(PROFILE)) == 1


def test_auth_error_alerts():
    w = World()
    w.graph.fail[("list", PROFILE)] = AuthError("code=190")
    w.push("levels")
    assert not w.run()
    assert "levels:profile:error" in w.alerter.active
    assert len(w.graph.live("levels-page")) == 1


def test_profile_handoff_between_churches():
    w = World()
    w.push("colfax")
    w.run(60)
    colfax_page = w.graph.live("colfax-page")
    colfax_profile = w.graph.live(PROFILE)
    # Colfax stops; Levels starts before Colfax's grace period is over.
    w.push("colfax", on=False)
    w.push("levels")
    w.run()
    assert w.graph.videos[colfax_profile[0]]["status"] == "VOD"
    profile_live = w.graph.live(PROFILE)
    assert len(profile_live) == 1 and w.graph.videos[profile_live[0]]["title"] == "Levels Worship"
    assert "sexton:colfax:profile" not in w.core.egresses()
    assert w.core.egresses()["sexton:levels:profile"].output == f"rtmps://fb/{profile_live[0]}"
    # Colfax's Page is untouched by the handoff; it ends after its own grace.
    assert w.graph.live("colfax-page") == colfax_page
    w.run(200)
    assert w.graph.live("colfax-page") == []
    # Ending Colfax after grace must not end Levels' profile video.
    assert w.graph.live(PROFILE) == profile_live
    assert len(w.creates(PROFILE)) == 2


def test_profile_contention_when_both_push_does_not_flap():
    w = World()
    w.push("colfax")
    w.run()
    w.push("levels")
    w.run(60)
    assert len(w.graph.live(PROFILE)) == 1
    assert w.graph.videos[w.graph.live(PROFILE)[0]]["title"] == "Colfax Worship"
    assert f"{PROFILE}:contention" in w.alerter.active
    assert len(w.graph.live("levels-page")) == 1


def test_pending_video_not_duplicated_then_alerts_if_never_live():
    w = World(new_status="UNPUBLISHED")  # Facebook never reports it as LIVE
    w.push("colfax")
    w.run(120)
    assert len(w.creates("colfax-page")) == 1  # pending, not duplicated
    assert "colfax:page:not_live" not in w.alerter.active
    w.run(20)
    assert "colfax:page:not_live" in w.alerter.active
    assert w.alerter.keys().count("colfax:page:not_live") == 1


def test_facebook_ending_video_mid_service_alerts_and_restarts():
    w = World()
    w.push("colfax")
    w.run(200)
    for vid in w.graph.live("colfax-page"):
        w.graph.videos[vid]["status"] = "LIVE_STOPPED"
    w.run()
    assert "colfax:page:not_live" in w.alerter.active
    w.run()
    assert len(w.creates("colfax-page")) == 2
    assert len(w.graph.live("colfax-page")) == 1
    w.run()
    assert "colfax:page:not_live" not in w.alerter.active  # recovered once seen LIVE
    assert len(w.creates(PROFILE)) == 1


def test_core_unreachable_alerts_and_touches_nothing():
    w = World()
    w.push("colfax")
    w.core.fail[("publishing", None)] = ApiError("connection refused")
    assert not w.run()
    assert "core" in w.alerter.active
    assert not w.graph.calls


def test_foreign_live_video_is_left_alone():
    w = World()
    foreign = w.graph.add("colfax-page", "Someone went live from their phone")
    w.push("colfax")
    w.run(60)
    assert w.graph.live("colfax-page") == [foreign]
    assert "colfax:page:foreign" in w.alerter.active
    assert "sexton:colfax:page" not in w.core.egresses()


def test_foreign_processes_are_never_touched():
    w = World()
    w.core.processes["restreamer-ui:egress:youtube:abc"] = object()
    w.push("colfax")
    w.run(60)
    w.push("colfax", on=False)
    w.run(300)
    assert "restreamer-ui:egress:youtube:abc" in w.core.processes
    assert all(pid.startswith("sexton:") for _, pid in w.core.calls)


@pytest.mark.parametrize("failing", ["page", "profile"])
def test_egress_create_failure_recovers_from_existing_video(failing):
    w = World()
    pid = f"sexton:colfax:{failing}"
    w.core.fail[("create_egress", pid)] = ApiError("core 500")
    w.push("colfax")
    assert not w.run()
    del w.core.fail[("create_egress", pid)]
    assert w.run()
    target = "colfax-page" if failing == "page" else PROFILE
    assert len(w.creates(target)) == 1  # reused the video created in the failed tick
    assert pid in w.core.egresses()
