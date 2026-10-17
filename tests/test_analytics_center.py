from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product, void_order


def _seed_analytics_orders():
    init_db()
    product_a = create_product("灭火器4kg", "个")
    product_b = create_product("灭火器8kg", "个")
    other_product = create_product("消防水带20m", "卷")


    customer_id = create_customer("分析客户")
    # These virtual master records existed before the historical reporting end.
    with get_db() as conn:
        conn.execute("UPDATE products SET created_at='2026-10-01 00:00:00'")
    sale_a = create_order_from_typed_rows(
        customer_id,
        "MD202610010101",
        [{"product_id": product_a, "product_name": "灭火器4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
    )
    create_order_from_typed_rows(
        customer_id,
        "MD202610020101",
        [{"product_id": product_b, "product_name": "灭火器8kg", "unit": "个", "unit_price_yuan": "30", "quantity": "3"}],
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
        [{"product_id": other_product, "product_name": "消防水带20m", "unit": "卷", "unit_price_yuan": "80", "quantity": "1"}],
        status="saved",
        order_date="2026-10-02",
    )
    void_order(void_id, "分析测试排除作废单")
    return customer_id, product_a, product_b, other_product


def test_analytics_summary_uses_shared_filters_and_net_sales_heatmap():
    from erp.services.analytics import summarize_analytics
    from erp.services.purchases import create_purchase_order

    customer_id, product_a, product_b, other_product = _seed_analytics_orders()
    create_purchase_order(
        "NH202610020101",
        "2026-10-02",
        [{"product_id": product_a, "quantity": "2", "unit_cost_yuan": "15"}],
        request_key="analytics-purchase-not-sales-001",
        customer_id=customer_id,
    )
    # 决策变更（v14）：商品身份合并为单一名称，型号不再是筛选维度。
    # 旧用例以基名 '灭火器' 一次命中 4kg / 8kg 两个商品；合并后名称即完整身份且精确匹配，
    # 基名匹配不到任何商品，故改为只按客户这一共享筛选口径取值，仍覆盖两个灭火器商品的合计。
    result = summarize_analytics(
        start_date="2026-10-01",
        end_date="2026-10-02",
        customer_id=customer_id,
        product_name="",
        metric="amount",
    )

    assert result["filters"]["product_name"] == ""
    assert result["summary"]["sales_amount_cents"] == 13000
    assert result["summary"]["returns_amount_cents"] == 2000
    assert result["summary"]["net_sales_amount_cents"] == 11000
    assert [cell["date"] for cell in result["heatmap"]] == ["2026-10-01", "2026-10-02"]
    assert [cell["net_amount_cents"] for cell in result["heatmap"]] == [4000, 7000]
    assert {row["product_id"] for row in result["product_rows"]} == {product_a, product_b, other_product}
    # 排行按流水净额降序：8kg (9000) 在 4kg (2000) 之前；名称已是合并后的完整身份。
    assert result["product_rows"][0]["product_name"] == "灭火器8kg"


def test_analytics_product_identity_is_single_name_so_merged_name_narrows_exactly():
    """决策变更（v14）：商品身份合并为单一名称（名称+型号直拼），型号不再是筛选维度。

    旧用例钉「product_name=灭火器 命中两个商品，再叠加 spec=4kg 收窄到一个」。
    (名称,型号) 合并为单一名称后，名称即完整身份、精确匹配单个商品，spec 参数被删除。
    """
    from erp.services.analytics import summarize_analytics
    import pytest

    customer_id, product_a, product_b, other_product = _seed_analytics_orders()
    all_products = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id)
    one_product = summarize_analytics(
        start_date="2026-10-01",
        end_date="2026-10-02",
        customer_id=customer_id,
        product_name="灭火器4kg",
    )

    # 合并后的两个灭火器商品是两条独立身份记录。
    assert {row["product_id"] for row in all_products["product_rows"]} == {product_a, product_b, other_product}
    # 完整名称精确命中单一商品。
    assert [row["product_id"] for row in one_product["product_rows"]] == [product_a]
    assert one_product["summary"]["net_sales_quantity_3dp"] == 1000
    assert one_product["summary"]["net_sales_amount_cents"] == 2000
    # 型号已从筛选契约删除：服务层不再接受 spec 关键字参数。
    with pytest.raises(TypeError):
        summarize_analytics(product_name="灭火器4kg", spec="4kg")


def test_analytics_page_exposes_filters_heatmap_flow_and_ranking_sections():
    customer_id, _, _, _ = _seed_analytics_orders()
    response = create_app().test_client().get(
        "/analytics/",
        query_string={"start_date": "2026-10-01", "end_date": "2026-10-02", "customer_id": customer_id,
                      # 商品筛选按**完整名称**精确匹配；2026-10-17 起半截名（如「灭火器」）会明确提示，
                      # 不再静默返回空结果（本用例只验证页面各分区都在，故传完整名）。
                      "product_name": "灭火器4kg"},
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "数据分析中心" in html
    assert 'id="analyticsFilters"' in html
    assert "净销售数量" in html and "流水净额" in html
    assert "流水热力图" in html and "产品排行" in html
    # 2026-10-17：「单据流水」段已在 2026-10-16 并入「金额汇总」（用户决策），
    # 旧断言靠筛选区提示里的「下方『单据流水』」这句话才通过 —— 那句话已按当前
    # 分区结构改成「金额汇总」。此处改断言为目标本身：合并后的六卡与承载它的容器。
    assert "金额汇总" in html
    assert 'id="documentFlow"' in html and 'class="analytics-amount-grid"' in html
    for card_label in ("销售金额", "退货金额", "拿货金额", "退拿货金额", "净销售额", "流水净额"):
        assert card_label in html, card_label
    # 决策 ②/③：毛利润/毛利率卡与成本诊断页签整块下线。
    assert "毛利润" not in html and "毛利率" not in html and "成本诊断" not in html
    assert "2026-10-02" in html


def test_analytics_returns_hot_low_and_unsold_rankings():
    from erp.services.analytics import summarize_analytics

    customer_id, product_a, product_b, other_product = _seed_analytics_orders()
    result = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id)

    assert result["rankings"]["hot_sales"][0]["product_id"] == product_b
    assert result["rankings"]["low_sales"][0]["product_id"] == product_a
    assert [row["product_id"] for row in result["rankings"]["unsold"]] == [other_product]


def test_analytics_cross_year_range_marks_mixed_units_as_not_comparable():
    from erp.services.analytics import summarize_analytics

    init_db()
    extinguisher = create_product("跨年灭火器4kg", "个")
    hose = create_product("跨年水带20m", "卷")


    customer_id = create_customer("跨年分析客户")
    create_order_from_typed_rows(customer_id, "MD202512310001", [{"product_id": extinguisher, "product_name": "跨年灭火器4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}], status="saved", order_date="2025-12-31")
    create_order_from_typed_rows(customer_id, "MD202601010001", [{"product_id": hose, "product_name": "跨年水带20m", "unit": "卷", "unit_price_yuan": "50", "quantity": "1"}], status="saved", order_date="2026-01-01")

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


def test_analytics_fully_returned_product_nets_zero():
    from erp.services.accounting import create_return_order_from_source
    from erp.services.analytics import summarize_analytics

    customer_id, product_a, _, _ = _seed_analytics_orders()
    with get_db() as conn:
        source_sale = conn.execute("SELECT id FROM orders WHERE order_no='MD202610010101'").fetchone()["id"]
        source_item = conn.execute("SELECT id FROM order_items WHERE order_id=?", (source_sale,)).fetchone()["id"]
    create_return_order_from_source(customer_id, "MD202610020102", source_sale, [{"source_item_id": source_item, "quantity": "1"}], status="saved", order_date="2026-10-02")
    zero = summarize_analytics(start_date="2026-10-01", end_date="2026-10-02", customer_id=customer_id, product_name="灭火器4kg")
    assert [row["product_id"] for row in zero["product_rows"]] == [product_a]
    # 2 件销售全部退回 → 净销售额与净销售数量都必须是显式 0（不是 None、不落回全范围）。
    assert zero["summary"]["net_sales_amount_cents"] == 0
    assert zero["summary"]["net_sales_quantity_3dp"] == 0


def test_analytics_drops_model_filter_and_keeps_empty_range_stable():
    """决策变更（v14）：型号筛选整条删除 —— 「必须先选择商品名称」的联动校验随之删除。

    旧用例钉「只传型号（未选名称）时报错必须先选商品名称」。该筛选维度连同它的前置校验
    一起删除：服务层不再接受 spec 关键字（TypeError），路由也忽略 spec（不再报型号错误）。
    """
    from erp.services.analytics import summarize_analytics
    import pytest

    # 服务层已无 spec 参数：型号不再是筛选维度。
    with pytest.raises(TypeError):
        summarize_analytics(start_date="2026-01-01", end_date="2026-01-02", spec="4kg")
    # 路由忽略 spec：不再因「只传型号」报错，页面正常渲染且不提示型号相关错误。
    page = create_app().test_client().get(
        "/analytics/", query_string={"start_date": "2026-01-01", "end_date": "2026-01-02", "spec": "4kg"}
    )
    assert page.status_code == 200
    assert "必须先选择商品名称" not in page.get_data(as_text=True)

    init_db()
    result = summarize_analytics(start_date="2026-01-01", end_date="2026-01-03", product_name="不存在的商品")
    assert result["summary"]["net_sales_amount_cents"] == 0
    assert [cell["date"] for cell in result["heatmap"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_analytics_top_filter_supports_year_month_range_and_document_types():
    """顶部筛选扩展：按年 / 按月到月 / 按日到日 + 订单类型多选，下方数据跟随。"""
    from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
    from erp.services.purchases import create_purchase_order

    init_db()
    customer = create_customer("P7筛选对象")
    product = create_product("P7商品S1", "个")

    create_order_from_typed_rows(
        customer, "MD202602100001",
        [{"product_id": product, "product_name": "P7商品S1", "unit": "个",
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
    assert 'data-type-none' in html and 'data-type-all' not in html

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


def test_document_type_filter_only_affects_flow_not_net_sales_metrics():
    """订单类型筛选：净销售额口径只算销售+退货；拿货/退拿货只影响流水与单据流水卡片。"""
    from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
    from erp.services.purchases import create_purchase_order
    from erp.services.analytics import summarize_analytics

    init_db()
    customer = create_customer("P7口径对象")
    product = create_product("P7口径商品S1", "个")

    create_order_from_typed_rows(
        customer, "MD202603100001",
        [{"product_id": product, "product_name": "P7口径商品S1", "unit": "个",
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

    # 只勾拿货单：净销售额仍按销售+退货口径（不受订单类型影响），流水只剩拿货
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
    from erp.services.purchases import create_purchase_order

    init_db()
    customer = create_customer("P7流水卡片对象")
    product = create_product("P7流水卡片商品S1", "个")

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
    assert "不参与净销售额与产品排行" in html
