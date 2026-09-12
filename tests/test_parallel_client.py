"""PoliteClient のスレッド安全性（ADR 0013）: 同一ホストの間隔は保たれ、別ホストは並行する。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import respx

from sitemill.fetch.client import PoliteClient

DELAY = 0.15


@respx.mock
def test_hosts_run_in_parallel_but_each_host_keeps_its_delay() -> None:
    hits: dict[str, list[float]] = {"a": [], "b": []}
    lock = threading.Lock()

    def recorder(host: str):  # noqa: ANN202
        def _handle(request: httpx.Request) -> httpx.Response:
            with lock:
                hits[host].append(time.monotonic())
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

        return _handle

    for h in ("a", "b"):
        respx.get(f"https://{h}.example/robots.txt").mock(return_value=httpx.Response(404))
        respx.get(url__regex=rf"https://{h}\.example/p\d").mock(side_effect=recorder(h))

    client = PoliteClient("sitemill-test/0", default_delay=DELAY, jitter=0)
    urls = [f"https://{h}.example/p{i}" for i in range(4) for h in ("a", "b")]
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(client.get, urls))
    elapsed = time.monotonic() - t0
    client.close()

    assert all(r.ok for r in results)
    assert client.request_count == 10  # 8 ページ + robots.txt 2 回（カウンタもスレッド安全）
    windows = {}
    for h in ("a", "b"):
        ts = sorted(hits[h])
        gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]
        assert all(g >= DELAY * 0.9 for g in gaps), (h, gaps)  # 同一ホストは間隔を守る
        windows[h] = (ts[0], ts[-1])
    # 並行しているかは「2 ホストの取得期間が重なっているか」で見る。経過時間の上限で見ると、
    # 機械が混んでいるときに落ちる（実際に 1 回落ちた）。重なりは負荷に左右されない
    (a0, a1), (b0, b1) = windows["a"], windows["b"]
    assert min(a1, b1) > max(a0, b0), windows
    # 直列なら 7×DELAY 以上かかる。念のための緩い上限
    assert elapsed < 7 * DELAY, elapsed


@respx.mock
def test_sequential_use_is_unchanged() -> None:
    respx.get("https://c.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://c.example/x").mock(
        return_value=httpx.Response(200, text="<p>x</p>", headers={"content-type": "text/html"})
    )
    client = PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)
    assert client.get("https://c.example/x").ok and client.get("https://c.example/x").ok
    assert client.request_count == 3
    client.close()
