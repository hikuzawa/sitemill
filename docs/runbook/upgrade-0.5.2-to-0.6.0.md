# akiya-atlas を sitemill v0.5.2 から v0.6.0 に上げる

akiya-atlas のセッションが読む手順。japan-open-today は 2026-09-17 に v0.5.0 → v0.6.0 に上げ済み
（`6a919a5`）。

## 何が変わるか（v0.5.2 → v0.6.0）

`git log v0.5.2..v0.6.0` のうち、中身のあるものは 4 つ。akiya-atlas への効き方で並べる。

| コミット | 内容 | akiya-atlas への効き方 |
|---|---|---|
| `0569aec` | **生 HTML のキャッシュを、巡回状態と照合してから使う**（ADR 0024 追記。akiya-atlas からの指摘） | **日次の動きが変わる**。下の「上げた後に見ること」 |
| `19cee86` `5db66ac` | もしもアフィリエイトの貼り付けで、提携状況（未申請など）を案件名にしない。「本人 NG」などの可否の札を見出しにしない。「検索結果 31件」「並び替え 新着順」を画面部品として捨てる | `sitemill offers screen` の結果が変わる。**実データでは v0.6.0 の時点で直り切っておらず、main で続きを直した**（下の「もしもの読み取り」） |
| `cb5b4f3` | pre-commit フックの gitleaks を `git` / `protect` で分岐 | 対応なし。akiya-atlas は `e65c218` で同じ形にしてある |
| `e23102f` | ADR 0024「守りたい性質は公開する直前に確かめる。コミットするものはキャッシュしない」と、sitemill の CLAUDE.md への 1 項目 | 文書だけ。下の「確かめること」 |

中身の無いもの:
- `e5948be`（`release/0.5` を main にマージ）、`e1b612b` `6fee3b0`（同じ 2 コミットが `git pull --rebase` で
  平らに積み直されたもの。**中身は v0.5.2 にある `c29ab3d` `3131832` と同一**）、`df7c496` `e9fc932`（版を 0.6.0 に）、
  `0713e0f`（タグのコミットを main に合流。ファイルの変更なし）、`34829e0`（0.5.0 の lock）
- PII の修正（v0.5.1）は akiya-atlas には v0.5.2 で届いている。v0.6.0 で新しく入る PII の変更は無い

**`release/0.5` は削除した**。v0.5.1 / v0.5.2 のタグは残っていて、コミットは main からも辿れる。
これからタグは main から打つ（sitemill の CLAUDE.md）。

## 手順

1. `.github/workflows/` の **4 本**（`checks.yml` / `pipeline.yml` / `search.yml` / `weekly.yml`）の
   sitemill の `ref: v0.5.2` を `ref: v0.6.0` にする
2. `uv lock` で `uv.lock` の sitemill を 0.6.0 にする（手元は `../sitemill` の editable なので、
   `uv run` するだけでも書き換わる。CI は `uv sync --frozen` なので lock をコミットしておく）
3. `uv run pytest` と `uv run ruff check src tests`（2026-09-17 に v0.6.0 と同じコミットで 145 passed を確認済み。
   `uv run --frozen` で、lock を書き換えずに流した）
4. コミットして push。checks が通ることを見る
5. pipeline を 1 本実行する

## 上げた後に見ること

### 生 HTML のキャッシュの照合（`0569aec`）

akiya-atlas の `actions/cache` は `data/raw` と `data/llm_cache` だけを持つ（コミット対象は入っていない。
ADR 0024 §2 は満たしている）。ただし `data/raw` そのものが状態より古くなりうるのが今回の穴。

- **巡回**: キャッシュのメタに記録した `content_hash` と、`crawl.json` の `content_hash` が食い違えば、
  ETag / Last-Modified を付けずに取り直す。件数は実行レポートの `crawl.stale_cache`
