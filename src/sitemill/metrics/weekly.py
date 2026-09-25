"""日次パイプラインの 1 週間をまとめる（実行時間・費用・自己修復・抽出の充足率）。

`data/runs/` に毎回残る実行レポートだけを読む。相手サイトには一切アクセスしない。
「タグを打ってよいか」「費用と時間が想定内か」「抽出が劣化していないか」を、人が毎日ログを
見なくても判断できる形にする（ADR 0010 の抽出メトリクスと ADR 0014 の着手条件と同じ入力）。

akiya-atlas が先に持っていた同名の仕組みを、サービスに依らない部分だけエンジンへ移したもの。
サービス固有の項目（取り下げ依頼の件数など）は、サービス側で行を足す。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sitemill.settings import Workspace

# モデルごとの単価（$/100 万トークン）。ここに無いモデルは費用を 0 として扱い、注記を出す
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-5": (15.0, 75.0),
}

# robots.txt のせいで巡回できない日がこれだけ続いたら、ホスト名を出す
# （その情報源の更新が止まっている）
ROBOTS_STALE_DAYS = 3
# 巡回を見送った理由のうち robots.txt によるもの。エンジン（fetch.robots / fetch.client）は
# 「取得できない」「robots.txt 202: 今回は巡回しない」のような異常な応答、
# 「robots.txt により拒否」の 3 通りを書く。どれも続けばその情報源の更新は止まるので、まとめて拾う
ROBOTS_MARK = "robots.txt"
ROBOTS_STALLED_NOTE = "この情報源の掲載は更新が止まっている"


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class DayRow:
    """1 日ぶんの実行の合計。"""

    date: str
    seconds: float = 0.0
    fetched: int = 0
    changed: int = 0
    pages: int = 0
    items: int = 0
    created: int = 0
    updated: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    heal_checked: int = 0
    heal_changed: int = 0
    heal_downgraded: int = 0
    heal_recrawl: int = 0
    build_pages: int = 0
    # 項目ごとの (読めた, 出てきた) の合計。充足率の変化を見る
    fields: dict[str, tuple[int, int]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def cost(self, price: tuple[float, float]) -> float:
        return self.input_tokens / 1e6 * price[0] + self.output_tokens / 1e6 * price[1]

    @property
    def parsed_rate(self) -> float:
        parsed = sum(p for p, _ in self.fields.values())
        total = sum(t for _, t in self.fields.values())
        return parsed / total if total else 0.0


def collect(
    ws: Workspace, *, days: int = 7, now: datetime | None = None, source: str = "ci"
) -> list[DayRow]:
    """直近 N 日の実行レポートを日ごとにまとめる（ファイルだけを読む）。

    `source` は "ci"（日次パイプラインだけ）/ "local"（手元の作業だけ）/ "all"。
    手元の作業を混ぜると日次の所要時間と費用を読み違えるので、既定は "ci"。
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    rows: dict[str, DayRow] = {}
    for path in sorted(ws.runs_dir.glob("*.json")):
        if path.name.startswith(("latest-", "weekly-", "backfill", "discover-")):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        started = _dt(data.get("started_at"))
        if started is None or started < since:
            continue
        is_ci = bool(data.get("ci"))
        if (source == "ci" and not is_ci) or (source == "local" and is_ci):
            continue
        key = started.date().isoformat()
        row = rows.setdefault(key, DayRow(date=key))
        _absorb(row, data, started)
    return [rows[k] for k in sorted(rows)]


