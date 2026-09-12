"""日本の祝日（ADR 0018）。一次データを優先し、無ければ法律の規則から計算する。

一次データは内閣府「国民の祝日について」の CSV（政府標準利用規約 第 2.0 版＝ライセンスの
ホワイトリスト適合）。サービスが `data/reference/` に取得日付きで保存し、ここから読む。

取得できない・範囲外の年は規則から計算する。計算は 1949 年以降の現行法（2020 年改正まで）に
基づき、春分・秋分は 1980〜2099 年で正しい近似式を使う。範囲外の年は「祝日かどうか不明」として
扱えるよう、`HolidayCalendar.covers()` で問い合わせられるようにしている。

なぜ両方持つか: 一次データがあるならそれが正しい（五輪の年のような一度だけの移動がある）。
一方で、判定を毎日回すのに外部ファイルの取得成功を前提にすると、取得できない日に祝日が消えて
「開館」と誤判定する。計算は落ちないための下支え。
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# 内閣府 CSV の配布元。取得はサービス側が PoliteClient で行い、ここでは読むだけ。
CABINET_OFFICE_CSV_URL = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"
# 計算で正しさを保証する範囲（春分・秋分の近似式の有効範囲）。
COMPUTED_FROM_YEAR = 1980
COMPUTED_TO_YEAR = 2099

_MONTH_DAY = re.compile(r"^\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*$")


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """その月の第 nth <weekday> の日付。weekday は 0=月曜。"""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (nth - 1))


def _vernal_equinox(year: int) -> int:
    """春分の日（日）。1980〜2099 年で正しい近似式。"""
    return int(20.8431 + 0.242194 * (year - 1980) - (year - 1980) // 4)


def _autumnal_equinox(year: int) -> int:
    """秋分の日（日）。1980〜2099 年で正しい近似式。"""
    return int(23.2488 + 0.242194 * (year - 1980) - (year - 1980) // 4)


def fixed_holidays(year: int) -> dict[date, str]:
    """振替休日・国民の休日を除いた、その年の祝日。"""
    days: dict[date, str] = {
        date(year, 1, 1): "元日",
        date(year, 2, 11): "建国記念の日",
        date(year, 4, 29): "昭和の日",
        date(year, 5, 3): "憲法記念日",
        date(year, 5, 4): "みどりの日",
        date(year, 5, 5): "こどもの日",
        date(year, 8, 11): "山の日",
        date(year, 11, 3): "文化の日",
        date(year, 11, 23): "勤労感謝の日",
        date(year, 3, _vernal_equinox(year)): "春分の日",
        date(year, 9, _autumnal_equinox(year)): "秋分の日",
        _nth_weekday(year, 1, 0, 2): "成人の日",
        _nth_weekday(year, 7, 0, 3): "海の日",
        _nth_weekday(year, 9, 0, 3): "敬老の日",
        _nth_weekday(year, 10, 0, 2): "スポーツの日",
    }
    if year >= 2020:
        days[date(year, 2, 23)] = "天皇誕生日"
    return days


def computed_holidays(year: int) -> dict[date, str]:
    """振替休日と国民の休日まで含めた、その年の休日。

    振替休日: 祝日が日曜のとき、その後の最初の「祝日でない日」を休日にする。
      祝日が連続していると 2 日以上先へ動く（5/3 が日曜なら 5/6 が振替）。
    国民の休日: 前後どちらも祝日である平日を休日にする（9 月の敬老の日と秋分の日の間など）。
    """
    days = dict(fixed_holidays(year))
    # 年をまたぐ連休（1/1 が日曜など）を正しく扱うため、前後の年の祝日も見る
    neighbors = {**fixed_holidays(year - 1), **days, **fixed_holidays(year + 1)}

    substitutes: dict[date, str] = {}
    for day in sorted(neighbors):
        if day.weekday() != 6:  # 日曜のみ
            continue
        candidate = day + timedelta(days=1)
        while candidate in neighbors:
            candidate = candidate + timedelta(days=1)
        if candidate.year == year:
            substitutes[candidate] = "休日（振替休日）"
    days.update(substitutes)

    bridges: dict[date, str] = {}
    known = {**neighbors, **substitutes}
    for day in sorted(neighbors):
        middle = day + timedelta(days=1)
        after = day + timedelta(days=2)
        if middle in known or after not in known:
            continue
        if middle.weekday() == 6 or middle.year != year:
            continue  # 日曜は振替の対象。年が違えばその年の計算に任せる
        bridges[middle] = "休日（国民の休日）"
    days.update(bridges)
    return days


def load_cabinet_office_csv(path: Path) -> dict[date, str]:
    """内閣府の CSV（Shift_JIS、「月日,名称」）を読む。読めない行は捨てる。"""
    rows: dict[date, str] = {}
    with path.open(encoding="cp932", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            m = _MONTH_DAY.match(row[0])
            if m is None:
                continue  # 見出し行
            y, mo, d = (int(g) for g in m.groups())
            rows[date(y, mo, d)] = row[1].strip()
    if not rows:
        raise ValueError(f"{path}: 祝日の行が 1 件も読めなかった")
    return rows


@dataclass
class HolidayCalendar:
    """祝日の問い合わせ口。一次データを優先し、無い年は計算で埋める。

    層は 3 つある。
    - `primary`: 内閣府 CSV。**その年の全休日が入っている**前提で、ある年は計算より優先する
    - 計算: `primary` に無い年を規則から埋める
    - `extra`: サービスが足す休日（町民の日など）。年ごとの優先には関わらず、常に上に重ねる

    `primary` と `extra` を分けているのは、数日足しただけでその年が「一次データのある年」に
    化けると、計算で出ていた祝日が消えてしまうため（休館日を開館と誤判定する）。

    `covers()` が False の年は「祝日かどうか分からない」。判定側はそれを unknown の材料にできる。
    """

    primary: dict[date, str] = field(default_factory=dict)
    extra: dict[date, str] = field(default_factory=dict)
    source_url: str | None = None
    fetched_on: date | None = None
    computed_from: int = COMPUTED_FROM_YEAR
    computed_to: int = COMPUTED_TO_YEAR
    _computed: dict[int, dict[date, str]] = field(default_factory=dict, repr=False)

    @classmethod
    def computed(cls) -> HolidayCalendar:
        """一次データを使わず、規則の計算だけで答える暦（テストと下支え用）。"""
        return cls()

    @classmethod
    def from_csv(
        cls, path: Path, *, source_url: str | None = None, fetched_on: date | None = None
    ) -> HolidayCalendar:
        return cls(
            primary=load_cabinet_office_csv(path),
            source_url=source_url or CABINET_OFFICE_CSV_URL,
            fetched_on=fetched_on,
        )

    @classmethod
    def load(
        cls, path: Path | None, *, source_url: str | None = None, fetched_on: date | None = None
    ) -> HolidayCalendar:
        """CSV があれば読み、無ければ計算だけの暦を返す（取得失敗で判定を落とさない）。"""
        if path is not None and path.is_file():
            try:
                return cls.from_csv(path, source_url=source_url, fetched_on=fetched_on)
            except (OSError, ValueError) as e:
                log.warning("祝日 CSV を読めなかったので計算で代替する（%s）: %s", path, e)
        return cls.computed()

    @property
    def primary_years(self) -> set[int]:
        return {d.year for d in self.primary}

    def covers(self, day: date) -> bool:
        """その日について答えられるか。一次データのある年か、計算できる年なら True。"""
        if day in self.extra:
            return True
        return day.year in self.primary_years or self.computed_from <= day.year <= self.computed_to

    def _for_year(self, year: int) -> dict[date, str]:
        if year in self.primary_years:
            return {d: name for d, name in self.primary.items() if d.year == year}
        if year not in self._computed:
            self._computed[year] = computed_holidays(year)
        return self._computed[year]

    def name(self, day: date) -> str | None:
        """祝日名。祝日でなければ None。答えられない年も None（`covers` で見分ける）。"""
        if day in self.extra:
            return self.extra[day]
        if not self.covers(day):
            return None
        return self._for_year(day.year).get(day)

    def is_holiday(self, day: date) -> bool:
        return self.name(day) is not None

    def next_non_holiday(self, day: date, *, limit: int = 14) -> date | None:
        """その日以降で最初の「祝日でない日」。答えられる範囲を超えたら None。"""
        cursor = day
        for _ in range(limit):
            if not self.covers(cursor):
                return None
            if not self.is_holiday(cursor):
                return cursor
            cursor = cursor + timedelta(days=1)
        return None

    def between(self, start: date, end: date) -> Iterator[tuple[date, str]]:
        cursor = start
        while cursor <= end:
            name = self.name(cursor)
            if name is not None:
                yield cursor, name
            cursor = cursor + timedelta(days=1)

    def merged_with(self, extra: Iterable[tuple[date, str]]) -> HolidayCalendar:
        """休日を足した暦を返す（サービスが独自の休日を足すとき）。

        足した日は `extra` に入る。`primary` には入れない（その年の一次データが揃っている
        という意味になり、計算で出ていた祝日を消してしまう）。
        """
        merged = dict(self.extra)
        merged.update(extra)
        return HolidayCalendar(
            primary=dict(self.primary),
            extra=merged,
            source_url=self.source_url,
            fetched_on=self.fetched_on,
            computed_from=self.computed_from,
            computed_to=self.computed_to,
        )
