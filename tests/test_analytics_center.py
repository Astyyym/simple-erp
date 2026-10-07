from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product, void_order
from erp.services.inventory import initialize_product


def _seed_analytics_orders():
    init_db()
    product_a = create_product("灭火器", "4kg", "个", 3000, safety_stock="5")
    product_b = create_product("灭火器", "8kg", "个", 5000, safety_stock="2")
    other_product = create_product("消防水带", "20m", "卷", 8000, safety_stock="1")
    initialize_product(product_a, "20", "10", "2026-10-01", "期初", "analytics-init-a")
    initialize_product(product_b, "10", "12", "2026-10-01", "期初", "analytics-init-b")
    initialize_product(other_product, "1", "50", "2026-10-01", "期初", "analytics-init-other")
    customer_id = create_customer("分析客户")
    # These virtual master records existed before the historical reporting end.
    with get_db() as conn:
        conn.execute("UPDATE products SET created_at='2026-10-01 00:00:00'")
    sale_a = create_order_from_typed_rows(
        customer_id,
        "MD202610010101",
        [{"product_id": product_a, "product_name": "灭火器", "spec": "4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
    )
    create_order_from_typed_rows(
        customer_id,
        "MD202610020101",
        [{"product_id": product_b, "product_name": "灭火器", "spec": "8kg", "unit": "个", "unit_price_yuan": "30", "quantity": "3"}],
        status="saved",
        order_date="2026-10-02",
    )
    with get_db() as conn:
        sale_item_id = conn.execute("SELECT id FROM order_items WHERE order_id=?", (sale_a,)).fetchone()["id"]
    from erp.services.accounting import create_return_order_from_source

    create_return_order_from_source(
        customer_id,
        "MD202610030101",
        sale_a,
        [{"source_item_id": sale_item_id, "quantity": "1"}],
        status="saved",
        order_date="2026-10-02",
    )
    void_id = create_order_from_typed_rows(
        customer_id,
        "MD202610040101",
        [{"product_id": other_product, "product_name": "消防水带", "spec": "20m", "unit": "卷", "unit_price_yuan": "80", "quantity": "1"}],
        status="saved",
        order_date="2026-10-02",
    )
    void_order(void_id, "分析测试排除作废单")
    return customer_id, product_a, product_b, other_product


def test_analytics_summary_uses_shared_filters_and_net_sales_heatmap():
    from erp.services.analytics import summarize_analytics
    from erp.services.inventory import create_purchase_order

    customer_id, product_a, product_b, _ = _seed_analytics_orders()
    create_purchase_order(
        "NH202610020101",
        "2026-10-02",
        [{"product_id": product_a, "quantity": "2", "unit_cost_yuan": "15"}],
        request_key="analytics-purchase-not-sales-001",
        customer_id=customer_id,
    )
    result = summarize_analytics(
        start_date="2026-10-01",
        end_date="2026-10-02",
        customer_id=customer_id,
        product_name="灭火器",
        metric="amount",
    )

    assert result["filters"]["product_name"] == "灭火器"
    assert result["summary"]["sales_amount_cents"] == 13000
    assert result["summary"]["returns_amount_cents"] == 2000
    assert result["summary"]["net_sales_amount_cents"] == 11000
    assert result["summary"]["net_cost_cents"] == 4600
    assert result["summary"]["gross_profit_cents"] == 6400
    assert result["summary"]["cost_complete"] is True
    assert [cell["date"] for cell in result["heatmap"]] == ["2026-10-01", "2026-10-02"]
    assert [cell["net_amount_cents"] for cell in result["heatmap"]] == [4000, 7000]
    assert {row["product_id"] for row in result["product_rows"]} == {product_a, product_b}
    assert result["product_rows"][0]["product_name"] == "灭火器"


def test_analytics_product_name_includes_models_and_spec_filter_narrows_one_product():
    from erp.services.analytics import summarize_analytics

    customer_id, product_a, product_b, _ = _seed_analytics_orders()
    all_models = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id, product_name="灭火器")
    one_model = summarize_analytics(
        start_date="2026-10-01",
        end_date="2026-10-02",
        customer_id=customer_id,
        product_name="灭火器",
        spec="4kg",
    )

    assert {row["product_id"] for row in all_models["product_rows"]} == {product_a, product_b}
    assert [row["product_id"] for row in one_model["product_rows"]] == [product_a]
    assert one_model["summary"]["net_sales_quantity_3dp"] == 1000
    assert one_model["summary"]["net_sales_amount_cents"] == 2000


def test_analytics_separates_current_inventory_status_and_rejects_invalid_range():
    from erp.services.analytics import summarize_analytics

    customer_id, product_a, _, _ = _seed_analytics_orders()
    with get_db() as conn:
        conn.execute("UPDATE product_inventory_state SET quantity_3dp=0 WHERE product_id=?", (product_a,))
    result = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id, product_name="灭火器")
    statuses = {row["product_id"]: row["inventory_status"] for row in result["product_rows"]}
    assert statuses[product_a] == "缺货"
    assert statuses[next(product_id for product_id in statuses if product_id != product_a)] == "正常"
    assert result["summary"]["inventory_is_current"] is True

    import pytest

    with pytest.raises(ValueError, match="起始日期不能晚于结束日期"):
        summarize_analytics(start_date="2026-10-04", end_date="2026-10-01")


