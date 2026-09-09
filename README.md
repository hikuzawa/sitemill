# sitemill

公的・公式サイトを「監視 → 差分検知 → 構造化 → 静的サイト生成 → デプロイ → 計測 → 改善」する共通エンジン。
ライブラリ兼 CLI として設計し、各サービス（akiya-atlas など）は `Service` を実装して差し込む。

- 設計判断は `docs/adr/` に ADR として残す
- 規約は `CLAUDE.md` を参照
- 開発: `uv sync` → `uv run pytest`
