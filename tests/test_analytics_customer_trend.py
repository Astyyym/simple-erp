"""3-A 分析页：客户排行 + 月度趋势（仅数量金额，不含利润/成本）。"""
from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
    create_return_order_from_source,
)
from erp.services.analytics import summarize_analytics


def _seed():
    init_db()
    product_id = create_product("趋势测试商品", "个")
    customer_a = create_customer("趋势客户甲")
    customer_b = create_customer("趋势客户乙")

    sale_a = create_order_from_typed_rows(
        customer_a,
        "MD202609050001",
        [{"product_id": product_id, "product_name": "趋势测试商品", "unit": "个", "unit_price_yuan": "10.00", "quantity": "2"}],
        status="saved",
        order_date="2026-09-05",
    )
    create_order_from_typed_rows(
        customer_a,
        "MD202610050001",
        [{"product_id": product_id, "product_name": "趋势测试商品", "unit": "个", "unit_price_yuan": "10.00", "quantity": "3"}],
        status="saved",
        order_date="2026-10-05",
    )
    with get_db() as conn:
        sale_a_item = conn.execute("SELECT id FROM order_items WHERE order_id=?", (sale_a,)).fetchone()["id"]
    create_return_order_from_source(
        customer_a,
        "MD202610060001",
        sale_a,
        [{"source_item_id": sale_a_item, "quantity": "1"}],
        status="saved",
        order_date="2026-10-06",
    )
    create_order_from_typed_rows(
        customer_b,
        "MD202610100001",
        [{"product_id": product_id, "product_name": "趋势测试商品", "unit": "个", "unit_price_yuan": "20.00", "quantity": "1"}],
        status="saved",
        order_date="2026-10-10",
    )


def test_customer_ranking_and_monthly_trend_numbers():
    _seed()
    result = summarize_analytics(start_date="2026-09-01", end_date="2026-10-31")

    customers = {row["customer_name"]: row for row in result["customer_rows"]}
    assert customers["趋势客户甲"]["net_sales_amount_cents"] == 4000  # 20+30-10 元
    assert customers["趋势客户甲"]["net_sales_quantity_3dp"] == 4000  # 2+3-1 个
    assert customers["趋势客户乙"]["net_sales_amount_cents"] == 2000
    # 按净销售额降序
    assert result["customer_rows"][0]["customer_name"] == "趋势客户甲"

    months = {row["month"]: row for row in result["monthly_trend"]}
    assert months["2026-09"]["net_sales_amount_cents"] == 2000
    assert months["2026-10"]["net_sales_amount_cents"] == 4000  # 30 - 10 + 20
    assert [row["month"] for row in result["monthly_trend"]] == ["2026-09", "2026-10"]


def test_no_profit_or_cost_fields_in_dimension_rows():
    _seed()
    result = summarize_analytics(start_date="2026-09-01", end_date="2026-10-31")
    for row in result["customer_rows"] + result["monthly_trend"]:
        for key in row:
            assert "cost" not in key.lower(), key
            assert "profit" not in key.lower(), key
            assert "margin" not in key.lower(), key


def test_customer_ranking_respects_customer_filter():
    _seed()
    with get_db() as conn:
        cid = conn.execute("SELECT id FROM customers WHERE name='趋势客户乙'").fetchone()["id"]
    result = summarize_analytics(start_date="2026-09-01", end_date="2026-10-31", customer_id=cid)
    assert [row["customer_name"] for row in result["customer_rows"]] == ["趋势客户乙"]


def test_analytics_page_renders_customer_trend_section():
    _seed()
    client = create_app().test_client()
    page = client.get("/analytics/?start_date=2026-09-01&end_date=2026-10-31")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "客户排行与月度趋势" in html
    assert "客户排行（按净销售额）" in html
    assert "月度趋势（按净销售额）" in html
    assert "趋势客户甲" in html
    assert "2026-09" in html and "2026-10" in html
    # 明确不做利润/成本口径（页面可以出现「不含利润/成本」这句说明，但不得有利润字段/榜单）
    assert "毛利" not in html
    assert "利润榜" not in html