def test_analytics_page_exposes_filters_heatmap_profit_and_inventory_sections():
    customer_id, _, _, _ = _seed_analytics_orders()
    response = create_app().test_client().get(
        "/analytics/",
        query_string={"start_date": "2026-10-01", "end_date": "2026-10-02", "customer_id": customer_id, "product_name": "灭火器"},
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "数据分析中心" in html
    assert 'id="analyticsFilters"' in html
    assert "净销售数量" in html and "流水净额" in html
    assert "流水热力图" in html and "利润汇总" in html
    assert "当前库存状态" in html and "库存告急" in html
    assert "2026-10-02" in html


def test_analytics_inventory_alert_ranking_has_workbench_anchor():
    _seed_analytics_orders()
    html = create_app().test_client().get("/analytics/").get_data(as_text=True)

    assert 'id="inventoryAlert"' in html
    assert "库存告急" in html


def test_analytics_returns_hot_low_unsold_profit_and_inventory_alert_rankings():
    from erp.services.analytics import summarize_analytics

    customer_id, product_a, product_b, other_product = _seed_analytics_orders()
    result = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id)

    assert result["rankings"]["hot_sales"][0]["product_id"] == product_b
    assert result["rankings"]["low_sales"][0]["product_id"] == product_a
    assert [row["product_id"] for row in result["rankings"]["unsold"]] == [other_product]
    assert result["rankings"]["gross_profit"][0]["product_id"] == product_b
    assert all(row["inventory_status"] in {"正常", "库存告急", "缺货", "未启用"} for row in result["rankings"]["inventory_alert"])


