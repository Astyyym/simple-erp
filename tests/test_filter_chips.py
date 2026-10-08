"""筛选卡「已选条件」chip 的真实行为。

两页共用同一套 chip 语义：
- 时间范围不产生 chip（它是常驻组合控件）。
- 「不勾选 = 全部」与「全选 = 全部」等价 → 两种情况都不产生 chip。
- 每个 chip 的链接 = 当前 URL 去掉该条件（并去掉 page）。
"""
import uuid

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _client():
    init_db()
    return create_app().test_client()


def _orders_html(**query):
    return _client().get("/orders/", query_string=query).get_data(as_text=True)


def test_orders_chips_appear_only_for_real_narrowing_conditions():
    html = _orders_html(start_date="2026-07-01", end_date="2026-07-31")
    # 只有时间（常驻）时不出 chip 行。
    assert 'class="filter-chips"' not in html

    # 客户 + 单一类型 → 两个 chip。
    html = _orders_html(customer="甲", order_type="sale", start_date="2026-07-01", end_date="2026-07-31")
    assert 'class="filter-chips"' in html
    assert "客户：甲" in html
    assert "类型：销售单" in html
    # 类型 chip 的移除链接去掉 order_type，但保留 customer / 日期。
    assert "customer=%E7%94%B2" in html or "customer=甲" in html
    assert "order_type=sale" in html  # 复选控件仍回显
    assert "&amp;order_type=sale" not in html.split('filter-chip')[1].split('</a>')[0]


def test_orders_chips_treat_all_types_or_all_statuses_as_no_filter():
    # 四类全勾 = 全部，不产生类型 chip。
    html = _orders_html(order_type=["sale", "return", "purchase", "purchase_return"])
    assert 'class="filter-chips"' not in html

    # 单一状态才产生状态 chip。
    html = _orders_html(status="draft")
    assert "状态：草稿" in html

    # 两态也产生 chip（部分选择）。
    html = _orders_html(status=["draft", "saved"])
    assert "状态：草稿、正式保存" in html


def test_analytics_chips_cover_party_product_model_and_type():
    suffix = uuid.uuid4().hex[:8]
    init_db()
    customer_id = create_customer(f"分析chip客户{suffix}")
    create_order_from_typed_rows(
        customer_id, f"chip-{suffix}",
        [{"product_name": f"片商品{suffix}", "spec": "Q1", "unit": "个",
          "quantity": "1", "unit_price_yuan": "10"}],
        status="saved", order_date="2026-07-10",
    )
    client = create_app().test_client()
    html = client.get("/analytics/", query_string={
        "customer_id": customer_id, "product_name": f"片商品{suffix}", "spec": "Q1",
        "start_date": "", "end_date": "",
    }).get_data(as_text=True)
    assert 'class="filter-chips"' in html
    assert f"客户：分析chip客户{suffix}" in html
    assert f"商品：片商品{suffix}" in html
    assert "型号：Q1" in html

    # 商品 chip 的移除链接同时清掉 spec（型号从属于商品）。
    product_chip = next(chip for chip in html.split('class="filter-chip"') if f"片商品{suffix}" in chip)
    assert "spec=" not in product_chip.split("</a>")[0]


def test_analytics_no_chips_when_only_time_range_is_set():
    html = _client().get("/analytics/", query_string={"start_date": "", "end_date": ""}).get_data(as_text=True)
    assert 'class="filter-chips"' not in html
    # 四类全勾 = 全部。
    html = _client().get("/analytics/", query_string={
        "document_type": ["sale", "return", "purchase", "purchase_return"],
        "start_date": "", "end_date": "",
    }).get_data(as_text=True)
    assert 'class="filter-chips"' not in html
