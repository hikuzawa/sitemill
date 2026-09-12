# ADR 0018: 「その日、開いているか」を規則と祝日から計算する

- ステータス: 採用（2026-09-12）

## 背景
japan-open-today は施設と交通について「今日・今週、行けるか」を出す。断定できないときに断定すると、
利用者は休館日に島まで渡ることになる。開館時間を持つだけでは足りず、**規則**（定休日・祝日の扱い・
臨時の告知）を構造化して祝日の暦と突き合わせる必要がある。この計算はサービスに依らないので engine に置く。

## 決定

### 日本時間を 1 か所に集める（`sitemill/clock.py`）
日次実行は GitHub Actions（UTC）で走る。`date.today()` を使うと JST の朝 6 時に前日を判定する。
`JST` / `jst_now` / `jst_today` / `to_jst` をここに集め、日付が絡む処理は必ず通す。
判定の関数は時刻帯を持たない `date` だけを受け取り、「今日」を決める責任は呼び出し側に置く。

### 祝日（`sitemill/jpcal/`）
- 一次データは内閣府の CSV（政府標準利用規約 第 2.0 版＝ライセンスのホワイトリスト適合）。
  サービスが `data/reference/` に取得日付きで保存し、engine は読むだけ
- 取得できない・範囲外の年は法律の規則から計算する（固定日・ハッピーマンデー・春分秋分の近似式・
  振替休日・国民の休日）。**外部ファイルの取得成功を判定の前提にしない**。取得できない日に祝日が
  消えると、休館日を開館と誤判定する
- 層は `primary`（CSV。その年は計算より優先）/ 計算 / `extra`（サービスが足す休日。年の優先には
  関わらず常に上乗せ）の 3 つ。`primary` と `extra` を分けたのは、数日足しただけでその年が
  「一次データのある年」に化けて計算分が消える事故を防ぐため
- 答えられない年は `covers()` が False を返す。判定側はそれを unknown の材料にする

### 規則のモデル（`sitemill/models/schedule.py`）
`TimeRange` / `DaySelector` / `DateSpan` / `AnnualSpan` / `HoursPeriod` / `ClosureRule` /
`SpecialNotice`。各要素が `Evidence`（原文の引用・一次情報 URL・取得日時）を持つ。
根拠の表示に使い、規則と告知が食い違ったときにどちらが新しいかを決めるのにも使う。

- `AnnualSpan` は年をまたげる（12/29〜1/3）
- `HolidayBehavior` は `closed` / `open` / `next_day` / `next_weekday` / `unspecified` の 5 値。
  **「翌日」と「翌平日」を分ける**のが要点（下の理由）
- `ClosureKind.irregular` を持つ。「不定休」は規則が無いのではなく、**計算できないと分かっている**状態

### パーサ（`sitemill/parse/jp/`）
`hours.py`（開館時間・最終入館・季節・曜日別）、`closures.py`（定休日・第 n 曜日・年末年始・運行日）、
`dates.py` に `parse_date_range`、`duration.py`（所要時間）。すべて quote-then-parse の約束どおり
`(値, 注記)` を返し、読めなければ `None` を返して引用だけを残す。
月ごとに時間が変わる表のような形は読まない。推測で埋めるより「不明」と言って一次情報へ送る。

### 判定（`sitemill/openstatus/`）
`resolve_day(day, hours, closures, notices, service_days, holidays, fetched_at, stale_after_days)`
→ `DayVerdict{state, periods, reasons}`。`state` は **open / closed / unknown の 3 値だけ**。

材料の優先順位と突き合わせ:
1. 運行日（交通のダイヤ）。当てはまらない日は `not_in_service` で closed
2. 臨時の告知。休業の告知は規則の上で開館日でも closed にする（食い違いではなく追加の事実）
3. 定休日の規則
4. 開館時間。休館日でないと分かっても、その日の時間が無ければ open と言わない（`no_data` で unknown）

**告知と規則が食い違うとき**（臨時開館の告知 × 定休日）は、`Evidence.fetched_at` が新しい方を採り、
根拠は両方残す。告知の方が古いか同時刻なら `conflicting` で unknown にする。

**祝日が連続するときの振替**:
- `next_day`（「祝日の場合は翌日」）で、その翌日もまた祝日のとき、原文はさらに動くのか当日休むのかを
  語っていない。**推測せず `substitute_ambiguous` で unknown**。2026 年 9 月（21 日 敬老の日・
  22 日 国民の休日・23 日 秋分の日）が実際にこの形になる
- `next_weekday`（「翌平日」）なら決まる。連休明けの最初の平日を振替の休館日にする
- `unspecified`（祝日に触れているが扱いが読めない）も unknown

**鮮度の下限**: `stale_after_days` を超えて一次情報を取得できていなければ、規則の上で開館日でも
`stale_source` を先頭に付けて unknown に落とす。規則の根拠は残すので「規則では開館日だが情報が古い」と
書ける。取得が途切れているのは相手サイトの変更・障害・移転のいずれかで、規則の前提自体が崩れている
可能性が高い。黙って古い規則で断定するより不明と言う方が利用者の損失が小さい。

### 巡回間隔の例外
`[crawl] always_daily_kinds`（既定は空）に挙げた種別の seed ページは、変化率で間隔が延びた source でも
毎日取りに行く（`crawl_source(only_kinds=...)`）。臨時休業と運休は「変化の少ないページに突然出る」ので、
間隔を延ばすと最も重要な情報を取り逃がす。営業時間や料金のページは年に数回しか変わらないため、
これまでどおり変化率に従う。

## 影響
- `[crawl] stale_after_days` は engine では読み込むだけで、使うのはサービス（`resolve_day` に渡す）
- 判定の文言（「9月12日（土）は 10:00–17:00 開館」など）はサービスがロケールごとに作る。
  engine が返すのは根拠コードと引用だけ（ADR 0016 の多言語と組み合わせる）
- 既存のサービス（akiya-atlas）はこれらを使わないので影響を受けない。`always_daily_kinds` の既定は空
