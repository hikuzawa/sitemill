"""日本時間の扱いを 1 か所に集める（ADR 0018）。

対象サイトはすべて日本の公式サイトで、開館日・運行日・告知の日付はすべて日本時間で書かれている。
一方で日次実行は GitHub Actions（UTC）で走る。`date.today()` や `utcnow().date()` を使うと、
JST の朝 6 時に「前日」の判定を出してしまう。日付が絡む計算は必ずこのモジュールを通す。

OS の時刻帯データベースが無い環境（Windows）があるため固定オフセットで表す（JST に夏時間は無い）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")


def to_jst(value: datetime) -> datetime:
    """時刻帯つきの datetime を JST に変換する。時刻帯が無いものは JST とみなす。"""
    return value.astimezone(JST) if value.tzinfo else value.replace(tzinfo=JST)


def jst_now(now: datetime | None = None) -> datetime:
    """現在時刻（JST）。テストからは now を渡す。"""
    return to_jst(now if now is not None else datetime.now(UTC))


def jst_today(now: datetime | None = None) -> date:
    """「今日」の日付（JST）。判定の基準日はすべてこれで決める。"""
    return jst_now(now).date()


def jst_datetime(day: date, clock: time | None = None) -> datetime:
    """日付（＋時刻）を JST の datetime にする。"""
    return datetime.combine(day, clock or time(0, 0), tzinfo=JST)
