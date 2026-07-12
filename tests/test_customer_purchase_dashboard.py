import json
import re
from uuid import uuid4

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _dashboard_payload(html: str) -> dict:
    match = re.search(
        r'<script id="customerPurchaseData" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert match, "客户统计 JSON 未渲染"
    return json.loads(match.group(1))


def test_exact_customer_filter_renders_daily_purchase_heatmap_and_excludes_returns():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"热力客户{suffix}"
    customer_id = create_customer(customer_name)
    create_order_from_typed_rows(
        customer_id,
        f"SALE-1-{suffix}",
        [
            {"product_name": "消防水带", "unit": "卷", "unit_price_yuan": "100", "quantity": "2"},
            {"product_name": "消防栓", "unit": "个", "unit_price_yuan": "50", "quantity": "1"},
        ],
        status="saved",
        order_date="2026-07-01",
    )
    create_order_from_typed_rows(
        customer_id,
        f"SALE-2-{suffix}",
        [{"product_name": "消防水带", "unit": "卷", "unit_price_yuan": "100", "quantity": "1"}],
        status="saved",
        order_date="2026-07-01",
    )
    create_order_from_typed_rows(
        customer_id,
        f"RETURN-{suffix}",
        [{"product_name": "消防水带", "unit": "卷", "unit_price_yuan": "100", "quantity": "9"}],
        status="saved",
        order_date="2026-07-02",
        order_type="return",
    )
    create_order_from_typed_rows(
        customer_id,
        f"DRAFT-{suffix}",
        [{"product_name": "草稿商品", "unit": "个", "unit_price_yuan": "999", "quantity": "8"}],
        status="draft",
        order_date="2026-07-02",
    )
    void_id = create_order_from_typed_rows(
        customer_id,
        f"VOID-{suffix}",
        [{"product_name": "作废商品", "unit": "个", "unit_price_yuan": "888", "quantity": "7"}],
        status="saved",
        order_date="2026-07-02",
    )
    with get_db() as conn:
        conn.execute("UPDATE orders SET status='void' WHERE id=?", (void_id,))

    html = create_app().test_client().get(
        "/orders/",
        query_string={
            "customer": customer_name,
            "date_mode": "range",
            "start_date": "2026-07-01",
            "end_date": "2026-07-02",
        },
    ).get_data(as_text=True)
    payload = _dashboard_payload(html)

    assert "客户采购统计" in html
    assert payload["scope"] == "customer"
    assert payload["customer"] == customer_name
    assert payload["start_date"] == "2026-07-01"
    assert payload["end_date"] == "2026-07-02"
    assert payload["days"] == [
        {
            "date": "2026-07-01",
            "amount_cents": 35000,
            "order_count": 2,
            "products": ["消防水带", "消防栓"],
        },
        {"date": "2026-07-02", "amount_cents": 0, "order_count": 0, "products": []},
    ]
    assert payload["ranking"][0] == {"name": "消防水带", "quantity": 3.0, "amount_cents": 30000}
    assert payload["ranking"][1] == {"name": "消防栓", "quantity": 1.0, "amount_cents": 5000}


def test_fuzzy_unique_customer_shows_dashboard_but_multiple_matches_only_list_with_notice():
    init_db()
    suffix = uuid4().hex[:8]
    unique_prefix = f"唯模糊{suffix}"
    multi_prefix = f"多模糊{suffix}"
    unique_name = f"{unique_prefix}总店"
    multi_a = f"{multi_prefix}甲"
    multi_b = f"{multi_prefix}乙"
    unique_id = create_customer(unique_name)
    create_customer(multi_a)
    create_customer(multi_b)
    create_order_from_typed_rows(
        unique_id,
        f"FUZZY-{suffix}",
        [{"product_name": "灭火器", "unit": "具", "unit_price_yuan": "80", "quantity": "1"}],
        status="saved",
        order_date="2026-06-09",
    )
    client = create_app().test_client()

    unique_html = client.get("/orders/", query_string={"customer": unique_prefix}).get_data(as_text=True)
    multi_html = client.get("/orders/", query_string={"customer": multi_prefix}).get_data(as_text=True)

    payload = _dashboard_payload(unique_html)
    assert payload["customer"] == unique_name
    assert payload["scope"] == "customer"
    assert 'id="customerPurchaseData"' not in multi_html
    assert "匹配到多个客户" in multi_html


def test_empty_customer_shows_storewide_dashboard_by_quantity():
    init_db()
    suffix = uuid4().hex[:8]
    a_id = create_customer(f"全店甲{suffix}")
    b_id = create_customer(f"全店乙{suffix}")
    create_order_from_typed_rows(
        a_id,
        f"ALL-A-{suffix}",
        [{"product_name": "数量更多", "unit": "个", "unit_price_yuan": "1", "quantity": "10"}],
        status="saved",
        order_date="2026-07-03",
    )
    create_order_from_typed_rows(
        b_id,
        f"ALL-B-{suffix}",
        [{"product_name": "金额更高", "unit": "个", "unit_price_yuan": "100", "quantity": "2"}],
        status="saved",
        order_date="2026-07-03",
    )
    create_order_from_typed_rows(
        b_id,
        f"ALL-R-{suffix}",
        [{"product_name": "退货不进", "unit": "个", "unit_price_yuan": "50", "quantity": "9"}],
        status="saved",
        order_date="2026-07-03",
        order_type="return",
    )

    html = create_app().test_client().get(
        "/orders/",
        query_string={"date_mode": "range", "start_date": "2026-07-03", "end_date": "2026-07-03"},
    ).get_data(as_text=True)
    payload = _dashboard_payload(html)

    assert payload["scope"] == "all"
    assert payload["customer"] in ("", None)
    assert "全店拿货统计" in html
    assert payload["ranking"][0]["name"] == "数量更多"
    assert payload["ranking"][0]["quantity"] == 10.0
    assert all(row["name"] != "退货不进" for row in payload["ranking"])


def test_dashboard_safely_converts_text_quantity_and_exposes_offline_interactions():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"文本数量客户{suffix}"
    customer_id = create_customer(customer_name)
    order_id = create_order_from_typed_rows(
        customer_id,
        f"TEXT-{suffix}",
        [{"product_name": "安全商品", "unit": "个", "unit_price_yuan": "10", "quantity": "2"}],
        status="saved",
        order_date="2026-05-01",
    )
    with get_db() as conn:
        conn.execute("UPDATE order_items SET quantity=? WHERE order_id=?", ("不是数字", order_id))

    html = create_app().test_client().get("/orders/", query_string={"customer": customer_name}).get_data(as_text=True)
    payload = _dashboard_payload(html)

    assert payload["ranking"][0]["quantity"] == 0.0
    assert "采购热力图" in html
    assert "前5" in html and "前10" in html
    assert "查看订单详情" in html and "取消选择" in html
    assert "mousedown" in html and "mouseenter" in html
    assert "URLSearchParams" in html
    assert "canvas" in html.lower()
    assert "chart.js" not in html.lower()
    assert "echarts" not in html.lower()
    assert "另有" in html
    assert 'name="date_mode"' in html
    assert "按年" in html and "按月" in html and "按日到日" in html
    assert "purchase-month-grid" in html or "month-calendar" in html
