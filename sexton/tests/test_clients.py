"""HTTP-level behaviour of the Core and Graph clients: response checking,
dry-run, and the sexton: ownership guard."""

from __future__ import annotations

from unittest import mock

import pytest

from sexton.alerts import Alerter, Healthcheck
from sexton.config import CoreConfig
from sexton.core import CoreClient
from sexton.facebook import GraphClient
from sexton.http import ApiError, AuthError, Http, RateLimitError


def response(status=200, body=None, text=""):
    resp = mock.Mock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.text = text
    if body is None:
        resp.json.side_effect = ValueError
    else:
        resp.json.return_value = body
    return resp


def http_with(*responses, dry_run=False):
    http = Http(dry_run=dry_run)
    http.session = mock.Mock()
    http.session.request.side_effect = list(responses)
    return http


def test_dry_run_makes_no_mutating_calls():
    http = http_with(response(200, {"data": []}), dry_run=True)
    graph = GraphClient(http)
    core = CoreClient(CoreConfig("http://core"), http)
    assert graph.create_live_video("page", "t", "title", "")[0] == "dry-run"
    graph.end_live_video("123", "t")
    core.create_egress("sexton:a:b", "x.stream", "rtmps://fb")
    core.delete_egress("sexton:a:b")
    core.set_metadata("sexton:a:b", {})
    Alerter(http, "https://ntfy.sh/topic").problem("k", "t", "m")
    Healthcheck(http, "https://hc-ping.com/uuid").ping(True)
    assert http.session.request.call_count == 0
    assert graph.active_live_videos("page", "t") == []  # reads still happen
    assert http.session.request.call_count == 1


def test_refuses_to_touch_foreign_processes():
    core = CoreClient(CoreConfig("http://core"), http_with())
    for call in (
        lambda: core.delete_egress("restreamer-ui:egress:youtube:1"),
        lambda: core.create_egress("restreamer-ui:egress:x", "a", "b"),
        lambda: core.set_metadata("other", {}),
    ):
        with pytest.raises(ValueError):
            call()


def test_egresses_lists_only_owned_processes():
    body = [
        {"id": "restreamer-ui:ingest:1", "created_at": 1},
        {"id": "sexton:colfax:page", "created_at": 5, "config": {"output": [{"address": "rtmps://x"}]}},
    ]
    core = CoreClient(CoreConfig("http://core"), http_with(response(200, body)))
    egresses = core.egresses()
    assert list(egresses) == ["sexton:colfax:page"]
    assert egresses["sexton:colfax:page"].created_at == 5


def test_publishing_reduces_paths_to_channel_names():
    core = CoreClient(CoreConfig("http://core"), http_with(response(200, [{"name": "/live/abc.stream"}])))
    assert core.publishing() == {"abc.stream"}


def test_core_relogs_in_once_on_401():
    http = http_with(
        response(200, {"access_token": "a1"}),
        response(401),
        response(200, {"access_token": "a2"}),
        response(200, []),
    )
    core = CoreClient(CoreConfig("http://core", "u", "p"), http)
    assert core.publishing() == set()
    last = http.session.request.call_args
    assert last.kwargs["headers"]["Authorization"] == "Bearer a2"


def test_core_errors_are_raised():
    core = CoreClient(CoreConfig("http://core"), http_with(response(500, text="nope")))
    with pytest.raises(ApiError):
        core.publishing()


@pytest.mark.parametrize(
    "status,code,exc",
    [(400, 190, AuthError), (403, 200, AuthError), (400, 613, RateLimitError), (500, 1, ApiError)],
)
def test_graph_errors_are_typed(status, code, exc):
    graph = GraphClient(http_with(response(status, {"error": {"code": code, "message": "x"}})))
    with pytest.raises(exc):
        graph.active_live_videos("page", "t")


def test_graph_error_body_with_200_is_an_error():
    graph = GraphClient(http_with(response(200, {"error": {"code": 1, "message": "x"}})))
    with pytest.raises(ApiError):
        graph.end_live_video("1", "t")


def test_graph_filters_inactive_videos_client_side():
    body = {"data": [{"id": "1", "status": "VOD", "title": "a"}, {"id": "2", "status": "LIVE", "title": "b"}]}
    graph = GraphClient(http_with(response(200, body)))
    assert [v.id for v in graph.active_live_videos("page", "t")] == ["2"]


def test_ingest_url_prefers_master_stream():
    body = {"ingest_streams": [{"secure_stream_url": "backup"}, {"secure_stream_url": "main", "is_master": True}]}
    graph = GraphClient(http_with(response(200, body)))
    assert graph.ingest_url("1", "t") == "main"


def test_create_without_stream_url_is_an_error():
    graph = GraphClient(http_with(response(200, {"id": "1"})))
    with pytest.raises(ApiError):
        graph.create_live_video("page", "t", "title", "")


def test_alerts_are_deduplicated_and_recovery_is_sent():
    http = http_with(response(200, {}), response(200, {}))
    alerter = Alerter(http, "https://ntfy.sh/topic", clock=lambda: 0)
    alerter.problem("k", "t", "m")
    alerter.problem("k", "t", "m")
    alerter.resolved("k")
    alerter.resolved("k")
    assert http.session.request.call_count == 2
