"""所要時間のパーサ（ADR 0018）。「約60分」「1時間30分」を分にする。

第 2 フェーズの旅程生成で使う。相対的・曖昧な表現（「半日」「ゆっくり」）は値にしない。
"""

from __future__ import annotations

import re

from sitemill.parse.jp.numbers import normalize_text

_HOURS_MINUTES = re.compile(r"(?P<h>\d{1,2})\s*時間\s*(?:(?P<m>\d{1,2})\s*分)?")
_MINUTES = re.compile(r"(?P<m>\d{1,3})\s*分")
_VAGUE = re.compile(r"半日|終日|一日|ゆっくり|人により|個人差")


def parse_minutes(text: str) -> tuple[int | None, str | None]:
    """所要時間を分で返す。返り値は (値, 失敗理由)。"""
    t = normalize_text(text)
    if not t:
        return None, "no_text"
    m = _HOURS_MINUTES.search(t)
    if m is not None:
        minutes = int(m.group("h")) * 60 + int(m.group("m") or 0)
        note = "approx" if "約" in t or "およそ" in t else None
        return (minutes, note) if 0 < minutes <= 24 * 60 else (None, "out_of_range")
    m = _MINUTES.search(t)
    if m is not None:
        minutes = int(m.group("m"))
        note = "approx" if "約" in t or "およそ" in t else None
        return (minutes, note) if 0 < minutes <= 24 * 60 else (None, "out_of_range")
    if _VAGUE.search(t):
        return None, "vague"
    return None, "no_duration"
