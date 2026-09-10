# ADR 0012: 公開前チェックと個人情報検査をビルドに組み込む

- ステータス: 採用（2026-09-10）

## 背景
生成した静的サイトを公開する前に、検索エンジンと利用者に対して最低限の体裁と安全性を
保証したい。信頼シグナル（ADR 0007 / trust.py）は各ページに既にあるが、サイト全体としての
公開準備（robots・sitemap・404・運営者情報）や、SEO 用のメタデータ（canonical・OGP・
JSON-LD）、そして「個人情報を載せない」という方針の自動担保が無かった。手作業のチェックは
横展開（都道府県を増やす）で破綻するので、ビルドの一部として機械的に検査し、欠ければビルドを
止める。

## 決定

### 1. ページ個別チェック（`build/preflight.py: check_page_html`）
各ページの描画直後に、信頼シグナル検査（`verify_page_html`）に続けて次を検査する。欠けると
`BuildError` でビルドを止める。
- **canonical**: `<link rel="canonical">` があること。
- **OGP**: `og:title` / `og:type` / `og:url` があること。
- **JSON-LD**: `application/ld+json` ブロックが 1 つ以上あり、いずれも妥当な JSON であること。
- `noindex` のページ（404 など）は索引対象でないため canonical / OGP / JSON-LD を免除する
  （JSON-LD があれば妥当性だけは検査する）。

メタデータは共通マクロ `sitemill/macros.html: head_meta(meta, site, base_url, ...)` が出力する。
`PageMeta.structured_data`（dict の一覧）を Jinja の `tojson` で埋め込むので、JSON-LD は
構築時点で必ず妥当になる。サービスは各ページに載せたいノード（WebSite / Organization 等）を
`structured_data` に入れるだけでよい。

### 2. サイト全体チェック（`build/preflight.py: check_site`）
全ファイル出力後に dist 全体を検査する。欠けると `BuildError`。
- `robots.txt` / `sitemap.xml` / `404.html` / `about/index.html` が存在すること。
- `robots.txt` が `Sitemap:` 行を持つこと。
- `/about` に「運営者」欄と「免責」の記載があること（運営者名は準備中でもよい。文言の有無を見る）。
- **解析タグの出力条件**: Cloudflare Web Analytics のビーコンは、トークンが設定されている
  ときだけ出力する（`metrics/analytics.py`）。トークン未設定なのにビーコンが出力されていれば
  失敗、設定済みなのにどのページにも無ければ失敗（回帰防止）。

### 3. サイト検証ファイルの仕組み
- **ファイル方式**: サービスルートの `verification/` に置いたファイルを、ビルド時に dist 直下へ
  複写する（`README.md` と隠しファイルは除く）。Search Console 等の HTML ファイル確認に使う。
- **メタタグ方式**: `.env` の `GOOGLE_SITE_VERIFICATION` があれば全ページ `<head>` に
  `<meta name="google-site-verification">` を出力する（未設定なら出力しない）。
- 検証はサイト登録後に行うため、ファイルやトークンが無くてもビルドは止めない（仕組みだけ用意する）。

### 4. 個人情報（PII）検査（`build/pii.py`）
レコードと生成ページに、個人の氏名・電話番号・メールアドレスらしき文字列が無いことを検査する。
- **検出**: メール（正規表現）、日本の電話番号（0 始まり 3 グループ／括弧市外局番。郵便番号は
  2 グループなので除外）、氏名（漢字 2〜4 文字 + 敬称「様/さん/氏」、および「氏名: ◯◯」等の
  ラベル付き）。全角の数字・記号は半角化してから走査する。誤検出でビルドを不必要に止めないよう、
  氏名は高精度パターンに絞り、一般語（皆様・お客様等）は除外する。
- **ホワイトリスト（`PiiPolicy`）**: 自治体・官公庁の代表メール（`.lg.jp` / `.go.jp` / 地理型
  `city.*/town.*/vill.*` ドメイン）は許可する。代表電話は明示登録したものだけ許可する。
  運営者自身の連絡先（site.toml の operator.contact）は第三者の個人情報ではないので許可する。
- **サービス拡張**: サービスは任意で `pii_policy(ws)` を実装してポリシーを差し込める。
  akiya-atlas は「運営主体と確認できた source の運営根拠（引用）に載る電話番号を代表電話として
  許可する」ポリシーを渡す（レコード本文中の番号は許可しない）。

## 影響
- すべての sitemill サイトが、公開前に同じ基準（体裁・SEO メタ・PII 不在）で検査される。
  japan-life-checklist / japan-open-today でも同じ仕組みがそのまま効く。
- サービス側の責務は「各ページに `structured_data` を載せる」「テンプレートで `head_meta` を呼ぶ」
  「必要なら `/about`・404 ページと `pii_policy` を用意する」だけ。
- 検査はネットワーク不要で、生成物だけを対象にするため CI でも安全に走る。
