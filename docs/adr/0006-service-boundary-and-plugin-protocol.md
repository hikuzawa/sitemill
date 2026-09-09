# ADR 0006: sitemill はフレームワーク、サービスは Service プロトコルで差し込む

- ステータス: 採用（2026-09-10）

## 背景
sitemill は akiya-atlas のほか japan-life-checklist / japan-open-today でも使う。境界が曖昧だと固有ロジックがエンジンに混ざる。

## 決定
- 判断基準は「他のサービスでも使うか」。使うなら sitemill、使わないならサービス側
- サービスは `sitemill.service.Service` を実装したオブジェクトを公開し、`site.toml` の `service = "pkg.module:attr"` で指定する。提供するもの: Source 一覧、ページ種別ごとの `ExtractionSpec`（LLM 出力モデル・プロンプト・パーサ対応表・レコード変換）、ページ生成、検索索引、リダイレクト
- テンプレート・静的ファイル・データはサービス側リポジトリに置く。sitemill は信頼シグナルのマクロと共通フィルタだけを提供する
- CLI は sitemill が持ち、サービスのルート（site.toml のある場所）で実行する
- サービスからの依存は、今フェーズは `../sitemill` への editable なパス依存。エンジンが安定したら git タグ固定に切り替える（akiya-atlas ADR 0006）

## 影響
- sitemill のテストはサービスに依存しない（テスト用のダミー Service を持つ）
- サービス固有の判断（要約の長さ、価格帯の境界など）はサービス側の ADR に書く
