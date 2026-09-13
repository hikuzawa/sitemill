# Search Console の取り込みを、サービスで有効にする

sitemill v0.4.1 以降。設計は ADR 0023。**japan-open-today では稼働中**なので、そちらの
`site.toml` と `.github/workflows/` が動く見本になる。

この手順は 1 サービスにつき 1 回。以後は日次パイプラインが取り込み、週次が Issue に出す。

## 0. 済んでいること（2026-09-14 時点、両サービス共通）

人の作業は終わっている。**やり直す必要はない**。

- Google Cloud のサービスアカウント `sitemill-search-console@japan-open-today.iam.gserviceaccount.com`
- Search Console の 2 プロパティ（`sc-domain:akiya-atlas.com` と `sc-domain:japan-open-today.com`）に
  「制限付き」で追加済み。どちらもドメインプロパティ
- 鍵は `.env` と GitHub Secrets の `GOOGLE_SEARCH_CONSOLE_KEY`
  （サービスアカウントの JSON を base64 にした 1 行）。**両リポジトリに登録済み**

疎通は次で確かめられる（メールアドレスとプロパティ名しか表示しない）。

```bash
uv run sitemill search properties
```

## 1. sitemill のタグを上げる

`.github/workflows/` の `ref:` を **v0.4.1** にする（akiya-atlas は 3 本: pipeline / checks / weekly）。
v0.1.1 から上げる場合は、あいだの変更を確かめてから上げること。特に **v0.3.0 で画像の
ホワイトリストに CC BY-SA が入っている**（ADR 0020 追記）。akiya-atlas は画像を 1 枚も
公開していない（`data/assets` が無く、`service.py` に `assets` フックが無く、`dist` に `<img>` が無い）
ので影響しないことを確認済みだが、上げたあとに `uv run pytest` と `uv run sitemill build` を通すこと。

## 2. `site.toml` に設定を足す（省略可）

```toml
[search_console]
property = "sc-domain:akiya-atlas.com"
inspect_per_day = 200   # 1 日に URL 検査を回す件数。Google の割り当ては 2,000 件/日
refresh_days = 5        # Search Console の数字は 2〜3 日遅れて確定する
```

省略すると `base_url` のホストから `sc-domain:` を組み立てる。akiya-atlas はドメイン
プロパティなので省略しても同じ値になるが、**明示しておくほうが読み手に親切**。

`inspect_per_day` は「全 URL ÷ この値」日で一巡する。ページ数が多いサービスは、
一巡に何日かかるかを見て決める（akiya-atlas は物件ページが多いので、200 なら一巡に日数がかかる。
週次レポートに「サイト全体の何 % を検査済みか」が出るので、それを見て調整する）。

## 3. 日次パイプラインに 1 段足す

`build` の後、`Commit data changes` の**前**に置く。生成した `dist/sitemap.xml` を URL の一覧に
使うので build の後、取り込んだ結果を同じ実行でコミットしたいので commit の前。

```yaml
      - name: Search Console の取り込み（検索パフォーマンス・サイトマップ・URL 検査。ADR 0023）
        if: inputs.mode != 'deploy-only'
        working-directory: akiya-atlas
        env:
          GOOGLE_SEARCH_CONSOLE_KEY: ${{ secrets.GOOGLE_SEARCH_CONSOLE_KEY }}
        # 鍵が無い間は取り込みを飛ばす（パイプライン全体は止めない）
        continue-on-error: true
        run: uv run sitemill search fetch
```

コミットの行に `data/search` を足す。

```yaml
          git add data/state data/records data/runs data/search
```

## 4. 週次に要約を足す

akiya-atlas の `weekly.yml` は `akiya-atlas weekly-report` の出力を Issue にしている。
その**前**に検索の節を付ける（インデックスの数字が先に来るように）。

```yaml
      - name: まとめを作る
        working-directory: akiya-atlas
        env:
          GOOGLE_SEARCH_CONSOLE_KEY: ${{ secrets.GOOGLE_SEARCH_CONSOLE_KEY }}
        run: |
          uv run sitemill search report --days "${{ inputs.days || 7 }}" > summary.md
          echo "" >> summary.md
          uv run akiya-atlas weekly-report --days "${{ inputs.days || 7 }}" --source ci --no-save >> summary.md
          cat summary.md
```

`search report` は取り込んだ記録だけを読むので、鍵が無くても（0 件として）動く。

## 5. 手元で 1 回試す

```bash
uv run sitemill search fetch --inspect 8   # URL 検査を 8 件だけにして試す
uv run sitemill search report
```

`data/search/` に JSONL と JSON ができる。これはコミットする（生の HTML と違い、
再取得できない記録なので残す）。

## 読み方の注意（間違えると嘘の数字を出す）

- **`sitemaps` API の `indexed` は使わない。** Google が更新しておらず常に 0 で、
  そのまま出すと「N ページ送って 0 ページ登録」という嘘になる。インデックス数は URL 検査で数える
- **サイトマップの「送信 URL 数」はサイトの URL 数ではない。** Google が最後に取得した
  サイトマップの中身なので、ページを増やした直後はずれる（レポートは「再取得待ち」と書く）
- **検査していない分を「登録されていない」と読まない。** レポートは必ず
  「検査した N ページのうち M ページが登録済み」と、サイト全体の何 % を検査したかを併記する
- Search Console の数字は 2〜3 日遅れて確定する。だから毎日「直近 5 日」を取り直して**上書き**する。
  追記にすると確定前の数字が残って二重になる
- 日付は必ず `sitemill.clock`（JST）で決める。実行機は UTC なので、素の `date.today()` だと
  JST の朝に前日になる（v0.4.1 で実際に踏んだ）

## 費用と負荷

Google の API だけを叩く。1 日あたり検索パフォーマンス 3 回＋サイトマップ 1 回＋URL 検査
`inspect_per_day` 回。巡回先のサイトには触れない。API は無料。
