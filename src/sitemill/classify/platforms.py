"""既知の第三者プラットフォーム（民間 ASP・不動産ポータル）のホスト一覧（ADR 0007）。

sitemill が既定の一覧を持ち、サービス側から追記できる。
ここに載るホストは巡回せずリンクのみ扱いにする。
"""

from __future__ import annotations

from urllib.parse import urlsplit

# 空き家バンクを扱う代表的な民間プラットフォーム。ホスト（またはその親ドメイン）で照合する。
DEFAULT_PLATFORM_HOSTS: frozenset[str] = frozenset(
    {
        "akiya-athome.jp",  # アットホーム 空き家バンク（自治体別サブドメイン）
        "athome.co.jp",
        "homes.co.jp",  # LIFULL HOME'S 空き家バンク
        "rakuen-akiya.jp",  # 楽園信州 空き家バンク・空き地バンク
        "akiyabank-all.com",  # 日本全国の空き家バンクを検索
        "hatomarksite.com",  # ハトマークサイト
        "sumai.biz",  # Sumai空き家
        "fudousan.co.jp",  # ココスマ 等
        "purehouse.jp",
        "athome-tochi.jp",
    }
)


class PlatformRegistry:
    """第三者プラットフォームのホスト集合。サービス側の追加ホストを足せる。"""

    def __init__(self, extra_hosts: frozenset[str] | set[str] | None = None) -> None:
        self._hosts: set[str] = {h.lower().lstrip(".") for h in DEFAULT_PLATFORM_HOSTS}
        if extra_hosts:
            self.add(extra_hosts)

    def add(self, hosts: frozenset[str] | set[str] | list[str]) -> None:
        self._hosts.update(h.lower().lstrip(".") for h in hosts)

    @property
    def hosts(self) -> frozenset[str]:
        return frozenset(self._hosts)

    def match(self, url: str) -> str | None:
        """URL のホストが登録ホスト（またはそのサブドメイン）なら、その登録ホストを返す。"""
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            return None
        for known in self._hosts:
            if host == known or host.endswith("." + known):
                return known
        return None

    def is_platform(self, url: str) -> bool:
        return self.match(url) is not None
