import json
import re
from uuid import uuid4

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _payload(html: str) -> dict:
    match = re.search(r'<script id="customerPurchaseData" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match.group(1))


def test_product_ranking_is_sorted_by_quantity_then_amount():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"排行客户{suffix}"
    customer_id = create_customer(customer_name)
    create_order_from_typed_rows(
        customer_id,
        f"RANK-{suffix}",
        [
            {"product_name": "数量更多", "unit": "个", "unit_price_yuan": "1", "quantity": "10"},
            {"product_name": "金额更高", "unit": "个", "unit_price_yuan": "100", "quantity": "2"},
        ],
        status="saved",
        order_date="2026-07-03",
    )

    html = create_app().test_client().get("/orders/", query_string={"customer": customer_name}).get_data(as_text=True)
    ranking = _payload(html)["ranking"]
    assert [row["name"] for row in ranking[:2]] == ["数量更多", "金额更高"]
    assert "商品名称" in html and "累计数量" in html and "累计金额" in html
    assert 'id="productRankingTableBody"' in html


def test_year_and_month_range_modes_resolve_bounds_including_cross_year():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"跨度客户{suffix}"
    customer_id = create_customer(customer_name)
    create_order_from_typed_rows(
        customer_id,
        f"SPAN-{suffix}",
        [{"product_name": "跨度商品", "unit": "个", "unit_price_yuan": "8", "quantity": "1"}],
        status="saved",
        order_date="2026-07-04",
    )
    client = create_app().test_client()

    year_html = client.get(
        "/orders/", query_string={"customer": customer_name, "date_mode": "year", "year": "2026"}
    ).get_data(as_text=True)
    month_html = client.get(
        "/orders/",
        query_string={
            "customer": customer_name,
            "date_mode": "month",
            "start_year": "2025",
            "start_month": "11",
            "end_year": "2026",
            "end_month": "2",
        },
    ).get_data(as_text=True)

    year_payload = _payload(year_html)
    month_payload = _payload(month_html)
    assert year_payload["start_date"] == "2026-01-01"
    assert year_payload["end_date"] == "2026-12-31"
    assert len(year_payload["days"]) == 365
    assert month_payload["start_date"] == "2025-11-01"
    assert month_payload["end_date"] == "2026-02-28"
    assert "开始年" in month_html and "结束月" in month_html
    assert "单据管理" in year_html
    assert "导出汇总表" in year_html


def test_invalid_dashboard_dates_do_not_return_500():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"日期客户{suffix}"
    customer_id = create_customer(customer_name)
    create_order_from_typed_rows(
        customer_id,
        f"DATE-VALID-{suffix}",
        [{"product_name": "日期商品", "unit": "个", "unit_price_yuan": "8", "quantity": "1"}],
        status="saved",
        order_date="2026-07-04",
    )

    response = create_app().test_client().get(
        "/orders/",
        query_string={
            "customer": customer_name,
            "date_mode": "range",
            "start_date": "abc",
            "end_date": "not-a-date",
        },
    )
    assert response.status_code == 200
    assert "2026-07-04" in response.get_data(as_text=True)
