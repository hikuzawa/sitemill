"""信頼シグナルの検査。全ページに必須（ADR 0007）。"""

from __future__ import annotations

import re

TRUST_MARKER = "data-sitemill-trust"
_TITLE = re.compile(r"<title>[^<]+</title>", re.I)
_LANG = re.compile(r"<html[^>]*\slang=", re.I)


def verify_page_html(html: str) -> list[str]:
    """問題点の一覧を返す。空なら合格。"""
    problems: list[str] = []
    if TRUST_MARKER not in html:
        problems.append("信頼シグナルのブロック（data-sitemill-trust）が無い")
    if not _TITLE.search(html):
        problems.append("<title> が無い")
    if not _LANG.search(html):
        problems.append("<html lang> が無い")
    return problems
