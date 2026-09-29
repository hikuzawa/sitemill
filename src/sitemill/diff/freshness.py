"""出どころのページを読めた時刻で鮮度を進める（提案 L、ADR 0028、2026-09-29）。

鮮度の基準は「出どころのページを最後に読めた時刻」で、**抽出した時刻ではない**。抽出は中身が
変わったページにしか走らないので、抽出した時刻を使うと、変わらないページの鮮度が止まる。

同じ考え方を 3 か所で別々に持っていて、片方だけ直る事故が起きた。
- japan-open-today の施設: 最初の抽出から 14 日たった 9/27〜28 に、毎日読めている施設まで
  「取得が 14 日以上できていない」に落ち、本番の判定が「開いている 8・不明 152」になった
- akiya-atlas の補助制度: 確認日が抽出した日のままで、2,638 件が 2027-03-14〜15 に一斉に
  「情報が古い可能性」になるところだった
- akiya-atlas の物件: `last_seen_at` を別の形で進めていた

進めてよいのは、巡回の状態で次の 3 つがそろうページだけ。
1. 失敗が無い（`error` が空）
2. 読めた時刻がある（`fetched_at`。200 でも 304 でも進み、失敗では進まない）
3. 抽出したときから中身が変わっていない（`content_hash == extracted_hash`）。中身が変わったのに
   抽出がまだ・失敗した晩に、古い事実を新しいものとして扱わない

進めないのは、取得に失敗しているページ、中身が変わってまだ抽出していないページ、巡回先から
外れたページ（状態が無い）。古くなったことが表示に出るのが正しい。

**落とし穴**:
- 抽出した時刻（provenance の `extracted_at`・`fetched_at`）を表示や判定に使うと、変わらないページで
  止まる。表示の「取得日時」も同じ（akiya-atlas の物件ページで「更新 9/28」と「取得 9/27」が
  並んでいた）
- 一斉に集めたデータは、同じ日に一斉に古くなる。障害は「ある日、全部」の形で出るので、テストでは
  「読めていれば進む」と「読めていなければ進まない」の両方を当てる
- 日付で持つ項目は JST で丸める（UTC の日付だと、日本時間の朝に走る日次は 1 日早くなる）
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from sitemill.clock import to_jst
from sitemill.diff.state import CrawlState, UrlState


def page_read_at(st: UrlState | None) -> datetime | None:
    """そのページを最後に読めた時刻。上の 3 つの条件がそろわなければ None。"""
    if st is None or st.error or st.fetched_at is None:
        return None
    if not st.content_hash or st.content_hash != st.extracted_hash:
        return None
    return st.fetched_at


def read_at(state: CrawlState, urls: Iterable[str]) -> datetime | None:
    """出どころのページを**すべて**読めているとき、いちばん古く読めた時刻。1 枚でも欠ければ None。

    読むだけで何も書き換えない。データを読むたびに鮮度を計算するサービス（japan-open-today）はこれを使う。
    """
    wanted = {u for u in urls if u}
    if not wanted:
        return None
    stamps = [page_read_at(state.get(u)) for u in sorted(wanted)]
    if any(s is None for s in stamps):
        return None
    return min(s for s in stamps if s is not None)


def mark_read(
    records: Iterable[dict[str, Any]],
    state: CrawlState,
    *,
    source_url: Callable[[dict[str, Any]], str | Iterable[str] | None],
    field: str,
    as_date: bool = False,
) -> int:
    """レコードの項目（`last_seen_at`・`checked_on` など）を、出どころを読めた時刻まで進める。

    進めた件数を返す。前に戻すことはしない。`as_date` なら JST の日付（YYYY-MM-DD）で書く。
    項目を保存して持つサービス（akiya-atlas の物件・補助制度）はこれを使う。
    """
    moved = 0
    for record in records:
        found = source_url(record)
        urls = [found] if isinstance(found, str) else list(found or [])
        read = read_at(state, urls)
        if read is None:
            continue
        value = to_jst(read).date().isoformat() if as_date else read.isoformat()
        if _later(value, record.get(field), as_date=as_date):
            record[field] = value
            moved += 1
    return moved


def _later(value: str, current: Any, *, as_date: bool) -> bool:
    if not current:
        return True
    if as_date:
        return value > str(current)[:10]
    try:
        before = datetime.fromisoformat(str(current).replace("Z", "+00:00"))
        return datetime.fromisoformat(value) > before
    except (TypeError, ValueError):
        return True