def test_analytics_cross_year_range_marks_mixed_units_as_not_comparable():
    from erp.services.analytics import summarize_analytics

    init_db()
    extinguisher = create_product("跨年灭火器", "4kg", "个", 2000)
    hose = create_product("跨年水带", "20m", "卷", 5000)
    initialize_product(extinguisher, "10", "8", "2025-12-31", "期初", "cross-year-init-a")
    initialize_product(hose, "10", "20", "2025-12-31", "期初", "cross-year-init-b")
    customer_id = create_customer("跨年分析客户")
    create_order_from_typed_rows(customer_id, "MD202512310001", [{"product_id": extinguisher, "product_name": "跨年灭火器", "spec": "4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}], status="saved", order_date="2025-12-31")
    create_order_from_typed_rows(customer_id, "MD202601010001", [{"product_id": hose, "product_name": "跨年水带", "spec": "20m", "unit": "卷", "unit_price_yuan": "50", "quantity": "1"}], status="saved", order_date="2026-01-01")

    result = summarize_analytics(start_date="2025-12-31", end_date="2026-01-01", customer_id=customer_id, metric="quantity")

    assert [cell["date"] for cell in result["heatmap"]] == ["2025-12-31", "2026-01-01"]
    assert result["summary"]["quantity_comparable"] is False
    assert result["summary"]["net_sales_quantity_3dp"] is None
    assert all(cell["net_quantity_3dp"] is None for cell in result["heatmap"])
    page = create_app().test_client().get(
        "/analytics/",
        query_string={"start_date": "2025-12-31", "end_date": "2026-01-01", "customer_id": customer_id},
    )
    assert page.status_code == 200 and "数量不可合计" in page.get_data(as_text=True)


def test_analytics_zero_denominator_and_unknown_cost_are_explicit():
    from erp.services.accounting import create_return_order_from_source
    from erp.services.analytics import summarize_analytics

    customer_id, product_a, _, _ = _seed_analytics_orders()
    with get_db() as conn:
        source_sale = conn.execute("SELECT id FROM orders WHERE order_no='MD202610010101'").fetchone()["id"]
        source_item = conn.execute("SELECT id FROM order_items WHERE order_id=?", (source_sale,)).fetchone()["id"]
    create_return_order_from_source(customer_id, "MD202610020102", source_sale, [{"source_item_id": source_item, "quantity": "1"}], status="saved", order_date="2026-10-02")
    zero = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id, product_name="灭火器", spec="4kg")
    assert zero["summary"]["net_sales_amount_cents"] == 0
    assert zero["summary"]["gross_margin_rate"] is None

    unknown_product = create_product("未知成本商品", "U1", "个", 1000)
    create_order_from_typed_rows(customer_id, "MD202610020103", [{"product_id": unknown_product, "product_name": "未知成本商品", "spec": "U1", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}], status="saved", order_date="2026-10-02")
    unknown = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id, product_name="未知成本商品")
    assert unknown["summary"]["net_sales_amount_cents"] == 1000
    assert unknown["summary"]["cost_complete"] is False
    assert unknown["summary"]["unknown_line_count"] == 1
    assert unknown["summary"]["gross_profit_cents"] is None
    assert unknown["summary"]["gross_margin_rate"] is None


def test_analytics_rejects_model_filter_without_product_name_and_keeps_empty_range_stable():
    from erp.services.analytics import summarize_analytics
    import pytest

    with pytest.raises(ValueError, match="必须先选择商品名称"):
        summarize_analytics(start_date="2026-01-01", end_date="2026-01-02", spec="4kg")
    init_db()
    result = summarize_analytics(start_date="2026-01-01", end_date="2026-01-03", product_name="不存在的商品")
    assert result["summary"]["net_sales_amount_cents"] == 0
    assert [cell["date"] for cell in result["heatmap"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_analytics_top_filter_supports_year_month_range_and_document_types():
    """顶部筛选扩展：按年 / 按月到月 / 按日到日 + 订单类型多选，下方数据跟随。"""
    from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
    from erp.services.inventory import create_purchase_order, initialize_product

    init_db()
    customer = create_customer("P7筛选对象")
    product = create_product("P7商品", "S1", "个", 2000)
    initialize_product(product, "10", "10", "2026-01-01", "期初", "p7-init")
    create_order_from_typed_rows(
        customer, "MD202602100001",
        [{"product_id": product, "product_name": "P7商品", "spec": "S1", "unit": "个",
          "quantity": "2", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-02-10",
    )
    create_purchase_order(
        "NH202602100001", "2026-02-10",
        [{"product_id": product, "quantity": "5", "unit_cost_yuan": "10"}],
        customer_id=customer, request_key="p7-buy",
    )
    client = create_app().test_client()

    # 按年：2 月那笔销售进入范围
    html = client.get("/analytics/", query_string={"date_mode": "year", "year": "2026"}).get_data(as_text=True)
    assert 'id="analyticsDateModeyear"' in html and 'id="analyticsDateModeyear" value="year" checked' in html
    assert 'name="document_type"' in html
    assert 'id="analyticsDocTypesale"' in html and 'id="analyticsDocTypepurchase_return"' in html
    assert "全选" in html and "清空" in html

    # 按月到月：2 月到 3 月
    html = client.get("/analytics/", query_string={
        "date_mode": "month", "start_month_raw": "2026-02", "end_month_raw": "2026-03",
    }).get_data(as_text=True)
    assert "¥60.00" in html  # 2 个 × 30

    # 按月到月但范围不含该月 → 无销售
    html = client.get("/analytics/", query_string={
        "date_mode": "month", "start_month_raw": "2026-05", "end_month_raw": "2026-06",
    }).get_data(as_text=True)
    assert "¥0.00" in html


def test_document_type_filter_only_affects_flow_not_profit_metrics():
    """订单类型筛选：利润类只算销售+退货；拿货/退拿货只影响流水与单据流水卡片。"""
    from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
    from erp.services.inventory import create_purchase_order, initialize_product
    from erp.services.analytics import summarize_analytics

    init_db()
    customer = create_customer("P7口径对象")
    product = create_product("P7口径商品", "S1", "个", 2000)
    initialize_product(product, "10", "10", "2026-03-01", "期初", "p7-scope-init")
    create_order_from_typed_rows(
        customer, "MD202603100001",
        [{"product_id": product, "product_name": "P7口径商品", "spec": "S1", "unit": "个",
          "quantity": "2", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-03-10",
    )
    create_purchase_order(
        "NH202603100001", "2026-03-10",
        [{"product_id": product, "quantity": "5", "unit_cost_yuan": "10"}],
        customer_id=customer, request_key="p7-scope-buy",
    )
    window = {"start_date": "2026-03-01", "end_date": "2026-03-31", "customer_id": customer}

    # 只勾销售单：流水净额 = 销售 60
    sale_only = summarize_analytics(**window, document_types=["sale"])
    assert sale_only["summary"]["net_sales_amount_cents"] == 6000
    assert sale_only["flow_totals"]["net_flow_cents"] == 6000
    assert sale_only["flow_totals"]["purchase_count"] == 0
    assert sale_only["flow_totals"]["purchase_amount_cents"] == 0

    # 只勾拿货单：利润类仍按销售+退货口径（不受订单类型影响），流水只剩拿货
    purchase_only = summarize_analytics(**window, document_types=["purchase"])
    assert purchase_only["summary"]["net_sales_amount_cents"] == 6000
    assert purchase_only["flow_totals"]["net_flow_cents"] == -5000
    assert purchase_only["flow_totals"]["purchase_count"] == 1
    assert purchase_only["flow_totals"]["purchase_amount_cents"] == 5000

    # 四类全勾：销售 60 − 拿货 50 = 10
    both = summarize_analytics(**window, document_types=["sale", "purchase"])
    assert both["flow_totals"]["net_flow_cents"] == 1000

    # 不传 = 四类全部（与显式全选一致）
    everything = summarize_analytics(**window)
    assert everything["flow_totals"]["net_flow_cents"] == 1000
    assert everything["filters"]["document_types"] == []

    # 非法订单类型被拒绝
    import pytest
    with pytest.raises(ValueError, match="订单类型"):
        summarize_analytics(**window, document_types=["fake"])


def test_analytics_page_renders_document_flow_card():
    """单据流水卡片显示拿货/退拿货金额与流水净额，并声明不参与利润口径。"""
    from erp.services.accounting import create_customer, create_product
    from erp.services.inventory import create_purchase_order, initialize_product

    init_db()
    customer = create_customer("P7流水卡片对象")
    product = create_product("P7流水卡片商品", "S1", "个", 2000)
    initialize_product(product, "10", "10", "2026-04-01", "期初", "p7-flow-init")
    create_purchase_order(
        "NH202604010001", "2026-04-01",
        [{"product_id": product, "quantity": "3", "unit_cost_yuan": "12"}],
        customer_id=customer, request_key="p7-flow-buy",
    )
    html = create_app().test_client().get(
        "/analytics/", query_string={"start_date": "2026-04-01", "end_date": "2026-04-30"}
    ).get_data(as_text=True)
    assert 'id="documentFlow"' in html
    assert "拿货金额" in html and "退拿货金额" in html and "流水净额" in html
    assert "1 张拿货单" in html
    assert "¥36.00" in html  # 3 × 12
    assert "不参与净销售额与毛利润" in html
