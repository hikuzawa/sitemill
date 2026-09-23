"""Cloudflare Web Analytics の読み取り（ADR 0007 追記、2026-09-23）。外部アクセスはしない。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from sitemill.metrics import NAVIGATION_HEADERS, rum_pageloads
from sitemill.metrics.rum import ENDPOINT
from sitemill.settings import Secrets

FULL = Secrets(cloudflare_api_token="t0ken", cloudflare_account_id="acc0unt")


def body(*rows: tuple[str, int]) -> dict[str, Any]:
    groups = [{"count": n, "dimensions": {"requestPath": path}} for path, n in rows]
    return {"data": {"viewer": {"accounts": [{"rumPageloadEventsAdaptiveGroups": groups}]}}}


@respx.mock
def test_counts_come_back_keyed_by_path() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=body(("/go/a/", 12), ("/go/b/", 3)))
    )
    assert rum_pageloads(FULL, "example.com", 7) == {"/go/a/": 12, "/go/b/": 3}

    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer t0ken"
    payload = httpx.Response(200, content=sent.content).json()
    assert payload["variables"]["host"] == "example.com"
    assert payload["variables"]["account"] == "acc0unt"
    assert payload["variables"]["since"].endswith("Z")


@respx.mock
def test_the_filter_is_the_host_not_the_site_tag() -> None:
    """同じホスト名で登録が 2 つあると、挿し込まれた token 側が空になることがある。"""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=body()))
    rum_pageloads(FULL, "example.com", 7)
    query = httpx.Response(200, content=respx.calls.last.request.content).json()["query"]
    assert "requestHost: $host" in query
    assert "siteTag" not in query


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, text="forbidden"),  # Account Analytics: Read が無い
        httpx.Response(200, json={"errors": [{"message": "bad"}], "data": None}),
        httpx.Response(200, json={"data": {"viewer": {"accounts": []}}}),
        httpx.Response(200, text="not json"),
    ],
)
@respx.mock
def test_anything_unusable_is_none_so_the_weekly_keeps_running(response: httpx.Response) -> None:
    respx.post(ENDPOINT).mock(return_value=response)
    assert rum_pageloads(FULL, "example.com", 7) is None


@respx.mock
def test_a_network_error_is_none_too() -> None:
    respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError("down"))
    assert rum_pageloads(FULL, "example.com", 7) is None


@pytest.mark.parametrize(
    ("secrets", "host"),
    [
        (Secrets(cloudflare_account_id="acc0unt"), "example.com"),
        (Secrets(cloudflare_api_token="t0ken"), "example.com"),
        (FULL, ""),
    ],
)
@respx.mock
def test_missing_keys_do_not_call_cloudflare(secrets: Secrets, host: str) -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=body()))
    assert rum_pageloads(secrets, host, 7) is None
    assert not route.called


def test_the_navigation_headers_are_the_ones_that_get_the_beacon() -> None:
    """自動挿入はナビゲーションと同じ形の要求にだけ入る（素の curl では入らない）。"""
    assert NAVIGATION_HEADERS["Sec-Fetch-Mode"] == "navigate"
    assert NAVIGATION_HEADERS["Accept"].startswith("text/html")
