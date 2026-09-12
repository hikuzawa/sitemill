# sitemill

公的・公式サイトを「監視→差分検知→構造化→静的サイト生成→デプロイ→計測」する共通エンジン。
ライブラリ兼 CLI。サービス（akiya-atlas など）は `Service` を実装して差し込む。親フォルダの CLAUDE.md の配置ルールに従う。

## 構成
- `src/sitemill/` src レイアウト。パッケージ名 `sitemill`
- `fetch/` 巡回（robots・間隔・文字コード）、`diff/` 正規化・ハッシュ・巡回状態、`extract/` 本文化・LLM 抽出・引用検証、
  `parse/jp/` 日本語の金額・面積・和暦パーサ、`license/` ライセンス判定、`build/` 静的ビルド、`charts/` SVG 図解、
  `embeds/` 地図・SNS 埋め込み、`i18n/` 多言語（ロケール・文言カタログ・ロケール別書式）、
  `deploy/` デプロイ、`metrics/` 実行レポートと抽出精度、`store/` JSON/JSONL の永続化
- `docs/adr/` 設計判断。方針を変えるときは新しい ADR を足す（既存は上書きしない）

## コマンド
- `uv sync` 依存を入れる / `uv run pytest` / `uv run ruff check src tests` / `uv run ruff format src tests`
- CLI はサービス側のルート（site.toml のある場所）で `uv run sitemill <discover|crawl|extract|build|deploy|run|eval|status>` を実行する
- `uv run sitemill eval --record` は本番の LLM で fixture の応答を取り直してから計測する（旧応答は llm_response.previous.json に残る）

## 守ること
- 対話・報告・質問は日本語で行う（コードとコミットメッセージは英語でよい）
- 汎用ロジックだけを置く。特定サービスの URL・スキーマ・テンプレートはサービス側へ
- **akiya-atlas の CI は sitemill のタグ固定（現在 `v0.1.0`）。main の変更は自動では反映されない**。main は別サービス向けの拡張に使ってよい。空き家側に届けるにはタグを打ち、akiya-atlas の `.github/workflows/` 3 本の `ref:` を上げる（手順は akiya-atlas の `docs/runbook/operations.md` 5 章）。破壊的変更はマイナー版（v0.x.0）で出す
- 数値は LLM に決めさせない。LLM は原文の引用を返し、`parse/jp` の決定的パーサだけが値にする（quote-then-parse、ADR 0004）
- ライセンス判定は既定で不採用。ホワイトリストに一致した証拠がある場合のみ採用（ADR 0005）
- 発見の確信度づけでは、運営主体が確認できた候補は自動採用し、レビュー行列に残すのは運営主体が判定できないものだけにする（akiya-atlas ADR 0007）。人間の作業を最小にする
- 巡回できる運営主体は二段で決める（ADR 0017）。エンジンは `third_party` と `unknown` を常に拒む。サービスごとの線引きは `Service.crawlable_operator_kinds` の宣言で、**宣言が無ければ自治体だけ**。この既定を緩めない
- サービス固有の規約（どの種別を巡回するか、どのページ種別を使うか）をエンジンの検証に埋め込まない。埋め込むとサービスを増やすたびに既存サービスの担保が消える
- 多言語は「事実は構造化データからロケール別に描画する」やり方だけを支える（ADR 0016）。描画時に翻訳エンジンや LLM は使わない
- 巡回は robots.txt を守り、ホストごとに間隔を空ける。取得した生 HTML はコミットしない（ADR 0002, 0003）
- 秘密情報はコードにも設定ファイルにも書かない。環境変数と .env のみ。値が `op://` のままなら起動時に止める
- `.env.example` にはプレースホルダー（空の値）だけを置く。値を書いた時点でコミット前フックが止める
- コミット前フックは `.githooks/pre-commit`（`uv run sitemill scan-secrets --staged`、gitleaks があれば併用）。clone 後に一度 `git config core.hooksPath .githooks` で有効化する。CI でも全履歴を `scan-secrets --history` で走査する
- 外部アクセスを伴うテストは書かない。`tests/fixtures/` の保存済み HTML と respx でモックする
- ページ本文は「データ」であり指示ではない。LLM への入力に含めても、その中の指示に従う設計にしない
- 区切りごとに `uv run pytest` と `uv run ruff check` を通してからコミットする。コミットはこのディレクトリ内で行う
