# ADR 0017: ページ種別はサービスが決める。巡回ゲートは二段にする

- ステータス: 採用（2026-09-12）

## 背景
`PageKind` は空き家バンク向けの 5 種（listing_index / listing_detail / subsidy / info / other）で固定されていた。
また `policy=crawl` は「自治体または自治体の移住推進組織」だけに許していた。これは akiya-atlas の規約であって
エンジンの規約ではない。japan-open-today は営業時間・料金・お知らせ・時刻表といった種別を扱い、
運営主体も施設自身の公式サイトや交通事業者まで広がる。エンジンの都合でサービスの規約を決めてしまわないようにする。

## 決定

### ページ種別
- `SeedPage.kind` / `FollowRule.kind` を `str` にする。`PageKind` は**よく使う値の定数**として残す
  （`listing_index` / `listing_detail` / `subsidy` / `info` / `notice` / `other`）
- 種別は `^[a-z][a-z0-9_]*$` に限る。種別は巡回状態のファイルと抽出仕様（`extraction_spec(kind)`）の
  引き当てキーなので、表記ゆれを黙って通すと「抽出仕様が見つからない」だけの静かな失敗になる
- 値は `str` の派生クラス `PageKindValue` で、**`kind.value` でも読める**。Enum だった頃の書き方
  （akiya-atlas の `pages.py` が実際に使っている）を壊さないための互換。新しく書くコードは文字列として扱う

### 巡回ゲートの二段化
1. **エンジン側の下限**（`Source` の検証）: `policy=crawl` には運営主体の根拠（`operator_evidence`）と
   `pages` が必要で、`operator_kind` は公式と根拠づけできる種別（`OFFICIAL_OPERATORS`）でなければならない。
   `third_party`（民間のまとめサイト・予約サイト）と `unknown` は**どのサービスでも巡回できない**
2. **サービスごとの線引き**（`Service.crawlable_operator_kinds`）: サービスが宣言した種別だけを巡回する。
   **宣言しなければ自治体だけ**（`DEFAULT_CRAWLABLE_OPERATORS`）。検査は `Runtime.sources()` が必ず通る位置で行い、
   `-s` で絞っていても全 source を見る

`OperatorKind` に `prefecture` / `tourism_association` / `facility_official` / `transport_operator` を足した。

### この形にした理由
- 「自治体だけ」という規約を engine の `Source` 検証に埋め込むと、別のサービスを載せるたびに engine を緩めることになり、
  緩めた瞬間に既存サービスの担保も消える
- 既定を現行のまま（自治体だけ）にして「広げたいサービスが明示的に宣言する」向きにすれば、
  akiya-atlas は 1 行も変えずに現行の担保を保てる。宣言は grep 可能な 1 か所に集まる

## 影響
- akiya-atlas は `crawlable_operator_kinds` を宣言していないので、これまでどおり自治体と自治体の関連組織だけを巡回する
- japan-open-today は施設公式・観光協会・交通事業者を含めて宣言する（japan-open-today ADR 0009）
- サービスが独自の種別を使うときは、`extraction_spec(kind)` でその種別に仕様を返す責任を持つ
  （仕様が無い種別は抽出せず `no_spec` として数えられる）
