import httpx
import pytest
import respx

from sitemill.fetch.client import PoliteClient

UA = "sitemill/0.1 (test; +https://t.example/about/)"


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []

    def sleep(self, s: float) -> None:
        self.sleeps.append(round(s, 6))
        self.t += s

    def now(self) -> float:
        return self.t


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make(clock: FakeClock, **kw: object) -> PoliteClient:
    return PoliteClient(UA, jitter=0.0, sleep=clock.sleep, clock=clock.now, **kw)  # type: ignore[arg-type]


@respx.mock
def test_get_decodes_and_spaces_requests_per_host(clock: FakeClock) -> None:
    respx.get("https://a.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://a.example/p1").mock(
        return_value=httpx.Response(
            200,
            content="<p>空き家</p>".encode("cp932"),
            headers={"content-type": "text/html; charset=Shift_JIS", "etag": '"v1"'},
        )
    )
    respx.get("https://a.example/p2").mock(
        return_value=httpx.Response(200, text="<p>b</p>", headers={"content-type": "text/html"})
    )
    c = make(clock)
    r1 = c.get("https://a.example/p1")
    assert r1.ok and "空き家" in r1.text and r1.encoding == "cp932" and r1.etag == '"v1"'
    r2 = c.get("https://a.example/p2")
    assert r2.ok and r2.text == "<p>b</p>"
    assert clock.sleeps == [3.0, 3.0]
    assert c.request_count == 3


@respx.mock
def test_conditional_get_returns_not_modified(clock: FakeClock) -> None:
    respx.get("https://a.example/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://a.example/p").mock(return_value=httpx.Response(304))
    r = make(clock).get(
        "https://a.example/p", etag='"v1"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT"
    )
    assert r.not_modified and not r.ok and r.status == 304
    sent = route.calls[0].request
    assert sent.headers["If-None-Match"] == '"v1"'
    assert sent.headers["If-Modified-Since"].startswith("Mon")
    assert sent.headers["User-Agent"] == UA


@respx.mock
def test_robots_disallow_blocks_without_request(clock: FakeClock) -> None:
    respx.get("https://a.example/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /secret\nCrawl-delay: 10\n")
    )
    secret = respx.get("https://a.example/secret/x").mock(
        return_value=httpx.Response(200, text="x")
    )
    open_page = respx.get("https://a.example/open").mock(return_value=httpx.Response(200, text="x"))
    c = make(clock)
    r = c.get("https://a.example/secret/x")
    assert r.blocked and not r.ok and secret.call_count == 0
    assert c.get("https://a.example/open").ok and open_page.call_count == 1
    assert clock.sleeps == [10.0]


@respx.mock
def test_retries_once_on_server_error(clock: FakeClock) -> None:
    respx.get("https://a.example/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://a.example/e").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, text="ok", headers={"content-type": "text/plain"}),
        ]
    )
    r = make(clock).get("https://a.example/e")
    assert r.ok and r.text == "ok" and route.call_count == 2


@respx.mock
def test_connection_error_is_reported_not_raised(clock: FakeClock) -> None:
    respx.get("https://a.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://a.example/x").mock(side_effect=httpx.ConnectError("boom"))
    r = make(clock, retries=0).get("https://a.example/x")
    assert not r.ok and r.error is not None and "ConnectError" in r.error


@respx.mock
def test_non_200_is_error_and_binary_is_not_decoded(clock: FakeClock) -> None:
    respx.get("https://a.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://a.example/missing").mock(return_value=httpx.Response(404))
    respx.get("https://a.example/f.pdf").mock(
        return_value=httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        )
    )
    c = make(clock)
    assert c.get("https://a.example/missing").error == "HTTP 404"
    pdf = c.get("https://a.example/f.pdf")
    assert pdf.ok and pdf.text == "" and pdf.encoding == "binary"
