"""礼儀正しい HTTP クライアント。robots.txt、ホスト別の間隔、条件付き GET を扱う（ADR 0003）。"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from sitemill.fetch.decode import decode_html
from sitemill.fetch.links import host_of
from sitemill.fetch.robots import RobotsCache
from sitemill.models import utcnow

log = logging.getLogger(__name__)

_TEXTUAL = ("text/", "xml", "json", "xhtml")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    fetched_at: datetime
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    text: str = ""
    encoding: str = ""
    not_modified: bool = False
    blocked: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None and not self.blocked

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")


class PoliteClient:
    """1 ホストにつき一定間隔でしか取得しない同期クライアント。"""

    def __init__(
        self,
        user_agent: str,
        *,
        default_delay: float = 3.0,
        jitter: float = 1.0,
        timeout: float = 30.0,
        retries: int = 1,
        retry_wait: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.default_delay = default_delay
        self.jitter = jitter
        self.retries = retries
        self.retry_wait = retry_wait
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "ja,en;q=0.5"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.robots = RobotsCache(self._fetch_robots, user_agent)
        self._last_request: dict[str, float] = {}
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 内部 ---------------------------------------------------------------

    def _wait_turn(self, host: str, delay: float) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = last + delay - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def _fetch_robots(self, url: str) -> tuple[int, bytes] | None:
        self._wait_turn(host_of(url), self.default_delay)
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            log.warning("robots.txt 取得失敗 %s: %s", url, e)
            return None
        self.request_count += 1
        return resp.status_code, resp.content

    def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        attempt = 0
        while True:
            try:
                resp = self._client.get(url, headers=headers)
                self.request_count += 1
                if resp.status_code < 500 or attempt >= self.retries:
                    return resp
            except httpx.TransportError:
                self.request_count += 1
                if attempt >= self.retries:
                    raise
            attempt += 1
            self._sleep(self.retry_wait)

    # --- 公開 ---------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        delay: float | None = None,
        check_robots: bool = True,
    ) -> FetchResult:
        now = utcnow()
        if check_robots and not self.robots.allowed(url):
            note = self.robots.info(url).note or "robots.txt により拒否"
            log.info("robots により取得しない %s (%s)", url, note)
            return FetchResult(
                url=url, final_url=url, status=0, fetched_at=now, blocked=True, error=note
            )

        wait = max(
            delay if delay is not None else self.default_delay, self.robots.crawl_delay(url) or 0.0
        )
        wait += self._rng.uniform(0, self.jitter) if self.jitter > 0 else 0.0
        self._wait_turn(host_of(url), wait)

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            resp = self._request(url, headers)
        except httpx.HTTPError as e:
            log.warning("取得失敗 %s: %s", url, e)
            return FetchResult(
                url=url, final_url=url, status=0, fetched_at=now, error=f"{type(e).__name__}: {e}"
            )

        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        result = FetchResult(
            url=url,
            final_url=str(resp.url),
            status=resp.status_code,
            fetched_at=now,
            headers=resp_headers,
            content=resp.content,
        )
        if resp.status_code == 304:
            result.not_modified = True
            return result
        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}"
            return result
        ctype = resp_headers.get("content-type", "")
        if not ctype or any(t in ctype for t in _TEXTUAL):
            result.text, result.encoding = decode_html(resp.content, ctype or None)
        else:
            result.encoding = "binary"
        return result
