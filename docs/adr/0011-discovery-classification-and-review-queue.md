# ADR 0011: 発見・分類・レビュー待ち行列を汎用モジュールとして持つ

- ステータス: 採用（2026-09-10）

## 背景
複数の都道府県・複数のサービス（akiya-atlas ほか）で、候補ページの自動発見→分類→確信度づけ→人間レビューを回したい
（akiya-atlas ADR 0007）。この骨組みはサービスに依存しないため sitemill に置く。

## 決定
- `classify/`: ページ分類器。候補ページを 一覧(listing_index) / 詳細(listing_detail) / SPA(spa) /
  第三者プラットフォーム(third_party) / 非物件(not_listing) に確信度つきで分類する（`ClassifiedPage`）。
  判定は価格表記・物件番号・項目ラベル・スクリプト量・SPA マーカーなどの複数シグナルの一致度で行い、LLM 不要。
- `classify/platforms.py`: 既知の第三者プラットフォームのホスト一覧を sitemill が既定で持ち、`PlatformRegistry` で
  サービス側から追記できる（akiya-atlas ADR 0007 条件4）。
- `review.py`: レビュー待ち行列（`ReviewQueue` / `ReviewCandidate`）。低確信の候補だけを Markdown 表で提示し
  （対象・候補URL・分類・確信度・根拠・提案アクション）、人間が行ごとに承認/却下/URL修正を返せる。YAML で永続化する。
- 巡回の可否は分類だけでは決めない。運営主体ゲート（サービス側）と組み合わせ、lg.jp か人間承認のどちらか無しには
  巡回しない（akiya-atlas ADR 0007 条件）。

## 影響
- サービスは分類結果と運営主体の根拠から確信度を計算し、高確信は自動採用、低確信は `policy=pending` でレビュー行列に残す。
- 実データでの分類精度はサービス側の fixture で検証する（sitemill のテストは合成 HTML で行い、公開リポジトリに
  自治体 HTML を再配布しない）。
