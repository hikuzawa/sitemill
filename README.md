# sitemill

公的・公式サイトを「監視 → 差分検知 → 構造化 → 静的サイト生成 → デプロイ → 計測」する共通エンジン。
ライブラリ兼 CLI として設計し、各サービス（[akiya-atlas](https://github.com/hikuzawa/akiya-atlas) など）は
`Service` プロトコルを実装して差し込む。

## 何をするか

- **巡回（`fetch/`）**: robots.txt を尊重し、ホストごとに間隔を空けて取得する。ETag / If-Modified-Since による条件付き GET と文字コード判定つき。
- **差分検知（`diff/`）**: 本文を正規化した SHA-256 で比較し、変化したページだけを次段に渡す。巡回状態は JSON で保存する。
- **構造化（`extract/`）**: quote-then-parse 方式。LLM には値ではなく「原文の引用」を返させ、引用が本文に実在することを検証してから、決定的パーサ（`parse/jp/` の金額・面積・和暦・所在地）で値にする。推定値は混ぜない。
- **静的生成（`build/`）**: Jinja2 でページを描画し、sitemap・robots・リダイレクト・検索索引を出力する。信頼シグナル（更新日時・一次情報リンク・運営者・件数）が欠けたページはビルドを失敗させる。
- **図解・埋め込み（`charts/`, `embeds/`）**: 依存なしの SVG グラフと、Google Maps などの埋め込み（転載ではなく埋め込み機能経由のみ）。
- **ライセンス判定（`license/`）**: ホワイトリスト（CC BY / CC0 / 政府標準利用規約 2.0 など）に一致した明示的な証拠がある場合だけ採用。既定は不採用。
- **計測（`metrics/`）**: 実行レポートと、fixture による抽出精度の計測（項目別の抽出率・null 理由）。
- **案件選定（`affiliate/`）**: ASP の検索結果を貼ると、案件を構造化して導線適合・地域・成果条件の重さ・確定率・EPC・掲載の是非で判定し、申請すべき順に並べる。除外は理由つきで残す。物差しはサービスごとの YAML（`profiles/` に雛形）。
- **秘密検出（`scan-secrets`）**: コミット前フックと CI で、鍵・トークン・`.env` の混入を止める。

設計判断は [`docs/adr/`](docs/adr/) に ADR として残している。

## 使い方

```bash
uv sync
uv run pytest
# サービス側のルート（site.toml のある場所）で:
uv run sitemill run          # crawl → extract → build
uv run sitemill eval         # 保存済み fixture で抽出精度を計測
# どこでも実行できる:
uv run sitemill offers screen --profile profile.yaml --input paste.txt   # ASP 案件の選定
```

コミット前フックの有効化（clone 後に一度）:

```bash
git config core.hooksPath .githooks
```

## 対象と免責

- 対象は robots.txt で許可された公的・公式サイトを想定する。利用者は各サイトの利用規約と robots.txt、および適用される法令を自ら確認すること。
- 画像・データの再利用は、明示的なライセンスがホワイトリストに一致した場合に限る。判定が曖昧なものは採用しない。
- 本ソフトウェアは MIT ライセンスで「現状のまま」提供され、抽出結果や生成物の正確性・適法性を保証しない（[LICENSE](LICENSE)）。
- 秘密情報（API キー等）はコードにも設定ファイルにも含めない。`.env` と環境変数だけで扱う。

## ライセンス

MIT。[LICENSE](LICENSE) を参照。
