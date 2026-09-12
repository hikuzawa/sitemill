"""画面の文言カタログ。サービスの i18n/<locale>.yaml を読む（ADR 0016）。

カタログに入れるのは「画面の文言」だけ。施設名や説明文のような**データ**は入れない
（データはレコードとして持ち、出典と取得日時を添える）。値は HTML ではなく平文で書く
（Jinja の自動エスケープが働くため、タグを書いても文字として出る）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class TranslationError(KeyError):
    """どのロケールにも無いキーを引いたとき。テンプレートの書き間違いなので止める。"""

    def __init__(self, key: str, locale: str) -> None:
        super().__init__(key)
        self.key = key
        self.locale = locale

    def __str__(self) -> str:
        return f"文言カタログにキーがない: {self.key!r}（locale={self.locale}）"


def flatten(data: Any, prefix: str = "") -> dict[str, str]:
    """入れ子の辞書を "nav.today" のような平らなキーにする。"""
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{name}."))
        elif value is not None:
            out[name] = str(value)
    return out


@dataclass
class Catalog:
    """1 ロケール分の文言。欠けていれば既定ロケールに落とし、記録に残す。"""

    locale: str
    entries: dict[str, str] = field(default_factory=dict)
    fallback: Catalog | None = None
    misses: set[str] = field(default_factory=set)

    def has(self, key: str) -> bool:
        return key in self.entries or (self.fallback is not None and self.fallback.has(key))

    def get(self, key: str, **params: object) -> str:
        text = self.entries.get(key)
        if text is None and self.fallback is not None:
            text = self.fallback.entries.get(key)
            if text is not None:
                # 未翻訳。ビルドは止めずに警告として集める（公開は止めない判断。ADR 0016）
                self.misses.add(key)
        if text is None:
            raise TranslationError(key, self.locale)
        if not params:
            return text
        try:
            return text.format(**params)
        except (KeyError, IndexError) as e:
            raise TranslationError(f"{key}（差し込み {e} が足りない）", self.locale) from e


def load_catalog(path: Path, locale: str, fallback: Catalog | None = None) -> Catalog:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return Catalog(locale=locale, entries=flatten(data or {}), fallback=fallback)


def load_catalogs(
    directory: Path, codes: Iterable[str], *, default_code: str
) -> dict[str, Catalog]:
    """i18n/<code>.yaml をまとめて読む。ファイルが無いロケールは空のカタログになる。

    既定ロケールのカタログが他のロケールの受け皿（fallback）になる。
    """
    codes = list(codes)
    base = load_catalog(directory / f"{default_code}.yaml", default_code)
    out = {default_code: base}
    for code in codes:
        if code == default_code:
            continue
        out[code] = load_catalog(directory / f"{code}.yaml", code, fallback=base)
    return out
