"""テスト用の最小サービス。sitemill のコマンドとビルドを外部依存なしで通す。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.extract.spec import ExtractedItem, ExtractionSpec, QuoteField
from sitemill.models import (
    CrawlPolicy,
    FollowRule,
    OperatorEvidence,
    OperatorInfo,
    OperatorKind,
    Page,
    PageKind,
    PageMeta,
    Provenance,
    Redirect,
    SeedPage,
    Source,
    SourceLink,
    TrustSignals,
    utcnow,
)
from sitemill.parse.jp import parse_yen
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

SPEC = ExtractionSpec(
    name="listing",
    prompt_version="dummy_v1",
    system_prompt="引用だけを返す",
    output_schema={"type": "object"},
    quote_fields=(
        QuoteField("listing_no_quote", "listing_no", None),
        QuoteField("price_quote", "price", parse_yen),
    ),
    items_key="listings",
)


class DummyService:
    id = "dummy"

    def __init__(self, base: str = "https://akiya.example") -> None:
        self.base = base

    def sources(self, ws: Workspace) -> list[Source]:
        return [
            Source(
                id="dummy-city",
                name="ダミー市",
                operator="ダミー市",
                operator_kind=OperatorKind.municipality,
                operator_evidence=OperatorEvidence(quote="運営: ダミー市", url=self.base + "/"),
                policy=CrawlPolicy.crawl,
                official_url=self.base + "/",
                pages=[
                    SeedPage(
                        url=self.base + "/",
                        kind=PageKind.listing_index,
                        follow=[FollowRule(pattern=r"/bukken/\d+", kind=PageKind.listing_detail)],
                    )
                ],
            )
        ]

    def extraction_spec(self, kind: str) -> ExtractionSpec | None:
        return SPEC if kind in ("listing_index", "listing_detail") else None

    def ingest(
        self,
        ws: Workspace,
        *,
        source: Source,
        url: str,
        kind: str,
        items: Sequence[ExtractedItem],
        provenance: Provenance,
    ) -> dict[str, int]:
        store = RecordStore(ws.records_dir / f"{source.id}.jsonl")
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        for item in items:
            no = item.value("listing_no")
            if not no:
                continue
            content = {
                "listing_no": no,
                "price": item.fields["price"].model_dump(mode="json"),
                "title": item.free.get("title"),
                "source_url": url,
            }
            result = store.upsert(
                f"{source.id}:{no}",
                content,
                now=utcnow(),
                provenance=provenance.model_dump(mode="json"),
            )
            counts[result] += 1
        store.save()
        return counts

    def finalize(self, ws: Workspace, *, now: datetime) -> None:
        return None

    def _records(self, ws: Workspace) -> list[dict[str, Any]]:
        return RecordStore(ws.records_dir / "dummy-city.jsonl").all()

    def pages(self, ws: Workspace, *, now: datetime) -> list[Page]:
        records = self._records(ws)
        trust = TrustSignals(
            updated_at=now,
            sources=[
                SourceLink(label="ダミー市 空き家バンク", url=self.base + "/", fetched_at=now)
            ],
            operator=OperatorInfo(name=ws.site.operator.name, contact=ws.site.operator.contact),
            record_count=len(records),
        )
        pages = [
            Page(
                meta=PageMeta(title="トップ", path="index.html", description="テスト"),
                template="index.html",
                context={"records": records},
                trust=trust,
            )
        ]
        if (ws.templates_dir / "broken.html").is_file():
            pages.append(
                Page(
                    meta=PageMeta(title="壊れ", path="broken/index.html"),
                    template="broken.html",
                    trust=trust,
                )
            )
        return pages

    def search_index(self, ws: Workspace) -> Any:
        return [{"id": r["record_id"], "no": r["listing_no"]} for r in self._records(ws)]

    def redirects(self, ws: Workspace) -> list[Redirect]:
        return [Redirect(from_path="/go/test", to_url="https://example.com/offer", status=302)]

    def eval_dir(self, ws: Workspace) -> Path | None:
        return None


service = DummyService()

SITE_TOML = """
[site]
id = "dummy"
name = "ダミー"
base_url = "https://dummy.example"
service = "tests.dummy_service:service"

[operator]
name = "テスト運営"
contact = "test@example.com"

[crawl]
default_delay_seconds = 0
jitter_seconds = 0

[llm]
provider = "fixture"
"""

BASE_TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>{{ meta.title }} | {{ site.name }}</title>{{ analytics }}</head>
<body>{% import "sitemill/macros.html" as sm %}
<main>{% block content %}{% endblock %}</main>
{{ sm.trust_block(trust) }}
</body></html>
"""

INDEX_TEMPLATE = """{% extends "base.html" %}
{% block content %}<h1>{{ meta.title }}</h1>
<ul>{% for r in records %}<li>{{ r.listing_no }}: {{ r.price.value|yen }}</li>{% endfor %}</ul>
{% endblock %}
"""


def make_workspace(root: Path) -> Path:
    """site.toml とテンプレートを持つ最小のサービスルートを作る。"""
    (root / "site.toml").write_text(SITE_TOML, encoding="utf-8")
    (root / "templates").mkdir(exist_ok=True)
    (root / "templates" / "base.html").write_text(BASE_TEMPLATE, encoding="utf-8")
    (root / "templates" / "index.html").write_text(INDEX_TEMPLATE, encoding="utf-8")
    (root / "static").mkdir(exist_ok=True)
    (root / "static" / "style.css").write_text("body{}", encoding="utf-8")
    return root
