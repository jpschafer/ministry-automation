"""Token renewal helper against a mocked Graph API."""

from __future__ import annotations

import pytest

from sexton import renew_token
from sexton.http import ApiError
from tests.test_clients import http_with, response


def test_exchange_inspect_and_pages():
    http = http_with(
        response(200, {"access_token": "long", "expires_in": 5184000}),
        response(200, {"data": {"is_valid": True, "scopes": ["publish_video"], "expires_at": 123}}),
        response(200, {"data": [{"id": "1", "name": "Colfax", "access_token": "pt"}]}),
    )
    assert renew_token.exchange(http, "app", "secret", "short") == "long"
    assert renew_token.inspect(http, "app", "secret", "long") == ({"publish_video"}, 123)
    assert renew_token.pages(http, "long")[0]["access_token"] == "pt"
    exchange_params = http.session.request.call_args_list[0].kwargs["params"]
    assert exchange_params["grant_type"] == "fb_exchange_token"
    assert exchange_params["fb_exchange_token"] == "short"


def test_expired_short_token_is_an_error():
    http = http_with(response(400, {"error": {"code": 190, "message": "expired"}}))
    with pytest.raises(ApiError):
        renew_token.exchange(http, "app", "secret", "short")


def test_invalid_new_token_is_an_error():
    http = http_with(response(200, {"data": {"is_valid": False}}))
    with pytest.raises(ApiError):
        renew_token.inspect(http, "app", "secret", "long")
