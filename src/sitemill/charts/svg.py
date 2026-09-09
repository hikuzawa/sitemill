"""依存なしの SVG 図解。横棒・縦棒（ヒストグラム）・手続きフロー図（ADR 0008）。

設計の要点（dataviz の指針に従う）:
- 1 系列は 1 色（CSS 変数 --chart-series-1）。文字は文字色トークンを使い、データ色を着せない
- 棒は太さ 20px 以下、データ側の端だけ 4px 丸め、基線側は角。隣接する棒の間は地の色の隙間
- 目盛線は 1px の実線で控えめ。直接ラベルは棒が 8 本以下のときだけ
- <title>/<desc> と role="img" を付け、各棒にも <title>（ホバー時の説明）を付ける
- すべて data-generated="sitemill.charts" を持ち、自動生成の図解だと分かるようにする
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from html import escape

BAR_THICKNESS = 20
BAND = 30
FONT = "font-family:system-ui,-apple-system,'Segoe UI','Hiragino Sans','Noto Sans JP',sans-serif"
SERIES = "var(--chart-series-1, #2a78d6)"
TEXT = "var(--chart-text, #52514e)"
TEXT_STRONG = "var(--chart-text-strong, #0b0b0b)"
GRID = "var(--chart-grid, #e5e5e2)"
SURFACE = "var(--chart-surface, #ffffff)"
DIRECT_LABEL_MAX_BARS = 8


@dataclass(frozen=True)
class Bar:
    label: str
    value: float
    tooltip: str | None = None


@dataclass(frozen=True)
class Chart:
    title: str
    svg: str
    table_html: str
    kind: str

    def html(self, *, note: str = "自動生成の図解（データは各自治体ページから機械抽出）") -> str:
        return (
            f'<figure class="sm-chart sm-chart-{self.kind}" data-generated="sitemill.charts">'
            f"{self.svg}"
            f'<figcaption class="sm-chart-note">{escape(note)}</figcaption>'
            f"{self.table_html}</figure>"
        )


def _chart_id(title: str, chart_id: str | None) -> str:
    return chart_id or "c" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def nice_ticks(max_value: float, target: int = 4) -> list[float]:
    """0 から始まる読みやすい目盛。1/2/5 × 10^n の刻み。"""
    if max_value <= 0:
        return [0.0, 1.0]
    raw = max_value / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    ticks: list[float] = []
    t = 0.0
    while t < max_value + step * 0.999:
        ticks.append(t)
        t += step
    return ticks


def table_html(title: str, bars: list[Bar], unit: str) -> str:
    rows = "".join(
        f'<tr><th scope="row">{escape(b.label)}</th><td>{_fmt(b.value)}{escape(unit)}</td></tr>'
        for b in bars
    )
    return (
        '<details class="sm-chart-table"><summary>表で見る</summary>'
        f"<table><caption>{escape(title)}</caption><tbody>{rows}</tbody></table></details>"
    )


def _bar_path(x: float, y: float, w: float, h: float, *, horizontal: bool) -> str:
    r = 4.0
    if horizontal:
        if w <= r:
            return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0.5):.1f}" height="{h:.1f}"/>'
        return (
            f'<path d="M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z"/>'
        )
    # 縦棒: y は棒の上端、基線は y + h
    if h <= r:
        return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{max(h, 0.5):.1f}"/>'
    return (
        f'<path d="M{x:.1f},{y + h:.1f} v-{h - r:.1f} a{r},{r} 0 0 1 {r},-{r} h{w - 2 * r:.1f} '
        f'a{r},{r} 0 0 1 {r},{r} v{h - r:.1f} z"/>'
    )


def _svg_open(cid: str, title: str, desc: str, width: int, height: int, kind: str) -> str:
    return (
        f'<svg class="sm-svg" viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-labelledby="{cid}-t {cid}-d" data-generated="sitemill.charts" data-chart="{kind}" '
        f'style="max-width:{width}px;{FONT}">'
        f'<title id="{cid}-t">{escape(title)}</title><desc id="{cid}-d">{escape(desc)}</desc>'
    )


def hbar_chart(
    title: str,
    bars: list[Bar],
    *,
    desc: str = "",
    unit: str = "",
    width: int = 640,
    chart_id: str | None = None,
) -> Chart:
    """横棒グラフ。ラベルが長いカテゴリ（市町村名など）向け。"""
    cid = _chart_id(title, chart_id)
    label_w = min(220, max((len(b.label) for b in bars), default=4) * 13 + 12)
    left, right, top, bottom = label_w + 8, 24, 12, 24
    plot_w = max(width - left - right, 80)
    height = top + BAND * max(len(bars), 1) + bottom
    vmax = max((b.value for b in bars), default=0)
    ticks = nice_ticks(vmax)
    scale = plot_w / ticks[-1] if ticks[-1] else 0
    out = [_svg_open(cid, title, desc or title, width, height, "hbar")]
    out.append(f'<g stroke="{GRID}" stroke-width="1">')
    for t in ticks:
        x = left + t * scale
        out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height - bottom}"/>')
    out.append("</g>")
    out.append(f'<g fill="{TEXT}" font-size="11" text-anchor="middle">')
    for t in ticks:
        out.append(f'<text x="{left + t * scale:.1f}" y="{height - 6}">{_fmt(t)}</text>')
    out.append("</g>")
    direct = len(bars) <= DIRECT_LABEL_MAX_BARS
    for i, b in enumerate(bars):
        y = top + i * BAND + (BAND - BAR_THICKNESS) / 2
        w = b.value * scale
        tip = escape(b.tooltip or f"{b.label}: {_fmt(b.value)}{unit}")
        out.append(
            f'<text x="{left - 8}" y="{y + 14}" font-size="12" fill="{TEXT_STRONG}" '
            f'text-anchor="end">{escape(b.label)}</text>'
        )
        bar = _bar_path(left, y, w, BAR_THICKNESS, horizontal=True)
        out.append(f'<g fill="{SERIES}"><title>{tip}</title>{bar}</g>')
        if direct:
            out.append(
                f'<text x="{left + w + 6:.1f}" y="{y + 14}" font-size="11" fill="{TEXT}">'
                f"{_fmt(b.value)}{escape(unit)}</text>"
            )
    out.append("</svg>")
    return Chart(
        title=title, svg="".join(out), table_html=table_html(title, bars, unit), kind="hbar"
    )


def column_chart(
    title: str,
    bars: list[Bar],
    *,
    desc: str = "",
    unit: str = "",
    width: int = 640,
    height: int = 240,
    chart_id: str | None = None,
) -> Chart:
    """縦棒グラフ。順序のある区分（価格帯・築年代）のヒストグラム向け。"""
    cid = _chart_id(title, chart_id)
    left, right, top, bottom = 44, 12, 12, 40
    plot_w, plot_h = width - left - right, height - top - bottom
    n = max(len(bars), 1)
    band = plot_w / n
    bar_w = min(BAR_THICKNESS + 12, band - 4)
    vmax = max((b.value for b in bars), default=0)
    ticks = nice_ticks(vmax)
    scale = plot_h / ticks[-1] if ticks[-1] else 0
    out = [_svg_open(cid, title, desc or title, width, height, "column")]
    out.append(f'<g stroke="{GRID}" stroke-width="1">')
    for t in ticks:
        y = top + plot_h - t * scale
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>')
    out.append("</g>")
    out.append(f'<g fill="{TEXT}" font-size="11" text-anchor="end">')
    for t in ticks:
        out.append(f'<text x="{left - 6}" y="{top + plot_h - t * scale + 4:.1f}">{_fmt(t)}</text>')
    out.append("</g>")
    direct = len(bars) <= DIRECT_LABEL_MAX_BARS
    for i, b in enumerate(bars):
        cx = left + band * i + band / 2
        h = b.value * scale
        y = top + plot_h - h
        tip = escape(b.tooltip or f"{b.label}: {_fmt(b.value)}{unit}")
        bar = _bar_path(cx - bar_w / 2, y, bar_w, h, horizontal=False)
        out.append(f'<g fill="{SERIES}"><title>{tip}</title>{bar}</g>')
        if direct and b.value > 0:
            out.append(
                f'<text x="{cx:.1f}" y="{y - 4:.1f}" font-size="11" fill="{TEXT}" '
                f'text-anchor="middle">{_fmt(b.value)}</text>'
            )
        out.append(
            f'<text x="{cx:.1f}" y="{height - bottom + 16}" font-size="11" fill="{TEXT_STRONG}" '
            f'text-anchor="middle">{escape(b.label)}</text>'
        )
    out.append("</svg>")
    return Chart(
        title=title, svg="".join(out), table_html=table_html(title, bars, unit), kind="column"
    )


def bin_values(
    values: list[float], edges: list[float], labels: list[str], *, tooltips: list[str] | None = None
) -> list[Bar]:
    """edges で区切った度数。labels は len(edges)+1 個（最後は上限超え）。"""
    if len(labels) != len(edges) + 1:
        raise ValueError("labels は edges より 1 つ多く必要")
    counts = [0] * len(labels)
    for v in values:
        idx = next((i for i, e in enumerate(edges) if v < e), len(edges))
        counts[idx] += 1
    tips = tooltips or [None] * len(labels)  # type: ignore[list-item]
    return [
        Bar(label=lab, value=c, tooltip=t) for lab, c, t in zip(labels, counts, tips, strict=True)
    ]


def _wrap(text: str, per_line: int, max_lines: int = 3) -> list[str]:
    lines = [text[i : i + per_line] for i in range(0, len(text), per_line)]
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [lines[max_lines - 1][: per_line - 1] + "…"]
    return lines or [""]


def flow_diagram(
    title: str,
    steps: list[str],
    *,
    desc: str = "",
    width: int = 640,
    chart_id: str | None = None,
    per_row: int = 4,
) -> Chart:
    """手続きの流れ。箱と矢印を並べ、幅に収まらなければ折り返す。"""
    cid = _chart_id(title, chart_id)
    box_w, box_h, gap, row_gap, pad = 130, 64, 30, 28, 12
    per_row = max(1, min(per_row, (width - 2 * pad + gap) // (box_w + gap)))
    rows = math.ceil(len(steps) / per_row) if steps else 1
    height = pad * 2 + rows * box_h + (rows - 1) * row_gap
    out = [_svg_open(cid, title, desc or "、".join(steps), width, height, "flow")]
    out.append(
        f'<defs><marker id="{cid}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{SERIES}"/></marker></defs>'
    )
    arrow = f'stroke="{SERIES}" stroke-width="1.5" marker-end="url(#{cid}-arrow)"'
    for i, step in enumerate(steps):
        r, c = divmod(i, per_row)
        x = pad + c * (box_w + gap)
        y = pad + r * (box_h + row_gap)
        out.append(
            f"<g><title>{escape(f'{i + 1}. {step}')}</title>"
            f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="6" fill="{SURFACE}" '
            f'stroke="{SERIES}" stroke-width="1.5"/>'
            f'<text x="{x + 10}" y="{y + 16}" font-size="10" fill="{TEXT}">{i + 1}</text>'
        )
        lines = _wrap(step, 9)
        y0 = y + box_h / 2 - (len(lines) - 1) * 7
        for j, line in enumerate(lines):
            out.append(
                f'<text x="{x + box_w / 2}" y="{y0 + j * 14 + 4:.1f}" font-size="12" '
                f'fill="{TEXT_STRONG}" text-anchor="middle">{escape(line)}</text>'
            )
        out.append("</g>")
        if i + 1 < len(steps):
            if c + 1 < per_row:
                mid = y + box_h / 2
                out.append(
                    f'<line x1="{x + box_w + 2}" y1="{mid}" x2="{x + box_w + gap - 3}" '
                    f'y2="{mid}" {arrow}/>'
                )
            else:
                nx, ny = pad + box_w / 2, y + box_h + row_gap
                out.append(
                    f'<path d="M{x + box_w / 2},{y + box_h} v{row_gap / 2} H{nx} V{ny - 3}" '
                    f'fill="none" {arrow}/>'
                )
    out.append("</svg>")
    rows_html = "".join(
        f'<tr><th scope="row">{i + 1}</th><td>{escape(s)}</td></tr>' for i, s in enumerate(steps)
    )
    table = (
        '<details class="sm-chart-table"><summary>手順を文章で見る</summary>'
        f"<table><caption>{escape(title)}</caption><tbody>{rows_html}</tbody></table></details>"
    )
    return Chart(title=title, svg="".join(out), table_html=table, kind="flow")


# dataviz の参照パレット。ライトは light 面、ダークは dark 面向けに段を選んだ同じ青
_LIGHT_TOKENS = {
    "--chart-series-1": "#2a78d6",
    "--chart-text": "#52514e",
    "--chart-text-strong": "#0b0b0b",
    "--chart-grid": "#e5e5e2",
    "--chart-surface": "#ffffff",
}
_DARK_TOKENS = {
    "--chart-series-1": "#3987e5",
    "--chart-text": "#c3c2b7",
    "--chart-text-strong": "#ffffff",
    "--chart-grid": "#2b2b29",
    "--chart-surface": "#1a1a19",
}


def _decl(tokens: dict[str, str]) -> str:
    return ";".join(f"{k}:{v}" for k, v in tokens.items())


def chart_css() -> str:
    """サービス側のスタイルシートに含める図解用の CSS（ライト・ダーク両対応）。"""
    light, dark = _decl(_LIGHT_TOKENS), _decl(_DARK_TOKENS)
    rules = [
        f".sm-chart{{margin:1.5rem 0;{light}}}",
        ".sm-chart .sm-svg{display:block;height:auto}",
        ".sm-chart-note{font-size:.8rem;color:var(--chart-text);margin-top:.25rem}",
        ".sm-chart-table{font-size:.85rem;margin-top:.25rem}",
        ".sm-chart-table table{border-collapse:collapse;margin-top:.5rem}",
        ".sm-chart-table th,.sm-chart-table td{border:1px solid var(--chart-grid);"
        "padding:.25rem .6rem;text-align:left}",
        ".sm-chart-table td{font-variant-numeric:tabular-nums;text-align:right}",
        '@media (prefers-color-scheme: dark){:root:not([data-theme="light"]) '
        f".sm-chart{{{dark}}}}}",
        f':root[data-theme="dark"] .sm-chart{{{dark}}}',
    ]
    return "\n".join(rules)
