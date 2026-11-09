"""In-memory stand-ins for Core, Graph, the alerter and the clock."""

# Fakes mirror the real clients' signatures, so some arguments go unused.
# pylint: disable=unused-argument

from __future__ import annotations

from sexton.config import Config, CoreConfig, Destination, Input
from sexton.core import Egress
from sexton.facebook import ACTIVE_STATUSES, LiveVideo
from sexton.http import ApiError

PROFILE = "me"


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        """Moves time forward."""
        self.now += seconds


class FakeCore:
    """Publishing channels and processes, with optional injected failures."""

    def __init__(self, clock):
        self.clock = clock
        self.publishing_names = set()
        self.processes = {}  # id -> Egress, including foreign processes
        self.metadata = {}
        self.calls = []
        self.fail = {}  # (method, process_id) -> exception

    def _maybe_fail(self, method, pid=None):
        err = self.fail.get((method, pid))
        if err:
            raise err

    def publishing(self):
        self._maybe_fail("publishing")
        return set(self.publishing_names)

    def egresses(self):
        return {pid: e for pid, e in self.processes.items() if pid.startswith("sexton:")}

    def create_egress(self, process_id, rtmp_name, stream_url):
        self.calls.append(("create_egress", process_id))
        self._maybe_fail("create_egress", process_id)
        self.processes[process_id] = Egress(process_id, self.clock(), stream_url)

    def delete_egress(self, process_id):
        self.calls.append(("delete_egress", process_id))
        self.processes.pop(process_id, None)

    def set_metadata(self, process_id, data):
        self.metadata[process_id] = data


class FakeGraph:
    """Live videos across targets. New videos report `new_status` until promoted."""

    def __init__(self, new_status="LIVE"):
        self.new_status = new_status
        self.videos = {}  # id -> dict(target, title, status, url)
        self.calls = []
        self.fail = {}  # (method, target) -> exception
        self._next = 1

    def add(self, target, title, status="LIVE"):
        """Seeds a live video as if created earlier."""
        vid = f"v{self._next}"
        self._next += 1
        self.videos[vid] = {"target": target, "title": title, "status": status, "url": f"rtmps://fb/{vid}"}
        return vid

    def active_live_videos(self, target, token):
        self.calls.append(("list", target))
        self._maybe_fail("list", target)
        return [
            LiveVideo(vid, v["status"], v["title"])
            for vid, v in self.videos.items()
            if v["target"] == target and v["status"] in ACTIVE_STATUSES
        ]

    def create_live_video(self, target, token, title, description, privacy=None):
        self.calls.append(("create", target))
        self._maybe_fail("create", target)
        vid = self.add(target, title, self.new_status)
        return vid, self.videos[vid]["url"]

    def ingest_url(self, video_id, token):
        self.calls.append(("ingest_url", video_id))
        return self.videos[video_id]["url"]

    def end_live_video(self, video_id, token):
        self.calls.append(("end", video_id))
        self.videos[video_id]["status"] = "VOD"

    def live(self, target):
        """Ids of videos currently LIVE on target."""
        return [vid for vid, v in self.videos.items() if v["target"] == target and v["status"] == "LIVE"]

    def _maybe_fail(self, method, target):
        err = self.fail.get((method, target))
        if err:
            raise err


class FakeAlerter:
    """Records problems and resolutions instead of sending them."""

    def __init__(self):
        self.problems = []  # (key, title)
        self.active = set()

    def problem(self, key, title, message):
        self.problems.append((key, title))
        self.active.add(key)

    def resolved(self, key):
        self.active.discard(key)

    def keys(self):
        """Every problem key ever raised."""
        return [k for k, _ in self.problems]


def make_config():
    """Two churches, each with its Page plus the shared profile."""

    def church(name, title, page):
        return Input(
            name=name,
            rtmp_name=f"{name}-uuid.stream",
            title=title,
            description="",
            destinations=(
                Destination("page", page, f"token-{page}"),
                Destination("profile", PROFILE, "token-user"),
            ),
        )

    return Config(
        core=CoreConfig(url="http://core"),
        inputs=(church("colfax", "Colfax Worship", "colfax-page"), church("levels", "Levels Worship", "levels-page")),
    )


__all__ = ["ApiError", "FakeAlerter", "FakeClock", "FakeCore", "FakeGraph", "PROFILE", "make_config"]
