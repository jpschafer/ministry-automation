"""HTTP-level behaviour of the YouTube client: token refresh, the reusable
stream, create + bind, ending vs deleting, and error typing."""

from __future__ import annotations

import pytest

from sexton.http import ApiError, AuthError, RateLimitError
from sexton.youtube import API_URL, TOKEN_URL, YouTubeClient
from tests.test_clients import http_with, response

TOKEN = response(200, {"access_token": "at", "expires_in": 3600})
STREAM = {
    "id": "s1",
    "snippet": {"title": "sexton"},
    "cdn": {"ingestionInfo": {"rtmpsIngestionAddress": "rtmps://a.rtmps.youtube.com/live2", "streamName": "key"}},
}


def client(*responses, dry_run=False):
    http = http_with(*responses, dry_run=dry_run)
    return YouTubeClient(http, "cid", "secret", clock=lambda: 0), http


def calls(http):
    """(method, url, params) of every request sent."""
    return [(c.args[0], c.args[1], c.kwargs.get("params")) for c in http.session.request.call_args_list]


def test_lists_active_and_pending_broadcasts_and_caches_the_access_token():
    yt, http = client(
        TOKEN,
        response(200, {"items": [{"id": "b1", "snippet": {"title": "A"}, "status": {"lifeCycleStatus": "live"}}]}),
        response(200, {"items": [{"id": "b2", "snippet": {"title": "B"}, "status": {"lifeCycleStatus": "ready"}}]}),
    )
    videos = yt.active_live_videos("mine", "refresh")
    assert [(v.id, v.status) for v in videos] == [("b1", "LIVE"), ("b2", "ready")]
    sent = calls(http)
    assert sent[0][1] == TOKEN_URL
    assert [p["broadcastStatus"] for _, _, p in sent[1:]] == ["active", "upcoming"]
    assert all(p["broadcastType"] == "event" for _, _, p in sent[1:])  # never the "Stream now" broadcast


def test_create_finds_the_reusable_stream_then_inserts_and_binds():
    yt, http = client(
        TOKEN,
        response(200, {"items": [{"id": "other", "snippet": {"title": "OBS"}}, STREAM]}),
        response(200, {"id": "b9"}),
        response(200, {"id": "b9"}),
    )
    video_id, url = yt.create_live_video("mine", "refresh", "Colfax – Nov 30, 2026", "", "unlisted")
    assert (video_id, url) == ("b9", "rtmps://a.rtmps.youtube.com/live2/key")
    insert = http.session.request.call_args_list[2]
    assert insert.args[:2] == ("POST", f"{API_URL}/liveBroadcasts")
    body = insert.kwargs["json"]
    assert body["status"]["privacyStatus"] == "unlisted"
    assert body["contentDetails"]["enableAutoStart"] is True
    assert body["contentDetails"]["enableAutoStop"] is False
    assert calls(http)[3][1:] == (f"{API_URL}/liveBroadcasts/bind", {"id": "b9", "streamId": "s1", "part": "id"})


def test_create_makes_the_stream_the_first_time():
    yt, http = client(TOKEN, response(200, {"items": []}), response(200, STREAM),
                      response(200, {"id": "b1"}), response(200, {"id": "b1"}))
    assert yt.create_live_video("mine", "refresh", "t", "")[1].endswith("/live2/key")
    create = http.session.request.call_args_list[2]
    assert create.args[1] == f"{API_URL}/liveStreams"
    assert create.kwargs["json"]["contentDetails"]["isReusable"] is True


def test_end_completes_an_on_air_broadcast():
    yt, http = client(TOKEN, response(200, {"items": [{"status": {"lifeCycleStatus": "live"}}]}), response(200, {}))
    yt.end_live_video("b1", "refresh")
    assert calls(http)[2] == ("POST", f"{API_URL}/liveBroadcasts/transition",
                              {"broadcastStatus": "complete", "id": "b1", "part": "id"})


def test_end_deletes_a_broadcast_that_never_aired():
    yt, http = client(TOKEN, response(200, {"items": [{"status": {"lifeCycleStatus": "ready"}}]}), response(204))
    yt.end_live_video("b1", "refresh")
    assert calls(http)[2] == ("DELETE", f"{API_URL}/liveBroadcasts", {"id": "b1"})


def test_end_treats_redundant_transition_as_done():
    err = {"error": {"code": 403, "message": "x", "errors": [{"reason": "redundantTransition"}]}}
    yt, _ = client(TOKEN, response(200, {"items": [{"status": {"lifeCycleStatus": "live"}}]}), response(403, err))
    yt.end_live_video("b1", "refresh")


def test_ingest_url_follows_the_bound_stream():
    yt, _ = client(TOKEN, response(200, {"items": [{"contentDetails": {"boundStreamId": "s1"}}]}),
                   response(200, {"items": [STREAM]}))
    assert yt.ingest_url("b1", "refresh") == "rtmps://a.rtmps.youtube.com/live2/key"


def test_revoked_refresh_token_is_an_auth_error():
    yt, _ = client(response(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    with pytest.raises(AuthError, match="youtube_auth"):
        yt.active_live_videos("mine", "refresh")


def test_stale_access_token_is_refreshed_once():
    yt, http = client(TOKEN, response(401, {"error": {"code": 401, "errors": [{"reason": "authError"}]}}),
                      response(200, {"access_token": "at2", "expires_in": 3600}),
                      response(200, {"items": []}), response(200, {"items": []}))
    assert yt.active_live_videos("mine", "refresh") == []
    assert http.session.request.call_args_list[3].kwargs["headers"]["Authorization"] == "Bearer at2"


@pytest.mark.parametrize(
    "status,reason,exc",
    [(403, "quotaExceeded", RateLimitError), (403, "rateLimitExceeded", RateLimitError),
     (403, "liveStreamingNotEnabled", AuthError), (400, "invalidValue", ApiError)],
)
def test_errors_are_typed_by_reason(status, reason, exc):
    yt, _ = client(TOKEN, response(status, {"error": {"code": status, "message": "x", "errors": [{"reason": reason}]}}))
    with pytest.raises(exc):
        yt.active_live_videos("mine", "refresh")


def test_dry_run_creates_nothing():
    yt, http = client(TOKEN, response(200, {"items": [STREAM]}), dry_run=True)
    assert yt.create_live_video("mine", "refresh", "t", "")[0] == "dry-run"
    methods = [m for m, _, _ in calls(http)]
    assert methods == ["POST", "GET"]  # token refresh (not mutating) + stream lookup only


def test_auth_helper_extracts_the_code_and_checks_state():
    from sexton.youtube_auth import code_from  # pylint: disable=import-outside-toplevel

    assert code_from("http://127.0.0.1:8765/?state=abc&code=4/xyz&scope=s", "abc") == "4/xyz"
    with pytest.raises(ApiError, match="state"):
        code_from("http://127.0.0.1:8765/?state=other&code=4/xyz", "abc")
    with pytest.raises(ApiError, match="access_denied"):
        code_from("http://127.0.0.1:8765/?error=access_denied&state=abc", "abc")
