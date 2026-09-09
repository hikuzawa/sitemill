# ADR 0003: 礼儀正しい巡回と差分検知

- ステータス: 採用（2026-09-10）

## 背景
対象は自治体などの小規模サイトで、負荷をかけず、robots.txt を守り、変化したページだけを LLM に渡す必要がある。

## 決定
- User-Agent は `sitemill/<version> (<service id>; +<base_url>/about/)` の形で連絡先を明示する
- robots.txt は protego で解釈し、ホストごとにキャッシュする。404 は全許可、5xx や接続失敗はその実行では巡回しない（保守的に倒す）
- ホストごとに既定 3 秒＋ジッタの間隔を空け、robots の Crawl-delay が大きければそちらに従う
- ETag / Last-Modified による条件付き GET を使い、304 なら未変更として扱う
- 変化判定は「本文の正規化テキスト」の SHA-256。script/style/nav/header/footer を除き、サービスが `content_selector` を指定できる。空白と Unicode NFKC を正規化する
- 変化した URL だけに `pending_extract` を立て、抽出段はそれだけを処理する
- 巡回範囲は各 Source の `pages`（seed）と `follow` パターン、`allow_hosts`、`max_pages` で明示的に制限する。`policy = link_only` の Source は一切取得しない

## 影響
- 1 実行あたりの取得数は Source 数 × max_pages に上限づけられる
- 文字コードは HTTP ヘッダ → meta charset → 自動判定の順で決める（`fetch/decode.py`）