- **抽出**: 食い違えば抽出せず `pending_extract` のまま残す。件数は `extract.stale_cache`。
  実行レポートの `errors` に「生 HTML のキャッシュが巡回状態より古い」という行が入るが、
  **ジョブは失敗しない**。akiya-atlas の失敗通知はジョブの失敗で立つ作りなので、これで Issue は立たない
  （初版に「Issue が立つ」と書いたのは誤り。akiya-atlas のセッションの指摘で直した）。
  気づくには実行レポートの件数を見る

**最初の数回の `crawl.stale_cache` を記録しておく**。0 なら「起きた形跡が無い」の裏づけになる。
0 でなければ、キャッシュが状態より古い組が実際にあったということで、その回の取り直しで解消する。
ただし 0 に意味があるのは、**復元したキャッシュに生 HTML が入っていた回だけ**。キャッシュが無ければ照合する
相手が無く、必ず 0 になる（`crawl.not_modified` も 0 になるので見分けられる）。japan-open-today の v0.6.0 初回
（2026-09-17）はこれで、直前が巡回しない deploy-only だけだったので意味のある値ではなかった。
akiya-atlas は日次でキャッシュに生 HTML が残っているので、上げた直後の回から意味がある。
正規化の設定（`content_selector` / `ignore_patterns`）を変えても食い違いにはならない
（本文から計算し直さず、保存時に記録したハッシュで比べているため）。

### もしもの読み取り（`19cee86` `5db66ac`、**続きの修正は v0.6.0 の後**）

v0.6.0 の時点では、実データで「案件名が提携状況になる」は直っていたが、**31 件中 28 件で次の案件の
見出し 4 行が前の案件の原文に付いていた**（akiya-atlas `docs/design/04-sitemill-proposals.md` の G 節）。
v0.6.0 の後の main で直した。`offers screen` は手元の `../sitemill`（main）で動くので、**タグを待たずに使える**
（CI には関係しない）。

直し方: もしもの案件カードは「案件名 → `サイト` → `成果` → 提携状況 → 案件名（再掲）→ 成果条件 → 札」の並び。
`サイト` の行を**カードの始まり**として、その直前の 1 行を案件名にして切る。カードの中で名前が繰り返されても、
「未申請」と「審査あり」が同じ提携の項目でも割らない。成果条件の見出し側の長さの上限を 40 → 80 字にした。

sitemill 側で `moshimo-fudosan.txt`（akiya-atlas のセッションの一時ディレクトリにあったもの）を通した結果:

| 確かめること | v0.6.0 | 修正後 |
| --- | --- | --- |
| 記録の数（原文のカード 30 枚） | 31 | **30** |
| 他の案件の見出しが原文に混ざった記録 | 29 | **0** |
| 取りこぼした見出し（AI鬼管理） | 1 | **0** |
| 成果条件の行を案件名と読んだもの | 1 | **0** |
| 1 案件が 2 つに割れたもの（Hoan Garden Kushiro） | 1 | **0** |

akiya-atlas の `profile.yaml` で `offers screen` を通すと、G 節の 4 件（Emanon・税務調査・地域文化振興機構・
総合資格学院）は種別が空になり「除外」に落ちた。ほかの 26 件の種別は変わらない。
回帰テストは架空の案件名で同じ並びを作った `tests/fixtures/affiliate/asp-list-moshimo-cards.txt`
（実データは ASP の掲載内容なので public のリポジトリに入れない）。

## 確かめること（ADR 0024）

- `.github/workflows/` の `actions/cache` の `path` に、コミット対象（`data/records`、`data/state`、
  補助制度のデータなどサービス固有のコミットするもの）が入っていないか。2026-09-17 時点の `pipeline.yml` は
  `data/raw` と `data/llm_cache` だけで問題ない。他の workflow にキャッシュがあれば同じように見る
- 「一度直したのに戻った」ことがあれば、戻した経路を探す前に、公開直前の検査を足す

## 訂正

sitemill `v0.6.0` のコミットとタグのメッセージにある「gitleaks 8.30 で `protect` が削除された」は誤り。
8.30.1 でも `protect` はヘルプに出ないだけで動く（akiya-atlas のセッションが実測）。フックの分岐は
どちらの版でも動くので、対応は要らない。
