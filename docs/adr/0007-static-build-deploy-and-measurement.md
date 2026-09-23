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

## 追記（2026-09-23）: 表示数の読み取りをエンジンに置く

2 つのサービス（akiya-atlas、japan-open-today）の `tools/report_clicks.py` が、Cloudflare の
GraphQL への同じ問い合わせをそれぞれ持っていた。配置ルールでは計測はエンジン側なので、
`sitemill.metrics.rum_pageloads(secrets, host, days)` に寄せる。返すのは「パス → 表示数」の
辞書だけで、どのパスを何の名前で並べるか（akiya-atlas は案件 × 枠、japan-open-today は
飛び先 × 言語）と、動作確認で開いた分の差し引きはサービス側に残す。

決めたこと:

- **絞り込みは `siteTag` ではなくホスト名（`requestHost`）**。同じホスト名で Web Analytics の
  登録が 2 つあると、HTML に挿し込まれている `data-cf-beacon` の token 側にイベントが 1 件も
  入らないことがある（japan-open-today で発生）。ホスト名ならどちらに入っていても取れる
- 必要な権限は **Account Analytics: Read**（配置用の権限だけでは 403）
- 鍵・権限・通信のいずれかが欠ければ `None` を返し、週次の集計は止めない。足りないものの案内は
  `sitemill.metrics.rum.NEED`
- 自動挿入のビーコンは**ブラウザのナビゲーションと同じ形の要求**にだけ入る。素の `fetch()` や
  `curl` の HTML には入らないので、`data-cf-beacon` の有無で計測を判定すると必ず「無い」と出る。
  `Accept: text/html` と `Sec-Fetch-Mode: navigate`（`NAVIGATION_HEADERS`）を付ければ入る。
  利用者側の UA は関係ない。同じ理由で、そうやって開いた分は表示数にも出ない
