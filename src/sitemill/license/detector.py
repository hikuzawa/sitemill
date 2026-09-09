"""ページ内の明示的な表記だけからライセンスを判定する。曖昧なら不採用（ADR 0005）。"""

from __future__ import annotations

import re

from sitemill.diff.normalize import page_text
from sitemill.models import LicenseId, LicenseVerdict
from sitemill.models.license import LICENSE_LABELS

_TAG = re.compile(r"<[^>]+>")
_ALLOW: tuple[tuple[re.Pattern[str], LicenseId], ...] = (
    (re.compile(r"creativecommons\.org/licenses/by/4\.0", re.I), LicenseId.CC_BY_4_0),
    (re.compile(r"creativecommons\.org/licenses/by/3\.0", re.I), LicenseId.CC_BY_3_0),
    (re.compile(r"creativecommons\.org/licenses/by/2\.1/jp", re.I), LicenseId.CC_BY_2_1_JP),
    (re.compile(r"creativecommons\.org/publicdomain/zero/1\.0", re.I), LicenseId.CC0_1_0),
    (re.compile(r"政府標準利用規約\s*[（(]\s*第\s*2\.0\s*版\s*[）)]"), LicenseId.GSTU_2_0),
    (re.compile(r"(?<![\w-])CC[\s\-]?BY[\s\-]?4\.0(?![\w.-])", re.I), LicenseId.CC_BY_4_0),
    (re.compile(r"クリエイティブ・?コモンズ[・\s]*表示\s*4\.0"), LicenseId.CC_BY_4_0),
    (re.compile(r"(?<![\w-])CC0(?:\s*1\.0)?(?![\w.-])"), LicenseId.CC0_1_0),
)
_DENY = (
    re.compile(r"creativecommons\.org/licenses/by-(nc|nd|sa)", re.I),
    re.compile(r"(?<![\w-])CC[\s\-]?BY[\s\-]?(NC|ND|SA)", re.I),
    re.compile(r"非営利|改変禁止|継承\s*4\.0|転載を?禁|無断転載|All Rights Reserved", re.I),
)


def _evidence(text: str, m: re.Match[str], width: int = 80) -> str:
    """一致箇所の前後を返す。前後の HTML タグは落とし、一致した文字列そのものは残す。"""
    before = _TAG.sub(" ", text[max(0, m.start() - width) : m.start()])
    after = _TAG.sub(" ", text[m.end() : m.end() + width])
    return " ".join(f"{before}{m.group(0)}{after}".split())


def detect_license(html: str, url: str, *, credit_name: str) -> LicenseVerdict:
    """HTML（リンク URL 込み）と本文から判定する。許可と拒否の表記が両方あれば不採用。"""
    text = page_text(html)
    haystacks = (html, text)
    allow_hit: tuple[LicenseId, str] | None = None
    for hay in haystacks:
        for pattern, lic in _ALLOW:
            m = pattern.search(hay)
            if m:
                allow_hit = (lic, _evidence(hay, m))
                break
        if allow_hit:
            break
    deny_hit = next((p.pattern for hay in haystacks for p in _DENY if p.search(hay)), None)

    if allow_hit and deny_hit is None:
        lic, evidence = allow_hit
        return LicenseVerdict.granted(
            lic,
            evidence_url=url,
            evidence_text=evidence[:300],
            credit_text=f"出典: {credit_name}（{LICENSE_LABELS[lic]}）",
        )
    if allow_hit and deny_hit is not None:
        return LicenseVerdict.denied(f"許可と制限の表記が混在（{deny_hit}）", url=url)
    if deny_hit is not None:
        return LicenseVerdict.denied(f"利用を制限する表記あり（{deny_hit}）", url=url)
    return LicenseVerdict.denied("明示的なライセンス表記が見つからない", url=url)
