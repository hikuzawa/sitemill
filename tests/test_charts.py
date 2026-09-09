import xml.etree.ElementTree as ET

import pytest

from sitemill.charts import (
    Bar,
    bin_values,
    chart_css,
    column_chart,
    flow_diagram,
    hbar_chart,
    nice_ticks,
)

NS = {"svg": "http://www.w3.org/2000/svg"}


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1))


def test_nice_ticks() -> None:
    assert nice_ticks(7) == [0.0, 2.0, 4.0, 6.0, 8.0]
    assert nice_ticks(0) == [0.0, 1.0]
    assert nice_ticks(1234)[-1] >= 1234


def test_hbar_chart_is_valid_accessible_svg() -> None:
    bars = [
        Bar("東御市", 12),
        Bar("佐久市", 7, tooltip="佐久市: 7 件（うち賃貸 2）"),
        Bar("<x>", 0),
    ]
    chart = hbar_chart("市町村別の掲載件数", bars, desc="3 市の件数", unit="件")
    root = _parse(chart.svg)
    assert root.attrib["role"] == "img" and root.attrib["data-generated"] == "sitemill.charts"
    assert root.find("svg:title", NS).text == "市町村別の掲載件数"  # type: ignore[union-attr]
    assert root.find("svg:desc", NS).text == "3 市の件数"  # type: ignore[union-attr]
    titles = [t.text for t in root.findall(".//svg:g/svg:title", NS)]
    assert "佐久市: 7 件（うち賃貸 2）" in titles and "東御市: 12件" in titles
    assert "&lt;x&gt;" in chart.svg and "<x>" not in chart.svg
    assert chart.svg.count('fill="var(--chart-series-1') == 3
    assert "12件" in chart.svg  # 8 本以下なので直接ラベルあり
    assert '<th scope="row">東御市</th><td>12件</td>' in chart.table_html
    html = chart.html()
    assert 'data-generated="sitemill.charts"' in html and "自動生成" in html


def test_direct_labels_are_dropped_when_many_bars() -> None:
    bars = [Bar(f"市{i}", i + 1) for i in range(9)]
    chart = hbar_chart("多い", bars, unit="件")
    assert ">9件</text>" not in chart.svg and "9件" in chart.table_html


def test_column_chart_and_bins() -> None:
    labels = ["100万未満", "100〜300万", "300〜500万", "500万以上"]
    bars = bin_values([50, 120, 299, 300, 900, 1200], [100, 300, 500], labels)
    assert [b.value for b in bars] == [1, 2, 1, 2]
    chart = column_chart("価格帯の分布", bars, unit="件")
    root = _parse(chart.svg)
    assert root.attrib["data-chart"] == "column"
    assert len(root.findall(".//svg:g/svg:title", NS)) == 4
    assert "500万以上" in chart.svg
    with pytest.raises(ValueError):
        bin_values([1], [1, 2], ["a", "b"])


def test_flow_diagram_wraps_rows_and_lists_steps() -> None:
    steps = ["現地確認と書類準備", "査定の申込み", "査定結果の比較", "媒介契約", "売却・引き渡し"]
    chart = flow_diagram("売却の流れ", steps, per_row=3)
    root = _parse(chart.svg)
    assert len(root.findall(".//svg:rect", NS)) == 5
    assert root.attrib["viewBox"].split()[-1] != "0"
    assert "手順を文章で見る" in chart.table_html and "5</th><td>売却・引き渡し" in chart.table_html
    empty = flow_diagram("空", [])
    assert _parse(empty.svg) is not None


def test_chart_css_defines_light_and_dark_tokens() -> None:
    css = chart_css()
    assert "--chart-series-1:#2a78d6" in css and "--chart-series-1:#3987e5" in css
    assert 'data-theme="dark"' in css