def _absorb(row: DayRow, data: dict[str, Any], started: datetime) -> None:
    finished = _dt(data.get("finished_at"))
    if finished is not None:
        row.seconds += (finished - started).total_seconds()
    stages: dict[str, Any] = data.get("stages") or {}
    crawl = stages.get("crawl") or {}
    row.fetched += crawl.get("fetched", 0)
    row.changed += crawl.get("changed", 0)
    extract = stages.get("extract") or {}
    row.pages += extract.get("pages", 0)
    row.items += extract.get("items", 0)
    ingest = stages.get("ingest") or {}
    row.created += ingest.get("created", 0)
    row.updated += ingest.get("updated", 0)
    heal = stages.get("heal") or {}
    row.heal_checked += heal.get("checked", 0)
    changed = heal.get("changed", 0)
    row.heal_changed += len(changed) if isinstance(changed, list) else changed
    row.heal_downgraded += heal.get("downgraded", 0)
    row.heal_recrawl += heal.get("swapped", heal.get("recrawl", 0))
    build = stages.get("build") or {}
    row.build_pages = max(row.build_pages, build.get("pages", 0))
    llm = data.get("llm") or {}
    row.llm_calls += llm.get("calls", 0)
    row.input_tokens += llm.get("input_tokens", 0)
    row.output_tokens += llm.get("output_tokens", 0)
    metrics = data.get("extraction_metrics") or {}
    for name, counts in (metrics.get("fields") or {}).items():
        parsed, total = row.fields.get(name, (0, 0))
        row.fields[name] = (parsed + counts.get("parsed", 0), total + counts.get("total", 0))
    for err in data.get("errors") or []:
        row.errors.append(f"{data.get('command', '?')}: {str(err)[:120]}")


def price_of(model: str) -> tuple[float, float] | None:
    return PRICES.get(model)


def month_estimate(rows: list[DayRow], price: tuple[float, float]) -> dict[str, float]:
    """1 か月に直した見込み（実行時間と費用）。"""
    if not rows:
        return {"minutes": 0.0, "cost": 0.0}
    days = len(rows)
    return {
        "minutes": sum(r.seconds for r in rows) / 60 / days * 30,
        "cost": sum(r.cost(price) for r in rows) / days * 30,
    }


def field_shift(rows: list[DayRow]) -> list[tuple[str, float, float]]:
    """項目ごとの充足率が、期間の最初と最後でどう動いたか。

    抽出の劣化に気づくための数字。`(項目, 最初, 最後)` を、変化の大きい順に返す。
    """
    withs = [r for r in rows if r.fields]
    if len(withs) < 2:
        return []
    first, last = withs[0], withs[-1]
    out: list[tuple[str, float, float]] = []
    for name in sorted(set(first.fields) | set(last.fields)):
        fp, ft = first.fields.get(name, (0, 0))
        lp, lt = last.fields.get(name, (0, 0))
        if not ft or not lt:
            continue
        out.append((name, fp / ft, lp / lt))
    return sorted(out, key=lambda r: -abs(r[2] - r[1]))


