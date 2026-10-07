from decimal import Decimal

import pytest


def test_integer_count_quantity_does_not_show_decimal_padding():
    from erp.utils import quantity

    assert quantity.format_quantity(Decimal("15.000"), "个") == "15"
    assert quantity.format_quantity_3dp(15000, "个") == "15"


@pytest.mark.parametrize("unit", ["个", "件", "套", "只", "台", "瓶", "箱", "包", "组"])
def test_count_units_preserve_historical_fractional_values(unit):
    from erp.utils.quantity import format_quantity, format_quantity_3dp

    assert format_quantity(Decimal("1.2500"), unit) == "1.25"
    assert format_quantity_3dp(1001, unit) == "1.001"


@pytest.mark.parametrize("unit", ["米", "公斤", "千克", "升", "自定义单位", ""])
def test_measure_and_unknown_units_preserve_all_nonzero_decimal_digits(unit):
    from erp.utils.quantity import format_quantity, format_quantity_3dp

    assert format_quantity("12.340000", unit) == "12.34"
    assert format_quantity("0.000000000000000000123400", unit) == "0.0000000000000000001234"
    assert format_quantity_3dp(12345, unit) == "12.345"


def test_quantity_missing_zero_negative_and_large_values_are_exact():
    from erp.utils.quantity import format_quantity, format_quantity_3dp

    assert format_quantity(None, "个") == "—"
    assert format_quantity_3dp(None, "米") == "—"
    assert format_quantity("-0.000", "个") == "0"
    assert format_quantity_3dp(0, "个") == "0"
    assert format_quantity_3dp(-1234, "千克") == "-1.234"
    assert format_quantity_3dp(123456789012345678901234567890123, "米") == "123456789012345678901234567890.123"


def test_product_list_renders_count_and_measure_quantities_in_separate_columns():
    from html.parser import HTMLParser

    from erp import create_app
    from erp.services.accounting import create_product
    from erp.services.inventory import initialize_product

    app = create_app()
    count_id = create_product("计件展示商品", "件型", "个", 1000, safety_stock="3")
    measure_id = create_product("计量展示商品", "米型", "米", 2000, safety_stock="1.125")
    initialize_product(count_id, "15", "11.333333", "2026-10-01", "测试", "quantity-list-count")
    initialize_product(measure_id, "12.345", "2", "2026-10-01", "测试", "quantity-list-measure")

    class TableRows(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self.row, self.cell = [], None, None

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.row = []
            elif tag in ("th", "td") and self.row is not None:
                self.cell = []

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

        def handle_endtag(self, tag):
            if tag in ("th", "td") and self.cell is not None:
                self.row.append("".join(self.cell).strip())
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.rows.append(self.row)
                self.row = None

    response = app.test_client().get("/products/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parser = TableRows()
    parser.feed(html)
    header = next(row for row in parser.rows if "名称" in row)
    assert header == ["", "名称", "规格", "单位", "默认价", "当前库存", "安全库存", "移动平均成本", "库存金额", "启用状态", "库存状况", "操作"]
    count_row = next(row for row in parser.rows if "计件展示商品" in row)
    measure_row = next(row for row in parser.rows if "计量展示商品" in row)
    assert count_row[5:7] == ["15", "3"]
    assert measure_row[5:7] == ["12.345", "1.125"]
    assert count_row[9:11] == measure_row[9:11] == ["已启用", "正常"]
    assert 'class="table-responsive product-table-shell"' in html
    assert 'class="selection-cell"' in html
    assert 'class="numeric-cell"' in html


@pytest.mark.parametrize("unit,sale_quantity,expected", [("个", "2", "2"), ("米", "1.250", "1.25"), ("个", "1.001", "1.001")])
def test_analytics_displays_unit_aware_quantity_in_summary_heatmap_ranking_and_product_rows(unit, sale_quantity, expected):
    import re

    from erp import create_app
    from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
    from erp.services.inventory import initialize_product

    app = create_app()
    product_id = create_product("分析数量商品", "Q1", unit, 2000)
    initialize_product(product_id, "10", "3", "2026-10-01", "测试", "quantity-analytics-init")
    customer_id = create_customer("数量分析往来")
    create_order_from_typed_rows(customer_id, "MD202610010901", [{"product_id": product_id, "product_name": "分析数量商品", "spec": "Q1", "unit": unit, "unit_price_yuan": "20", "quantity": sale_quantity}], status="saved", order_date="2026-10-01")
    response = app.test_client().get("/analytics/", query_string={"start_date": "2026-10-01", "end_date": "2026-10-01", "product_name": "分析数量商品", "metric": "quantity"})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # 日历格子只显示日期数字；数量保留在悬停/无障碍标签里（不再占格子）
    assert re.search(r'aria-label="2026-10-01 · 有交易 · 净销售数量 ' + re.escape(expected) + r'"', html)
    assert '<span class="heatmap-value">' not in html
    assert f'<span>净销售数量</span><strong>{expected}</strong>' in html
    # 排行改为竖向柱状图：数值标签带单位（全品类同一张榜），商品名在柱下
    assert f'<span class="rank-bar-value" style="bottom:' in html
    assert f'>{expected} {unit}</span>' in html
    assert '<div class="rank-columns" role="list">' in html
    # 柱高按同榜最大值等比（px），标注紧贴各自柱顶：唯一一项 = 最高柱 190px。
    assert '<span class="rank-bar-fill" style="height:190.0px"></span>' in html
    assert '<span class="rank-bar-value" style="bottom:190.0px">' in html
    assert '分析数量商品 · Q1' in html
    assert f'<td class="numeric-cell">{expected} {unit}</td>' in html
