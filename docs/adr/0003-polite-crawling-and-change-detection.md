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

## 追記（2026-09-26）: robots.txt で見送り続けているホストを週次に出す

robots.txt が取れない・異常な応答を返す・拒否しているときに巡回しないのは正しい判断だが、
**続くとその情報源だけ静かに更新が止まる**。japan-open-today では CI から 2 ホストの robots.txt が
9/13 から毎晩時間切れになっていた（手元からは 1 秒以内に 200）。akiya-atlas では robots.txt が
202 を返し続けて 10 日止まっていた自治体と、拒否されたページを巡回先にしていた自治体があった。

`sitemill runs report`（`metrics.weekly.report`）に 1 行足す。

- 期間内の実行レポートから、robots.txt で見送ったホストの数と延べ回数を出す
- 巡回の状態（`data/state/crawl.json`）にいま robots の理由が残っている URL を見て、
  **最後に取得できた日から `ROBOTS_STALE_DAYS`（3）日以上**のホストは名前と理由、打ち手を出す
  （取得できない・異常な応答 → 相手に当たり直す、拒否 → 巡回先の URL を見直す）。
  実行レポートだけでは、巡回間隔の適応で「その晩は対象外だった」のか「見送った」のかが混ざるため
- 見送りの理由はエンジンが書く 3 通りすべてを拾う

akiya-atlas が先に `weekly.py` に持っていたものを、同じ引数・戻り値のままエンジンに移した
（`robots_failures(ws, days=, now=)` と `robots_lines(hosts, times, stalled, note=)`。
止まっていることの意味はサービスの言葉で `note` に渡す）。