def report(
    ws: Workspace,
    *,
    days: int = 7,
    now: datetime | None = None,
    source: str = "ci",
    model: str = "",
) -> list[str]:
    """報告用の行（Markdown）。数字が無い日があっても表は出す。"""
    rows = collect(ws, days=days, now=now, source=source)
    model = model or ws.site.llm.model
    price = price_of(model)
    kind = {"ci": "日次パイプライン", "local": "手元の実行", "all": "すべての実行"}[source]
    out = [f"## {kind}の直近 {days} 日", ""]
    if not rows:
        out += [
            f"**この期間に記録がありません。**（`data/runs/` に {kind}のレポートが無い）",
            "",
        ]
        return out

    out += [
        "| 日付 | 実行 | 取得 | 変化 | 抽出 | 取込 | 新規/更新 | LLM | 費用 | heal | ページ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    total = DayRow(date="合計")
    for r in rows:
        cost = f"${r.cost(price):.2f}" if price else "—"
        out.append(
            f"| {r.date} | {r.seconds / 60:.1f}分 | {r.fetched} | {r.changed} | {r.pages} | "
            f"{r.items} | {r.created}/{r.updated} | {r.llm_calls} | {cost} | "
            f"{r.heal_checked} | {r.build_pages or '—'} |"
        )
        for name in (
            "seconds fetched changed pages items created updated llm_calls "
            "input_tokens output_tokens heal_checked heal_changed heal_downgraded heal_recrawl"
        ).split():
            setattr(total, name, getattr(total, name) + getattr(r, name))
        total.errors.extend(r.errors)
    total_cost = f"${total.cost(price):.2f}" if price else "—"
    out.append(
        f"| **合計** | **{total.seconds / 60:.0f}分** | {total.fetched} | {total.changed} | "
        f"{total.pages} | {total.items} | {total.created}/{total.updated} | {total.llm_calls} | "
        f"**{total_cost}** | {total.heal_checked} | — |"
    )
    estimate = month_estimate(rows, price or (0.0, 0.0))
    # Actions の無料枠（月 2,000 分）とは比べない。public リポジトリの実行は枠を消費しないので、
    # 「無料枠の n%」は意味を持たない（2026-09-21。両サービスとも public）
    out += [
        "",
        f"- 1 日あたり: {total.seconds / len(rows) / 60:.1f} 分"
        + (f" / ${total.cost(price) / len(rows):.2f}" if price else ""),
        f"- 1 か月の見込み: **{estimate['minutes']:.0f} 分**"
        + (f" / **${estimate['cost']:.2f}**" if price else ""),
        f"- 自己修復（heal）: 点検 {total.heal_checked} 件 / 変更 {total.heal_changed} 件 / "
        f"取り下げ {total.heal_downgraded} 件 / 再巡回 {total.heal_recrawl} 件",
        f"- 失敗した工程: {len(total.errors)} 件"
        + (f" — {total.errors[:3]}" if total.errors else ""),
    ]
    if not price:
        out.append(f"- 費用: モデル {model} の単価が未登録（`metrics/weekly.py` の PRICES）")
    out += robots_lines(*robots_failures(ws, days=days, now=now, source=source))

    shifts = field_shift(rows)
    if shifts:
        out += ["", "### 抽出の充足率（期間の最初 → 最後）", ""]
        out += ["| 項目 | 最初 | 最後 | 差 |", "| --- | ---: | ---: | ---: |"]
        for name, before, after in shifts[:10]:
            out.append(
                f"| {name} | {before * 100:.0f}% | {after * 100:.0f}% | "
                f"{(after - before) * 100:+.0f}pt |"
            )
    elif any(r.fields for r in rows):
        out += ["", "抽出の充足率: 比べられる日が 1 日しかありません（変化は次回から出ます）"]
    return out


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def robots_reason(error: str) -> str:
    """見送った理由を、打ち手が分かる言葉にする。"""
    if "拒否" in error:
        return "robots.txt で拒否。巡回先の URL を見直す"
    m = re.search(r"robots\.txt (\d{3})", error)
    if m:
        return f"robots.txt が {m.group(1)} を返す。相手に当たり直す"
    return "robots.txt を取得できない。相手に当たり直す"


def robots_failures(
    ws: Workspace, *, days: int = 7, now: datetime | None = None, source: str = "all"
) -> tuple[list[str], int, list[tuple[str, int | None, str]]]:
    """robots.txt のせいで巡回できなかったホスト。(ホスト一覧, 延べ回数, 止まっているホスト)。

    robots.txt が読めない・拒否されていると、その回は巡回しない（正しい判断）。ただし**続くと
    その情報源だけ静かに更新が止まる**。japan-open-today では CI から 2 ホストの robots.txt が
    9/13 から毎晩時間切れになっていて、週次には出ていなかった（2026-09-26）。akiya-atlas が
    先に持っていた仕組みを、サービスに依らない形でエンジンへ移したもの。

    数と延べ回数は期間内の実行レポート（`*-crawl.json` の errors）から数える。止まっているか
    どうかは実行レポートでは決められない（巡回間隔の適応で、その晩は対象外だったのか見送ったのかが
    混ざる）ので、巡回の状態（`data/state/crawl.json`）にいま robots の理由が残っている URL を見て、
    **最後に取得できた日からの日数**で判断する。`ROBOTS_STALE_DAYS` 日以上なら名前を出す。
    一度も取得できていないホストは日数を None で返す。

    エンジンが書く見送りの理由（取得できない・異常な応答・拒否）はすべて拾う。「取得できない」
    だけを拾うと、robots.txt が 202 を返し続けるホストや、拒否されたページを巡回先にしている
    情報源を見逃す（akiya-atlas で 2026-09-26 に起きた）。
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    hosts: dict[str, int] = {}
    for path in sorted(ws.runs_dir.glob("*-crawl.json")):
        if path.name.startswith(("latest-", "backfill", "discover-")):
            continue  # 直近の写しと手元の作業。二重に数えない
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        started = _dt(data.get("started_at"))
        if started is None or started < since:
            continue
        is_ci = bool(data.get("ci"))
        if (source == "ci" and not is_ci) or (source == "local" and is_ci):
            continue
        for err in data.get("errors") or []:
            err = str(err)
            if ROBOTS_MARK in err:
                host = _host(err.split(": " + ROBOTS_MARK, 1)[0])
                hosts[host] = hosts.get(host, 0) + 1

    stalled: dict[str, tuple[int | None, str]] = {}
    try:
        state = json.loads((ws.state_dir / "crawl.json").read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        state = {}
    for url, st in (state.get("urls") or {}).items():
        error = str((st or {}).get("error") or "")
        if ROBOTS_MARK not in error:
            continue
        host = _host(url)
        fetched = _dt(st.get("fetched_at"))
        age = None if fetched is None else (now - fetched).days
        known = stalled.get(host)
        # 同じホストの中では、いちばん長く止まっている URL の日数を採る（None が最も長い）
        if known is None or (known[0] is not None and (age is None or age > known[0])):
            stalled[host] = (age, robots_reason(error))
    named = sorted(
        (
            (h, age, reason)
            for h, (age, reason) in stalled.items()
            if age is None or age >= ROBOTS_STALE_DAYS
        ),
        key=lambda row: (row[1] is not None, -(row[1] or 0), row[0]),
    )
    return sorted(hosts), sum(hosts.values()), named


def robots_lines(
    hosts: list[str],
    times: int,
    stalled: list[tuple[str, int | None, str]],
    *,
    note: str = ROBOTS_STALLED_NOTE,
) -> list[str]:
    """週次に出す行。1 晩だけの見送りは数だけ、続いているものは名前と理由つきで。

    `note` は止まっていることの意味をサービスの言葉で書く（akiya-atlas なら「この自治体の
    掲載は更新が止まっている」）。
    """
    if not hosts and not stalled:
        return ["- robots.txt で巡回できなかったホスト: なし"]
    out = [f"- robots.txt で巡回できなかったホスト: **{len(hosts)}**（延べ {times} 回）"]
    if stalled:
        out.append(f"  - **{ROBOTS_STALE_DAYS} 日以上取得できていない**（{note}）")
        for host, age, reason in stalled:
            since = "一度も取得できていない" if age is None else f"最終取得から {age} 日"
            out.append(f"    - {host}（{since}）: {reason}")
    return out


def write_snapshot(ws: Workspace, *, days: int = 7, source: str = "ci") -> Path:
    """まとめを `data/runs/weekly-<日付>.json` に残す（次回との比較用）。"""
    rows = collect(ws, days=days, source=source)
    price = price_of(ws.site.llm.model) or (0.0, 0.0)
    path = ws.runs_dir / f"weekly-{datetime.now(UTC).date().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "days": days,
                "source": source,
                "model": ws.site.llm.model,
                "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "per_day": [vars(r) | {"cost": round(r.cost(price), 4)} for r in rows],
                "month_estimate": month_estimate(rows, price),
                "field_shift": [
                    {"field": n, "first": round(a, 4), "last": round(b, 4)}
                    for n, a, b in field_shift(rows)
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path
