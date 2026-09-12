"""schema.org の JSON-LD から営業時間を読む（ADR 0004・0018）。

施設が自分のページに `openingHoursSpecification` を書いていることがある。これは
**施設自身による機械可読の宣言**で、人が読む本文よりも曖昧さが無い。LLM に本文から
探させる前にここを見る。

    <script type="application/ld+json">
    {"@type": "TouristAttraction", "name": "…",
     "openingHoursSpecification": {
       "dayOfWeek": ["Monday", …, "Sunday"], "opens": "09:00", "closes": "17:00"}}
    </script>

これを入れた理由は実際の取りこぼしである。二十四の瞳映画村は本文に営業時間を書いておらず、
フッターに「AM9:00〜PM5:00」があるだけだった。フッターの時刻は TEL・FAX の隣にあるので
電話の受付時間とも読めて、営業時間の根拠にはできない。一方 JSON-LD には
`TouristAttraction` の `openingHoursSpecification` が 9:00–17:00 で書かれていた。

読まないもの:
- `Organization` / `LocalBusiness` 以外に付いた時間のうち、`@type` が場所でも組織でもないもの
- `opens` と `closes` が揃っていないもの（片側だけでは時間帯にならない）
- 曜日の語が読めないもの（推測しない）

`Organization` の時間は事務所の受付時間である場合があるため、場所・施設の型を優先し、
場所の型が 1 つも無いときにだけ組織の時間を使う（その旨を注記に残す）。
"""

from __future__ import annotations

import json
import re
from datetime import time
from typing import Any

from sitemill.models.schedule import DaySelector, HoursPeriod, TimeRange

_SCRIPT = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)
# schema.org の曜日。URL 形（https://schema.org/Monday）でも書かれる
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "publicholidays": -1,  # 祝日。曜日ではないので別扱い
}
# 短縮形（`openingHours` の "Mo-Su 09:00-17:00" 形式）
_SHORT_WEEKDAYS = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
# 場所・施設の型。これらの時間を優先する
_PLACE_TYPES = {
    "place",
    "touristattraction",
    "touristdestination",
    "museum",
    "civicstructure",
    "park",
    "landmarksorhistoricalbuildings",
    "aquarium",
    "zoo",
    "amusementpark",
    "artgallery",
    "performingartstheater",
    "library",
    "campground",
    "beach",
    "placeofworship",
    "buddhisttemple",
    "hindutemple",
    "church",
    "stadiumorarena",
    "movietheater",
    "touristinformationcenter",
}
_ORG_TYPES = {"organization", "localbusiness", "corporation", "governmentorganization"}
_TIME = re.compile(r"^\s*(?P<h>\d{1,2}):(?P<m>\d{2})(?::\d{2})?\s*$")
_SHORT_SPEC = re.compile(
    r"(?P<days>(?:[A-Za-z]{2}(?:\s*[-–]\s*[A-Za-z]{2})?)(?:\s*,\s*[A-Za-z]{2}"
    r"(?:\s*[-–]\s*[A-Za-z]{2})?)*)\s+(?P<from>\d{1,2}:\d{2})\s*[-–]\s*(?P<to>\d{1,2}:\d{2})"
)


