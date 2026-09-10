"""deploy の dry-run 検査（check_dist）。Pages の _redirects はドメイン単位の source を扱えない。

公式の Advanced redirects 表で「Domain-level redirects」は非対応（wrangler は受理するが効かない）。
www → apex や pages.dev → apex は Bulk Redirects（Cloudflare 側）で行う。ここでは source が
「/パス」以外の行を書式不正として止め、効かないルールが配置されるのを防ぐ。
"""

from sitemill.deploy.cloudflare_pages import check_dist


def test_check_dist_rejects_domain_level_redirect_sources(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "_redirects").write_text(
        "# comment"
        + chr(10)
        + "/go/test https://example.com/offer 302"
        + chr(10)
        + "www.example.com/* https://example.com/:splat 301"
        + chr(10)
        + "https://old.example.com/* https://example.com/:splat 301"
        + chr(10),
        encoding="utf-8",
    )
    plan = check_dist(tmp_path)
    assert [p for p in plan.problems if "_redirects" in p] == [
        "_redirects 3 行目の書式が不正: 'www.example.com/* https://example.com/:splat 301'",
        "_redirects 4 行目の書式が不正: 'https://old.example.com/* https://example.com/:splat 301'",
    ]
