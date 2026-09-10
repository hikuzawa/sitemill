# 分類器の回帰テスト用に保存した自治体ページ

akiya-atlas の全県展開で、発見が「一覧ではないページ」を一覧として採用した 5 事例と、その正解ページ 2 件。
著作権は各自治体・運営者にある。再配布せず、分類器（`sitemill.classify`）の回帰テストにだけ使う。
取得は `sitemill/0.1 (akiya-atlas; ...)` の User-Agent で、robots.txt を確認して行った（2026-09-10）。

| ファイル | URL | 実態 | 期待する判定 |
|---|---|---|---|
| ikusaka_tour.html | https://www.village.ikusaka.nagano.jp/gyousei/sinkouka/aki_nougyou_taiken2026.html | 生坂村 秋の農業体験ツアー募集（旅行代金の「円」が 3 件） | not_listing |
| obuse_subsidy.html | https://www.town.obuse.nagano.jp/docs/36516.html | 小布施町 空き家改修等補助金の案内 | not_listing |
| sakaide_subsidy.html | https://www.city.sakaide.lg.jp/soshiki/seisaku/akiyakaisyuu.html | 坂出市 移住促進・空き家改修補助金 | not_listing |
| sakae_guide_834.html | http://www.vill.sakae.nagano.jp/docs/834.html | 栄村 空き家バンクのご案内（制度説明、一覧へのリンクあり） | not_listing |
| sakae_list_1028.html | http://www.vill.sakae.nagano.jp/docs/1028.html | 栄村 空き家バンク登録物件一覧（表: 価格・所在地の列） | listing_index |
| ogawa_house_page2.html | https://ogawamura.jp/house/page/2/ | 小川村 空家情報 2 ページ目（全件ご成約済） | listing_index、2 ページ目、1 ページ目は house/ |
| ogawa_house_page1.html | https://ogawamura.jp/house/ | 小川村 空家情報 1 ページ目（募集中と成約済が混在） | listing_index、1 ページ目 |

個人の電話番号・メールアドレスが含まれないことを `sitemill.build.pii` で確認した。自治体の代表電話は残している。