def iter_jsonld(html: str) -> list[dict[str, Any]]:
    """HTML 中の JSON-LD を平らな辞書の並びにする。`@graph` と配列は展開する。

    壊れた JSON は無視する（1 つ壊れていても他を読む）。
    """
    out: list[dict[str, Any]] = []

    def push(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                push(item)
            return
        if not isinstance(node, dict):
            return
        graph = node.get("@graph")
        if graph is not None:
            push(graph)
        out.append(node)
        # 入れ子（containsPlace, subOrganization など）も見る
        for key in ("containsPlace", "location", "subOrganization", "department", "mainEntity"):
            if key in node:
                push(node[key])

    for m in _SCRIPT.finditer(html):
        raw = m.group(1).strip()
        try:
            push(json.loads(raw))
        except (ValueError, TypeError):
            continue
    return out


def _types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type") or node.get("type") or ()
    if isinstance(value, str):
        value = (value,)
    return {str(v).rsplit("/", 1)[-1].lower() for v in value}


def _clock(value: Any) -> time | None:
    if not isinstance(value, str):
        return None
    m = _TIME.match(value)
    if m is None:
        return None
    hour, minute = int(m.group("h")), int(m.group("m"))
    if hour == 24 and minute == 0:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _days(value: Any) -> tuple[tuple[int, ...], bool] | None:
    """`dayOfWeek` を (曜日, 祝日を含むか) にする。読めない語が混ざれば None。"""
    if value is None:
        return ((), False)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    weekdays: list[int] = []
    holidays = False
    for item in value:
        if not isinstance(item, str):
            return None
        key = item.rsplit("/", 1)[-1].strip().lower().replace(" ", "")
        index = _WEEKDAYS.get(key)
        if index is None:
            return None
        if index < 0:
            holidays = True
        else:
            weekdays.append(index)
    return (tuple(sorted(set(weekdays))), holidays)


def _selector(weekdays: tuple[int, ...], holidays: bool) -> DaySelector:
    if len(weekdays) == 7:  # 全曜日は「毎日」。曜日で絞らない
        return DaySelector(include_holidays=False)
    return DaySelector(weekdays=weekdays, include_holidays=holidays)


def _specs(node: dict[str, Any]) -> list[dict[str, Any]]:
    value = node.get("openingHoursSpecification")
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _periods_from_specs(node: dict[str, Any]) -> list[HoursPeriod]:
    periods: list[HoursPeriod] = []
    for spec in _specs(node):
        if spec.get("validFrom") or spec.get("validThrough"):
            continue  # 期間限定の時間。恒常の営業時間として採らない
        start, end = _clock(spec.get("opens")), _clock(spec.get("closes"))
        if start is None or end is None:
            continue
        days = _days(spec.get("dayOfWeek"))
        if days is None:
            continue
        weekdays, holidays = days
        periods.append(
            HoursPeriod(
                ranges=[TimeRange(start=start, end=end)],
                days=_selector(weekdays, holidays),
            )
        )
    return periods


def _periods_from_short(node: dict[str, Any]) -> list[HoursPeriod]:
    """`"openingHours": "Mo-Su 09:00-17:00"` の形。"""
    value = node.get("openingHours")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    periods: list[HoursPeriod] = []
    for item in value:
        if not isinstance(item, str):
            continue
        m = _SHORT_SPEC.search(item)
        if m is None:
            continue
        start, end = _clock(m.group("from")), _clock(m.group("to"))
        if start is None or end is None:
            continue
        weekdays = _short_days(m.group("days"))
        if weekdays is None:
            continue
        periods.append(
            HoursPeriod(
                ranges=[TimeRange(start=start, end=end)],
                days=_selector(weekdays, False),
                label=item[:60],
            )
        )
    return periods


def _short_days(text: str) -> tuple[int, ...] | None:
    out: list[int] = []
    for part in text.split(","):
        part = part.strip().lower()
        if "-" in part or "–" in part:
            first, _, last = part.replace("–", "-").partition("-")
            a, b = _SHORT_WEEKDAYS.get(first.strip()), _SHORT_WEEKDAYS.get(last.strip())
            if a is None or b is None:
                return None
            out.extend(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])
            continue
        index = _SHORT_WEEKDAYS.get(part)
        if index is None:
            return None
        out.append(index)
    return tuple(sorted(set(out)))


def _quote(node: dict[str, Any], periods: list[HoursPeriod]) -> str:
    """根拠として表示する引用。JSON-LD の該当部分をそのまま出す。"""
    del periods
    # `@type` は原文のまま出す（引用は根拠として表示するので、こちらで整形しない）
    body: dict[str, Any] = {"@type": node.get("@type") or node.get("type")}
    if node.get("name"):
        body["name"] = node["name"]
    for key in ("openingHoursSpecification", "openingHours"):
        if key in node:
            body[key] = node[key]
    return json.dumps({k: v for k, v in body.items() if v is not None}, ensure_ascii=False)


def opening_hours(html: str) -> tuple[list[HoursPeriod] | None, str | None, str | None]:
    """JSON-LD から営業時間を読む。返り値は (値, 引用, 注記)。

    値にできなければ (None, None, 理由) を返す。quote-then-parse の約束どおり、
    値と一緒に原文（ここでは JSON-LD の該当部分）を必ず返す。
    """
    nodes = iter_jsonld(html)
    if not nodes:
        return None, None, "no_jsonld"
    fallback: tuple[list[HoursPeriod], dict[str, Any]] | None = None
    for node in nodes:
        periods = _periods_from_specs(node) or _periods_from_short(node)
        if not periods:
            continue
        types = _types(node)
        if types & _PLACE_TYPES:
            return periods, _quote(node, periods), "jsonld_place"
        if fallback is None and types & _ORG_TYPES:
            fallback = (periods, node)
    if fallback is not None:
        # 組織にだけ時間が付いている。事務所の受付時間である可能性を注記に残す
        periods, node = fallback
        return periods, _quote(node, periods), "jsonld_organization"
    return None, None, "no_opening_hours"
