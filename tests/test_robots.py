from sitemill.fetch.robots import RobotsCache, origin_of, ua_token

UA = "sitemill/0.1 (akiya-atlas; +https://x.example/about/)"
ROBOTS = b"User-agent: *\nDisallow: /private\nCrawl-delay: 5\nSitemap: https://a.example/s.xml\n"


def _cache(responses: dict[str, tuple[int, bytes] | None]) -> tuple[RobotsCache, list[str]]:
    calls: list[str] = []

    def fetch(url: str) -> tuple[int, bytes] | None:
        calls.append(url)
        return responses.get(url)

    return RobotsCache(fetch, UA), calls


def test_ua_token_and_origin() -> None:
    assert ua_token(UA) == "sitemill"
    assert origin_of("https://a.example/x/y?z=1") == "https://a.example"


def test_rules_delay_and_sitemaps_are_cached_per_origin() -> None:
    cache, calls = _cache({"https://a.example/robots.txt": (200, ROBOTS)})
    assert cache.allowed("https://a.example/ok")
    assert not cache.allowed("https://a.example/private/x")
    assert cache.crawl_delay("https://a.example/ok") == 5.0
    assert cache.sitemaps("https://a.example/") == ["https://a.example/s.xml"]
    assert calls == ["https://a.example/robots.txt"]


def test_404_allows_everything() -> None:
    cache, _ = _cache({"https://b.example/robots.txt": (404, b"")})
    assert cache.allowed("https://b.example/anything")
    assert cache.crawl_delay("https://b.example/anything") is None
    assert cache.sitemaps("https://b.example/") == []


def test_server_error_or_failure_denies_this_run() -> None:
    cache, _ = _cache({"https://c.example/robots.txt": (503, b"")})
    assert not cache.allowed("https://c.example/x")
    cache2, _ = _cache({})
    assert not cache2.allowed("https://d.example/x")
    assert "巡回しない" in cache2.info("https://d.example/x").note


def test_ua_specific_group() -> None:
    cache, _ = _cache(
        {"https://e.example/robots.txt": (200, b"User-agent: sitemill\nDisallow: /\n")}
    )
    assert not cache.allowed("https://e.example/x")
    other, _ = _cache(
        {"https://f.example/robots.txt": (200, b"User-agent: otherbot\nDisallow: /\n")}
    )
    assert other.allowed("https://f.example/x")
