"""取り込んだ数字を、人が読んで次の一手を決められる形にする（ADR 0023）。

出すもの:

1. **インデックスの進み具合** — サイトマップの送信数と、URL 検査で「登録済み」と分かった数。
   最初の数週間はこれが一番知りたい数字なので先頭に置く。**登録されなかった理由の内訳**
   （Search Console の表記・件数・先週比・例）と、www・http の転送が転送として数えられているか
2. 検索パフォーマンスの合計と前の期間との差
3. 表示が多いのに CTR が低いページ
4. 順位が落ちたページ
5. 新しく表示され始めたページ
6. まだ表示されていないページ（サイトマップにあるのに表示 0）
7. 検索語（上位と、新しく出てきたもの）

**データが 0 でも読める形にする**。「まだ表示回数がありません」と、いつから数えているかを出す。
数字が無いことと、集計が壊れていることを混ぜない。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sitemill.clock import jst_today
from sitemill.search.store import Fact, SearchStore

# 「表示は多いが CTR が低い」とみなす境目
MIN_IMPRESSIONS = 30
LOW_CTR = 0.02
# 「順位が落ちた」とみなす差（平均掲載順位の悪化。数字が大きいほど下）
DROP_POSITION = 3.0
MIN_IMPRESSIONS_FOR_RANK = 10
TOP_N = 10
# 「登録済み」と読める URL 検査の状態
INDEXED_VERDICT = "PASS"
# 理由ごとに出す例の数
EXAMPLES = 3

# URL 検査の状態（API の表記）→ Search Console の画面の表記と、読み方。
# **Search Console API は「ページのインデックス作成」レポートの件数を返さない**。ここで数えるのは
# 自分で URL 検査した分（サイトマップの URL）なので、画面の件数とは一致しないことがある
COVERAGE_LABELS: dict[str, tuple[str, str]] = {
    "Submitted and indexed": ("送信して登録済み", ""),
    "Indexed, not submitted in sitemap": ("登録済み（サイトマップ未送信）", ""),
    "Discovered - currently not indexed": (
        "検出 - インデックス未登録",
        "URL は知られているがまだクロールされていない。新しいサイトでは普通で、待つ",
    ),
    "Crawled - currently not indexed": (
        "クロール済み - インデックス未登録",
        "クロールしたうえで登録を見送った。内容が薄い・似たページが多いと起きやすい。"
        "増え続けるなら例のページの中身を見る",
    ),
    "URL is unknown to Google": (
        "Google がまだ知らない URL",
        "画面のレポートには出ない、URL 検査だけの状態。サイトマップの再取得を待つ",
    ),
    "Page with redirect": (
        "ページにリダイレクトがあります",
        "サイトマップの URL が転送している。サイト内リンクかサイトマップを直す",
    ),
    "Duplicate, Google chose different canonical than user": (
        "重複（Google が別のページを正規ページに選択）",
        "例の矢印の先が Google の選んだ正規ページ。内容が重なっていないか見る",
    ),
    "Duplicate without user-selected canonical": (
        "重複（正規ページの指定なし）",
        "canonical が無いか読まれていない",
    ),
    "Alternate page with proper canonical tag": (
        "適切な canonical タグ付きの代替ページ",
        "意図どおりなら問題ない",
    ),
    "Excluded by ‘noindex’ tag": ("「noindex」タグによって除外", "意図どおりか確かめる"),
    "Not found (404)": (
        "見つかりませんでした（404）",
        "サイトマップに消えたページが残っていないか",
    ),
    "Soft 404": ("ソフト 404", "中身の無いページになっていないか"),
    "Blocked by robots.txt": ("robots.txt によりブロック", "robots.txt を直す"),
    "Server error (5xx)": ("サーバーエラー（5xx）", "配置先の障害を確かめる"),
}


@dataclass
class PageStat:
    page: str
    clicks: int = 0
    impressions: int = 0
    position_sum: float = 0.0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def position(self) -> float:
        return self.position_sum / self.impressions if self.impressions else 0.0


@dataclass
class IndexSummary:
    submitted: int = 0  # サイトマップに載せて送った URL 数（Google が最後に取得したもの）
    sitemap_last_downloaded: str = ""
    sitemap_errors: int = 0
    sitemap_warnings: int = 0
    site_urls: int = 0  # いまサイトにある URL 数
    inspected: int = 0  # 検査できた URL 数
    indexed: int = 0  # そのうち登録済み
    states: dict[str, int] = field(default_factory=dict)
    # 理由ごとの例（URL、重複なら「URL → Google の選んだ正規ページ」）
    examples: dict[str, list[str]] = field(default_factory=dict)
    # 1 週間前（以前で一番近い日）の状態ごとの件数。無ければ空
    previous_day: str = ""
    previous_states: dict[str, int] = field(default_factory=dict)
    # www・http の形の検査結果（転送の確認）
    variants: dict[str, dict[str, str]] = field(default_factory=dict)
    oldest_check: str = ""
    newest_check: str = ""

    @property
    def coverage(self) -> float:
        return self.inspected / self.site_urls if self.site_urls else 0.0

    @property
    def indexed_share(self) -> float:
        return self.indexed / self.inspected if self.inspected else 0.0


@dataclass
class Summary:
    start: date
    end: date
    days: int
    clicks: int = 0
    impressions: int = 0
    position: float = 0.0
    prev_clicks: int = 0
    prev_impressions: int = 0
    prev_position: float = 0.0
    index: IndexSummary = field(default_factory=IndexSummary)
    low_ctr: list[PageStat] = field(default_factory=list)
    dropped: list[tuple[str, float, float]] = field(default_factory=list)
    new_pages: list[PageStat] = field(default_factory=list)
    silent_pages: list[str] = field(default_factory=list)
    top_queries: list[tuple[str, int, int, float]] = field(default_factory=list)
    new_queries: list[str] = field(default_factory=list)
    first_day: date | None = None

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def has_data(self) -> bool:
        return self.impressions > 0


def _totals(facts: list[Fact]) -> tuple[int, int, float]:
    clicks = sum(f.clicks for f in facts)
    impressions = sum(f.impressions for f in facts)
    weighted = sum(f.position * f.impressions for f in facts)
    return clicks, impressions, (weighted / impressions if impressions else 0.0)


def _by_page(facts: list[Fact]) -> dict[str, PageStat]:
    out: dict[str, PageStat] = {}
    for f in facts:
        page = f.keys[0] if f.keys else ""
        stat = out.setdefault(page, PageStat(page=page))
        stat.clicks += f.clicks
        stat.impressions += f.impressions
        stat.position_sum += f.position * f.impressions
    return out


def summarise(
    store: SearchStore,
    *,
    site_urls: list[str] | None = None,
    days: int = 7,
    today: date | None = None,
) -> Summary:
    # 日付は必ず日本時間で決める（ADR 0018）。実行環境は UTC なので、JST の朝に前日になる
    end = today or jst_today()
    start = end - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    pages = store.read("page", since=prev_start - timedelta(days=1))
    now_facts = [f for f in pages if start <= f.day <= end]
    prev_facts = [f for f in pages if prev_start <= f.day < start]
    summary = Summary(start=start, end=end, days=days)
    summary.clicks, summary.impressions, summary.position = _totals(now_facts)
    summary.prev_clicks, summary.prev_impressions, summary.prev_position = _totals(prev_facts)

    all_pages = store.read("page")
    if all_pages:
        summary.first_day = min(f.day for f in all_pages)

    now_by_page = _by_page(now_facts)
    prev_by_page = _by_page(prev_facts)

    # 3. 表示は多いが CTR が低い
    summary.low_ctr = sorted(
        (s for s in now_by_page.values() if s.impressions >= MIN_IMPRESSIONS and s.ctr < LOW_CTR),
        key=lambda s: -s.impressions,
    )[:TOP_N]

    # 4. 順位が落ちた（平均掲載順位が悪化した）
    dropped: list[tuple[str, float, float]] = []
    for page, stat in now_by_page.items():
        before = prev_by_page.get(page)
        if before is None or before.impressions < MIN_IMPRESSIONS_FOR_RANK:
            continue
        if stat.impressions < MIN_IMPRESSIONS_FOR_RANK:
            continue
        if stat.position - before.position >= DROP_POSITION:
            dropped.append((page, before.position, stat.position))
    summary.dropped = sorted(dropped, key=lambda r: r[1] - r[2])[:TOP_N]

    # 5. 新しく表示され始めたページ（この期間より前には 1 度も表示がない）
    seen_before = {f.keys[0] for f in all_pages if f.day < start and f.keys and f.impressions}
    summary.new_pages = sorted(
        (s for page, s in now_by_page.items() if page not in seen_before and s.impressions),
        key=lambda s: -s.impressions,
    )[:TOP_N]

    # 6. まだ表示されていないページ
    if site_urls:
        ever = {f.keys[0] for f in all_pages if f.keys and f.impressions}
        summary.silent_pages = sorted(u for u in site_urls if u not in ever)

    # 7. 検索語
    queries = store.read("query", since=prev_start)
    now_q: dict[str, PageStat] = {}
    for f in queries:
        if not (start <= f.day <= end) or not f.keys:
            continue
        stat = now_q.setdefault(f.keys[0], PageStat(page=f.keys[0]))
        stat.clicks += f.clicks
        stat.impressions += f.impressions
        stat.position_sum += f.position * f.impressions
    summary.top_queries = [
        (s.page, s.impressions, s.clicks, s.position)
        for s in sorted(now_q.values(), key=lambda s: -s.impressions)[:TOP_N]
    ]
    before_q = {
        f.keys[0] for f in store.read("query") if f.day < start and f.keys and f.impressions
    }
    summary.new_queries = sorted(q for q in now_q if q not in before_q)[:TOP_N]

    summary.index = index_summary(store, site_urls or [], today=end)
    return summary


def index_summary(
    store: SearchStore, site_urls: list[str], *, today: date | None = None
) -> IndexSummary:
    out = IndexSummary(site_urls=len(site_urls))
    stored = store.read_sitemaps()
    for row in stored.get("sitemaps", []):
        for content in row.get("contents", []):
            # `indexed` は Google が値を返さなくなっており、常に 0。読まない（ADR 0023）
            out.submitted += int(content.get("submitted", 0))
        out.sitemap_errors += int(row.get("errors", 0))
        out.sitemap_warnings += int(row.get("warnings", 0))
        out.sitemap_last_downloaded = row.get("lastDownloaded", out.sitemap_last_downloaded)

    urls = store.read_urls()
    checks = [v.get("checked_on", "") for v in urls.values() if v.get("checked_on")]
    out.inspected = len(urls)
    out.indexed = sum(1 for v in urls.values() if v.get("verdict") == INDEXED_VERDICT)
    states: dict[str, int] = defaultdict(int)
    for v in urls.values():
        states[v.get("coverage_state") or "（状態なし）"] += 1
    out.states = dict(sorted(states.items(), key=lambda kv: -kv[1]))
    # 例は最近クロールされたものから（古い状態より、いまの Google の判断に近い）
    ordered = sorted(
        urls.items(), key=lambda kv: (str(kv[1].get("last_crawl") or ""), kv[0]), reverse=True
    )
    for url, v in ordered:
        if v.get("verdict") == INDEXED_VERDICT:
            continue
        state = v.get("coverage_state") or "（状態なし）"
        picked = out.examples.setdefault(state, [])
        if len(picked) < EXAMPLES:
            chosen = v.get("google_canonical") or ""
            picked.append(f"{url} → {chosen}" if chosen and chosen != url else url)
    if checks:
        out.oldest_check, out.newest_check = min(checks), max(checks)

    history = store.read_state_history()
    if today is not None and history:
        week_ago = (today - timedelta(days=7)).isoformat()
        older = [day for day in history if day <= week_ago]
        if older:
            out.previous_day = max(older)
            out.previous_states = history[out.previous_day]
    out.variants = {
        url: {k: str(val) for k, val in v.items()} for url, v in store.read_variants().items()
    }
    return out


def _label(state: str) -> str:
    return COVERAGE_LABELS.get(state, (state, ""))[0]


def _not_indexed_section(idx: IndexSummary) -> list[str]:
    """登録されなかった理由の内訳。Search Console の画面と同じ表記で、件数・先週比・例を出す。"""
    reasons = {st: n for st, n in idx.states.items() if st in idx.examples}
    lines = ["#### インデックスされなかった理由（URL 検査で数えた分）", ""]
    if not reasons:
        lines += ["検査したページはすべて登録済みです。", ""]
        return lines
    since = f"{idx.previous_day} 比" if idx.previous_day else "先週比"
    lines.append(f"| 理由（Search Console の表記） | 件数 | {since} |")
    lines.append("| --- | ---: | ---: |")
    for state, n in reasons.items():
        change = f"{n - idx.previous_states.get(state, 0):+,}" if idx.previous_day else "—"
        lines.append(f"| {_label(state)} | {n:,} | {change} |")
    lines.append("")
    if not idx.previous_day:
        lines += ["先週の記録がまだ無いので、比較は次回から出ます。", ""]
    for state in reasons:
        note = COVERAGE_LABELS.get(state, ("", ""))[1]
        lines.append(f"- **{_label(state)}**" + (f" — {note}" if note else ""))
        for example in idx.examples[state]:
            lines.append(f"  - {example}")
    lines += [
        "",
        "Search Console API は画面の「ページのインデックス作成」の件数を返さない。"
        "ここはサイトマップの URL を自分で検査して数えたもので、画面の件数と一致しないことがある。",
        "",
    ]
    return lines


def _variants_section(idx: IndexSummary) -> list[str]:
    """www・http の形が「転送」として数えられているか。画面の「リダイレクト」の中身。"""
    lines = ["#### 転送の確認（www・http の形）", ""]
    for url, v in idx.variants.items():
        if v.get("error"):
            lines.append(f"- {url}: 検査できなかった（{v['error'][:80]}）")
            continue
        state = v.get("coverage_state", "")
        if state == "Page with redirect":
            verdict = "転送として数えられている（想定どおり）"
        elif v.get("verdict") == INDEXED_VERDICT:
            verdict = "本来の URL と同じページとして扱われている（想定どおり）"
        elif state == "URL is unknown to Google":
            verdict = "Google はまだ見つけていない"
        else:
            verdict = "**転送として扱われていない。配置の転送設定を確かめる**"
        lines.append(f"- {url}: {_label(state) or '状態なし'} — {verdict}")
    lines += [
        "",
        "画面の「ページにリダイレクトがあります」は、主にこの www・http の形。"
        "サイトマップの URL に転送が無ければ、対応は要らない。",
        "",
    ]
    return lines


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _delta(now: int, before: int) -> str:
    if before == 0:
        return "（前の期間は 0）" if now else ""
    diff = now - before
    return f"（前の期間比 {diff:+,}／{diff / before * 100:+.0f}%）"


def markdown(summary: Summary, *, title: str = "検索の状況（Search Console）") -> str:
    """週次レポートに差し込む節。データが 0 でも読める形にする。"""
    s = summary
    idx = s.index
    lines = [f"## {title}", ""]

    lines.append(f"### インデックス（{s.end:%Y-%m-%d} 時点）")
    lines.append("")
    if idx.site_urls:
        lines.append(f"- サイトの URL: **{idx.site_urls:,}**")
    if idx.submitted:
        downloaded = idx.sitemap_last_downloaded[:10] or "不明"
        lines.append(
            f"- サイトマップの送信: **{idx.submitted:,}** URL"
            f"（Google の最終取得 {downloaded}、エラー {idx.sitemap_errors}／"
            f"警告 {idx.sitemap_warnings}）"
        )
        if idx.site_urls and idx.submitted < idx.site_urls:
            # 送信数はサイトの URL 数ではなく「Google が最後に取得したサイトマップの中身」。
            # ページを増やした直後はここがずれる。ずれたままにせず、待ちであることを書く
            lines.append(
                f"  - いまのサイトは {idx.site_urls:,} URL。"
                f"差の {idx.site_urls - idx.submitted:,} は Google のサイトマップ再取得待ち"
            )
    else:
        lines.append("- サイトマップ: まだ Google が取得していません")
    if idx.inspected:
        lines.append(
            f"- URL 検査: **{idx.inspected:,} ページを検査して {idx.indexed:,} ページが登録済み**"
            f"（検査した分の {_pct(idx.indexed_share)}／"
            f"サイト全体の {_pct(idx.coverage)} を検査済み）"
        )
        if idx.oldest_check:
            lines.append(f"  - 検査した日: {idx.oldest_check} 〜 {idx.newest_check}")
    else:
        lines.append("- URL 検査: まだ 1 ページも検査していません")
    lines.append("")
    if idx.inspected:
        lines += _not_indexed_section(idx)
    if idx.variants:
        lines += _variants_section(idx)

    lines.append(f"### 検索パフォーマンス（{s.start:%m-%d} 〜 {s.end:%m-%d}、{s.days} 日間）")
    lines.append("")
    if not s.has_data:
        since = (
            f"{s.first_day:%Y-%m-%d} から数えています"
            if s.first_day
            else "まだ 1 日も取得していません"
        )
        lines.append(f"**まだ表示回数がありません。**（{since}）")
        lines.append("")
        lines.append(
            "Search Console は登録前にさかのぼれず、数字は 2〜3 日遅れて確定します。"
            "インデックスが進むと、まずここに表示回数が出はじめます。"
        )
        lines.append("")
        return "\n".join(lines)

    lines.append(f"- 表示 **{s.impressions:,}** {_delta(s.impressions, s.prev_impressions)}")
    lines.append(f"- クリック **{s.clicks:,}** {_delta(s.clicks, s.prev_clicks)}")
    lines.append(f"- CTR **{_pct(s.ctr)}** ／ 平均掲載順位 **{s.position:.1f}**")
    lines.append("")

    if s.top_queries:
        lines.append("### 検索語（表示の多い順）")
        lines.append("")
        lines.append("| 検索語 | 表示 | クリック | 平均順位 |")
        lines.append("|---|---:|---:|---:|")
        for q, imp, clk, pos in s.top_queries:
            lines.append(f"| {q} | {imp:,} | {clk:,} | {pos:.1f} |")
        lines.append("")
    if s.new_queries:
        lines.append(f"新しく出てきた検索語: {'、'.join(s.new_queries)}")
        lines.append("")

    if s.low_ctr:
        lines.append(
            f"### 表示は多いが CTR が低いページ"
            f"（表示 {MIN_IMPRESSIONS} 以上・CTR {_pct(LOW_CTR)} 未満）"
        )
        lines.append("")
        lines.append("| ページ | 表示 | クリック | CTR | 平均順位 |")
        lines.append("|---|---:|---:|---:|---:|")
        for p in s.low_ctr:
            lines.append(
                f"| {p.page} | {p.impressions:,} | {p.clicks:,} "
                f"| {_pct(p.ctr)} | {p.position:.1f} |"
            )
        lines.append("")

    if s.dropped:
        lines.append(f"### 順位が落ちたページ（{DROP_POSITION:.0f} 位以上）")
        lines.append("")
        lines.append("| ページ | 前の期間 | この期間 |")
        lines.append("|---|---:|---:|")
        for page, before, now in s.dropped:
            lines.append(f"| {page} | {before:.1f} | {now:.1f} |")
        lines.append("")

    if s.new_pages:
        lines.append("### 新しく表示され始めたページ")
        lines.append("")
        for p in s.new_pages:
            lines.append(f"- {p.page}（表示 {p.impressions:,}／平均順位 {p.position:.1f}）")
        lines.append("")

    if s.silent_pages:
        lines.append(f"### まだ表示されていないページ: {len(s.silent_pages):,}")
        lines.append("")
        for url in s.silent_pages[:TOP_N]:
            lines.append(f"- {url}")
        if len(s.silent_pages) > TOP_N:
            lines.append(f"- …他 {len(s.silent_pages) - TOP_N:,} ページ")
        lines.append("")

    return "\n".join(lines)
