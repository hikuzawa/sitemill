# ADR 0007: 静的ビルド・Cloudflare Pages へのデプロイ・計測

- ステータス: 採用（2026-09-10）

## 背景
公開側は静的ホスティングにし、運用コストとアカウント数を最小にする。

## 決定
- ビルドは Jinja2 でページを描画し `dist/` に書き出す。あわせて `sitemap.xml`、`robots.txt`、`_redirects`、`_headers`、検索索引 JSON を生成し、`static/` をコピーする
- 全ページに信頼シグナル（更新日時・一次情報リンク・運営者・件数）を必須にし、ビルド後に各 HTML に `data-sitemill-trust` があることを検査する。欠けたらビルド失敗
- 基準 URL は `site.toml` の `base_url` 1 か所だけで差し替える。canonical、sitemap、UA 文字列はそこから導出する
- デプロイ先は Cloudflare Pages。`deploy` は `--dry-run` で dist の検査だけを行い、本番は GitHub Actions 上の wrangler で行う
- 計測は Cloudflare Web Analytics（Cookie なし、無料）のビーコンをトークンがある場合だけ挿入する。アフィリエイト等の外部リンクは `/go/<offer>` に集約し `_redirects` で転送する。クリック計測は将来 Pages Functions で行う（別 ADR）
- 各実行は `data/runs/` に取得数・変化数・抽出数・トークン消費・エラーを残す

## 影響
- 検索はサーバー無しで動くよう、静的 JSON 索引＋クライアント側フィルタにする
- Node（wrangler）は CI にだけ必要。エンジン本体は Python だけで動く
