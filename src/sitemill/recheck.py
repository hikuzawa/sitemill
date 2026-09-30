"""運営主体の根拠を定期的に確かめ直す回し方（ADR 0027、2026-09-28）。

akiya-atlas が先に自治体の根拠で実装した回し方（akiya-atlas ADR 0018、設計案 K）を、サービスに
依らない部分だけエンジンへ移したもの。確かめる中身と選び直しの中身はサービスが持つ。

- 毎晩、確認日の古い順に `per_night` 件（全件 ÷ 周期の日数）。前の晩に成り立たなかったものは、
  枠の外で翌晩も見る
- 結果は 4 通り:
  - ok: 成り立った。確認日を今日（JST）にする
  - fail: 成り立たなかった。日付は進めず、続いた晩数・始まった日・理由を残す。`retry_nights` 晩
    続いたら、サービスに `recheck_reselect` があれば呼ぶ（無ければ週次に出し続けるだけ）
  - skip: robots.txt で取れない。確かめていないので日付も失敗の数も動かさない
  - unreachable: 通信できない（時間切れ・接続できない・403・429）。skip と同じ扱い。海外の実行環境
    （GitHub Actions）からの接続を落とす国内サイトがあり、失敗に数えると選び直しが空回りする
    （japan-open-today の takamatsu.or.jp・teshima-navi.jp）
  skip と unreachable は `wait_days` 日後に見直す。待たせないと、古い日付のまま毎晩の枠を取り続ける
- 記録は `data/state/recheck.json`（件ごとの状態）と
  `data/runs/operator-rechecks.jsonl`（1 件 1 行）
- 今夜の 1 件ずつの結果（`RecheckResult`）は `RecheckReport.results` に入り、`sitemill recheck` は
  それをサービスの `recheck_done(ws, results)` に渡す。確認日を画面に出すサービスは、ここで
  自分の記録へ書き戻す（この回し方はサービスの確認日を書き換えない。ADR 0027 の 09-30 の追記）

**落とし穴**（akiya-atlas で実際に起きたもの）:
- まとめて作り直すたびに全件の確認日を今日にしない。確かめていないものまで「確かめた」になる
  （1,700 件以上）。確認日は、確かめた行だけ進める
- 日付は JST で書く。UTC だと、日本時間の朝に走る日次は 1 日早い日付になる
- 中身が同じなら記録の「作った時刻」を進めない。毎晩、差分だけが出る
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sitemill.clock import jst_now, jst_today
from sitemill.fetch.client import FetchResult
from sitemill.settings import Workspace

STATE_FILE = "recheck.json"
LOG_FILE = "operator-rechecks.jsonl"
# ページの有無について何も言っていない応答
REFUSED = (403, 429)
RESULTS = ("ok", "fail", "skip", "unreachable")


@dataclass(frozen=True)
class RecheckTarget:
    """確かめ直す 1 件。`checked_on` はサービスが持つ確認日（無ければ None）。"""

    key: str
    label: str
    checked_on: date | None = None


@dataclass(frozen=True)
class RecheckResult:
    """今夜確かめた 1 件の結果。`recheck_done` フックに渡す。

    - result: 確かめた結果（ok / fail / skip / unreachable）
    - checked_on: 今夜で確認日になった日（成り立った・選び直した）。それ以外は None
    - failures: 今夜のあとの、続けて成り立たなかった晩数（成り立った・選び直したら 0。
      見送り・通信できないときは、それまでの晩数のまま）
    - reselected: 選び直したときの記録（サービスの `recheck_reselect` が返したもの）
    """

    key: str
    label: str
    result: str
    reason: str = ""
    failures: int = 0
    checked_on: date | None = None
    reselected: dict[str, Any] | None = None


@dataclass
class RecheckReport:
    checked: int = 0
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RESULTS, 0))
    reselected: int = 0
    lines: list[str] = field(default_factory=list)
    results: list[RecheckResult] = field(default_factory=list)  # 確かめた順


def unreachable(res: FetchResult) -> bool:
    """届かなかった（時間切れ・接続できない・拒否）。ページがあるかどうかは分からない。"""
    return (res.status == 0 and bool(res.error) and not res.blocked) or res.status in REFUSED


def load_state(ws: Workspace) -> dict[str, dict[str, Any]]:
    try:
        return dict(json.loads((ws.state_dir / STATE_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _save_state(ws: Workspace, state: dict[str, dict[str, Any]]) -> None:
    path = ws.state_dir / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(dict(sorted(state.items())), ensure_ascii=False, indent=1, sort_keys=True)
    path.write_text(body + "\n", encoding="utf-8", newline="\n")


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def checked_on(target: RecheckTarget, state: dict[str, Any]) -> date | None:
    """確認日。サービスの値と、この回し方が確かめた日のうち新しいほう。"""
    days = [d for d in (target.checked_on, _day(state.get("checked_on"))) if d is not None]
    return max(days) if days else None


def pick(
    targets: list[RecheckTarget],
    state: dict[str, dict[str, Any]],
    *,
    per_night: int,
    wait_days: int,
    today: date,
) -> list[RecheckTarget]:
    """今夜見る分。前の晩に成り立たなかったもの（枠の外）と、確認日の古い順に per_night 件。"""
    retry: list[RecheckTarget] = []
    queue: list[tuple[str, str, RecheckTarget]] = []
    for target in targets:
        st = state.get(target.key) or {}
        last = _day(st.get("last_tried"))
        if st.get("waiting_since") and last is not None and (today - last).days < wait_days:
            continue
        if st.get("failures"):
            retry.append(target)
            continue
        day = checked_on(target, st)
        queue.append((day.isoformat() if day else "", target.key, target))
    queue.sort(key=lambda row: (row[0], row[1]))
    return retry + [t for _, _, t in queue[:per_night]]


def rotate(
    ws: Workspace,
    targets: list[RecheckTarget],
    check: Callable[[RecheckTarget], tuple[str, str]],
    *,
    per_night: int,
    retry_nights: int = 3,
    wait_days: int = 7,
    workers: int = 1,
    today: date | None = None,
    reselect: Callable[[RecheckTarget, str], dict[str, Any] | None] | None = None,
    describe_reselect: Callable[[RecheckTarget, dict[str, Any]], str] | None = None,
) -> RecheckReport:
    """今夜の分を確かめ直し、状態と記録を書く。

    `check` は (ok / fail / skip / unreachable, 理由) を返す。1 件ずつの結果は `report.results`
    （確かめた順）。`describe_reselect` を渡すと、選び直しの行（`report.lines`）をサービスが書く
    （無ければ記録をそのまま出す）。
    """
    today = today or jst_today()
    state = load_state(ws)
    chosen = pick(targets, state, per_night=per_night, wait_days=wait_days, today=today)

    def run(target: RecheckTarget) -> tuple[RecheckTarget, str, str]:
        try:
            result, reason = check(target)
        except Exception as e:  # noqa: BLE001 - 1 件の失敗で夜の分を止めない。記録に残す
            result, reason = "fail", f"確かめる途中で失敗した（{type(e).__name__}: {e}）"
        if result not in RESULTS:
            result, reason = "fail", f"サービスが知らない結果を返した: {result}"
        return target, result, reason

    if workers > 1 and len(chosen) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(run, chosen))
    else:
        outcomes = [run(t) for t in chosen]

    report = RecheckReport()
    today_s = today.isoformat()
    log: list[dict[str, Any]] = []
    for target, result, reason in outcomes:
        report.checked += 1
        report.counts[result] += 1
        st = dict(state.get(target.key) or {})
        entry: dict[str, Any] = {
            "at": jst_now().isoformat(timespec="seconds"),
            "result": result,
            "key": target.key,
            "label": target.label,
        }
        if reason:
            entry["reason"] = reason
        failures, checked, reselected = 0, None, None
        if result in ("skip", "unreachable"):
            st.update(waiting_since=st.get("waiting_since") or today_s, waiting_reason=reason)
            st["last_tried"] = today_s
            failures = int(st.get("failures") or 0)
        elif result == "ok":
            st = {"checked_on": today_s}
            checked = today
        else:
            nights = int(st.get("failures") or 0) + 1
            st = {k: v for k, v in st.items() if k not in ("waiting_since", "waiting_reason")} | {
                "failures": nights,
                "failed_since": st.get("failed_since") or today_s,
                "reason": reason,
                "last_tried": today_s,
            }
            failures = nights
            entry["failures"] = nights
            report.lines.append(f"{target.label}: 成り立たない（{nights} 晩目）: {reason}")
            if nights >= retry_nights and reselect is not None:
                record = reselect(target, reason)
                if record:
                    report.reselected += 1
                    log.append(entry)
                    entry = {"at": entry["at"], "result": "reselect", "key": target.key} | record
                    st = {"checked_on": today_s}
                    failures, checked, reselected = 0, today, record
                    report.lines.append(
                        describe_reselect(target, record)
                        if describe_reselect is not None
                        else f"{target.label}: 選び直した（{record}）"
                    )
        state[target.key] = st
        log.append(entry)
        report.results.append(
            RecheckResult(
                key=target.key,
                label=target.label,
                result=result,
                reason=reason,
                failures=failures,
                checked_on=checked,
                reselected=reselected,
            )
        )
    _save_state(ws, state)
    if log:
        ws.runs_dir.mkdir(parents=True, exist_ok=True)
        with (ws.runs_dir / LOG_FILE).open("a", encoding="utf-8", newline="\n") as f:
            for entry in log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    c = report.counts
    report.lines.insert(
        0,
        f"運営主体の確かめ直し: {report.checked} 件（成り立った {c['ok']}・"
        f"成り立たなかった {c['fail']}・robots.txt で見送り {c['skip']}・"
        f"通信できない {c['unreachable']}・選び直し {report.reselected}）",
    )
    return report


def recheck_lines(
    ws: Workspace,
    targets: list[RecheckTarget],
    *,
    days: int = 7,
    now: datetime | None = None,
) -> list[str]:
    """週次に出す行。件数・成り立たないもの・確かめられていないもの・いちばん古い確認日・選び直し。"""
    now = now or jst_now()
    since = (now - timedelta(days=days)).isoformat()
    counts = dict.fromkeys((*RESULTS, "reselect"), 0)
    reselects: list[dict[str, Any]] = []
    try:
        lines = (ws.runs_dir / LOG_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if str(entry.get("at", "")) < since:
            continue
        result = str(entry.get("result"))
        counts[result] = counts.get(result, 0) + 1
        if result == "reselect":
            reselects.append(entry)
    state = load_state(ws)
    labels = {t.key: t.label for t in targets}
    failing = sorted((k, st) for k, st in state.items() if st.get("failures") and k in labels)
    waiting = sorted((k, st) for k, st in state.items() if st.get("waiting_since") and k in labels)
    dated = [checked_on(t, state.get(t.key) or {}) for t in targets]
    known = [d for d in dated if d is not None]
    out = [
        f"- 運営主体の確かめ直し（直近 {days} 日）: 成り立った {counts['ok']}・"
        f"成り立たなかった {counts['fail']}・robots.txt で見送り {counts['skip']}・"
        f"通信できない {counts['unreachable']}・選び直し {counts['reselect']}"
        + (f"。いちばん古い確認日 {min(known).isoformat()}" if known else "")
        + (f"（確認日の無いもの {len(dated) - len(known)} 件）" if len(known) < len(dated) else "")
    ]
    if failing:
        out.append(f"  - **成り立たないもの: {len(failing)} 件**")
        for key, st in failing:
            out.append(
                f"    - {labels[key]}（{st.get('failed_since')} から {st.get('failures')} 晩）: "
                f"{st.get('reason')}"
            )
    if waiting:
        out.append(f"  - 確かめられていないもの: {len(waiting)} 件")
        for key, st in waiting:
            out.append(
                f"    - {labels[key]}（{st.get('waiting_since')} から）: {st.get('waiting_reason')}"
            )
    for entry in reselects:
        detail = {k: v for k, v in entry.items() if k not in ("at", "result", "key")}
        out.append(f"  - 選び直した: {labels.get(entry.get('key'), entry.get('key'))} {detail}")
    return out
