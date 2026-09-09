"""robots.txt の取得と判定。取得失敗や 5xx はそのホストを巡回しない（ADR 0003）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from protego import Protego

# (status, body) を返す。通信エラーは None。
RobotsFetcher = Callable[[str], tuple[int, bytes] | None]


@dataclass
class RobotsInfo:
    origin: str
    status: int | None
    parser: Protego | None
    allow_all: bool = False
    deny_all: bool = False
    note: str = ""


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def ua_token(user_agent: str) -> str:
    """robots.txt の User-agent 照合に使う製品名（例: sitemill）。"""
    return user_agent.split("/", 1)[0].split(" ", 1)[0] or "*"


class RobotsCache:
    def __init__(self, fetcher: RobotsFetcher, user_agent: str) -> None:
        self._fetch = fetcher
        self.token = ua_token(user_agent)
        self._cache: dict[str, RobotsInfo] = {}

    def info(self, url: str) -> RobotsInfo:
        origin = origin_of(url)
        cached = self._cache.get(origin)
        if cached is not None:
            return cached
        result = self._fetch(origin + "/robots.txt")
        if result is None:
            info = RobotsInfo(
                origin,
                None,
                None,
                deny_all=True,
                note="robots.txt を取得できないため今回は巡回しない",
            )
        else:
            status, body = result
            if status == 200:
                info = RobotsInfo(
                    origin, status, Protego.parse(body.decode("utf-8", errors="replace"))
                )
            elif 400 <= status < 500:
                info = RobotsInfo(
                    origin, status, None, allow_all=True, note=f"robots.txt {status}: 制限なし"
                )
            else:
                info = RobotsInfo(
                    origin,
                    status,
                    None,
                    deny_all=True,
                    note=f"robots.txt {status}: 今回は巡回しない",
                )
        self._cache[origin] = info
        return info

    def allowed(self, url: str) -> bool:
        info = self.info(url)
        if info.deny_all:
            return False
        if info.allow_all or info.parser is None:
            return True
        return bool(info.parser.can_fetch(url, self.token))

    def crawl_delay(self, url: str) -> float | None:
        info = self.info(url)
        if info.parser is None:
            return None
        delay = info.parser.crawl_delay(self.token)
        return float(delay) if delay is not None else None

    def sitemaps(self, url: str) -> list[str]:
        info = self.info(url)
        return list(info.parser.sitemaps) if info.parser is not None else []
