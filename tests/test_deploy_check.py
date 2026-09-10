"""deploy の dry-run 検査（check_dist）。_redirects のパス source とドメイン単位 source を通す。"""

from sitemill.deploy.cloudflare_pages import check_dist


def test_check_dist_accepts_path_and_domain_level_redirects(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "_redirects").write_text(
        "# comment\n"
        "/go/test https://example.com/offer 302\n"
        "www.example.com/* https://example.com/:splat 301\n"
        "example-asb.pages.dev/* https://example.com/:splat 301\n"
        "https://old.example.com/* https://example.com/:splat 301\n"
        "bad-line\n"
        "nodomain/* https://example.com/ 301\n",
        encoding="utf-8",
    )
    plan = check_dist(tmp_path)
    assert [p for p in plan.problems if "_redirects" in p] == [
        "_redirects 6 行目の書式が不正: 'bad-line'",
        "_redirects 7 行目の書式が不正: 'nodomain/* https://example.com/ 301'",
    ]
