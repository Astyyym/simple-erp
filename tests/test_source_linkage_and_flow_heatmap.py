"""2026-10-06：来源关联追溯（A 方案）与流水净额热力图。"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
from erp.services.purchases import create_purchase_order


def _seed():
    init_db()
    cid = create_customer("来源关联对象")
    pid = create_product("来源关联商品S1", "个")

    return cid, pid


def test_return_page_offers_optional_sales_source_linkage():
    cid, pid = _seed()
    create_order_from_typed_rows(
        cid, "MD202610060001",
        [{"product_id": pid, "product_name": "来源关联商品S1", "unit": "个", "quantity": "2", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-10-06",
    )
    client = create_app().test_client()
    html = client.get("/orders/return/new").get_data(as_text=True)
    assert "原销售单（可选）" in html
    # 检索式来源框：点击输入框列最近单据（候选走 API），不再是服务端渲染全量的原生下拉。
    assert 'id="returnSourceInput"' in html
    assert 'id="sourceOrderId"' in html and 'name="source_order_id"' in html
    assert 'id="returnSourceSuggestions"' in html
    assert 'id="returnSourceSelect"' not in html
    # A 方案：不强制来源 —— 候选按需取，页面本身不带候选 option
    assert '<option value="1">MD202610060001' not in html
    listed = client.get("/orders/api/return_sources").get_json()["orders"]
    assert [row["order_no"] for row in listed] == ["MD202610060001"]
    # 原单关联位于单据信息卡第一行（在客户名称之前），并带同层级预览块
    assert 'class="order-source-row"' in html
    assert html.index('id="returnSourceInput"') < html.index('id="customer_name"')
    assert 'id="sourcePreview"' in html and 'id="sourcePreviewApply"' in html


def test_return_source_api_returns_rows_available_and_reference_price():
    cid, pid = _seed()
    sale_id = create_order_from_typed_rows(
        cid, "MD202610060001",
        [{"product_id": pid, "product_name": "来源关联商品S1", "unit": "个", "quantity": "3", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-10-06",
    )
    client = create_app().test_client()
    payload = client.get(f"/orders/api/return_sources/{sale_id}").get_json()
    assert payload["items"][0]["available_quantity"] == "3"
    assert payload["items"][0]["unit_price"] == "30.00"
    assert payload["items"][0]["product_id"] == pid
    # 预览需要单头信息（单号/日期/合计）与原始数量
    assert payload["source"]["order_no"] == "MD202610060001"
    assert payload["source"]["order_date"] == "2026-10-06"
    assert payload["source"]["total_amount_cents"] == 9000
    assert payload["items"][0]["original_quantity"] == "3"


def test_return_page_without_source_is_not_forced_to_link():
    cid, pid = _seed()
    html = create_app().test_client().get("/orders/return/new").get_data(as_text=True)
    assert 'id="returnSourceInput"' in html
    # 不强制关联：来源输入框不是必填，明细行照旧可直接手输
    input_tag = html[html.index('id="returnSourceInput"'):]
    input_tag = input_tag[:input_tag.index('>')]
    assert "required" not in input_tag
    assert 'picker-name-input' in html


def test_purchase_return_page_links_original_purchase_instead_of_notes():
    cid, pid = _seed()
    create_purchase_order(
        "NH202610060001", "2026-10-06",
        [{"product_id": pid, "quantity": "5", "unit_cost_yuan": "14"}],
        request_key="src-purchase", customer_id=cid,
    )
    html = create_app().test_client().get("/purchases/return/new").get_data(as_text=True)
    assert "原拿货单（可选）" in html
    # F4（2026-10-14）：原拿货单改成与退货单同款的可检索输入框（点击列最近单据，候选走 API），
    # 不再是服务端渲染的全量原生下拉。
    assert 'id="purchaseSourceInput"' in html
    assert 'id="purchaseSourceId"' in html and 'name="source_order_id"' in html
    assert 'id="purchaseSourceSuggestions"' in html
    assert 'id="purchaseSourceClear"' in html
    assert 'id="purchaseSourceSelect"' not in html
    # 候选按需取：页面本身不再带候选 option（单号通过 API 返回，见下一条测试）
    assert "NH202610060001" not in html
    # 内部备注输入框已移除
    assert 'id="returnNotes"' not in html
    # 原单关联位于单据信息卡第一行（在往来对象之前），并带同层级预览块
    assert 'class="order-source-row"' in html
    assert html.index('id="purchaseSourceInput"') < html.index('id="customer_name"')
    assert 'id="sourcePreview"' in html and 'id="sourcePreviewApply"' in html


def test_purchase_source_api_is_searchable_and_carries_the_counterparty():
    """F4：候选接口要能按单号/往来对象过滤，并带出 customer_id 供页面把往来对象对齐到原单。"""
    cid, pid = _seed()
    order = create_purchase_order(
        "NH202610060001", "2026-10-06",
        [{"product_id": pid, "quantity": "5", "unit_cost_yuan": "14"}],
        request_key="src-purchase-search", customer_id=cid,
    )
    client = create_app().test_client()
    listing = client.get("/purchases/api/purchase_sources").get_json()
    row = next(item for item in listing if item["order_no"] == "NH202610060001")
    assert row["customer_id"] == cid
    assert row["customer_name"] == "来源关联对象"
    for query in ("NH202610060001", "来源关联", "LYGLDX"):
        hits = client.get(f"/purchases/api/purchase_sources?q={query}").get_json()
        assert [item["id"] for item in hits] == [order["order_id"]], f"按「{query}」没搜到原拿货单"
    assert client.get("/purchases/api/purchase_sources?q=不存在的单号").get_json() == []


def test_purchase_source_api_lists_saved_orders_and_items():
    cid, pid = _seed()
    order = create_purchase_order(
        "NH202610060001", "2026-10-06",
        [{"product_id": pid, "quantity": "5", "unit_cost_yuan": "14"}],
        request_key="src-purchase-2", customer_id=cid,
    )
    client = create_app().test_client()
    listing = client.get("/purchases/api/purchase_sources").get_json()
    assert any(row["order_no"] == "NH202610060001" for row in listing)
    detail = client.get(f"/purchases/api/purchase_source/{order['order_id']}").get_json()
    assert detail["items"][0]["quantity"] == "5"
    assert detail["items"][0]["unit_price"] == "14.00"


def test_purchase_entry_page_no_longer_exposes_source_notes_field():
    _seed()
    html = create_app().test_client().get("/purchases/new").get_data(as_text=True)
    assert "货源备注" not in html
    assert 'id="sourceNotes"' not in html


def test_analytics_flow_heatmap_uses_signed_flow_net():
    from erp.services.analytics import summarize_analytics
    cid, pid = _seed()
    create_order_from_typed_rows(
        cid, "MD202610060001",
        [{"product_id": pid, "product_name": "来源关联商品S1", "unit": "个", "quantity": "1", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-10-06",
    )
    create_purchase_order(
        "NH202610060001", "2026-10-06",
        [{"product_id": pid, "quantity": "1", "unit_cost_yuan": "10"}],
        request_key="flow-purchase", customer_id=cid,
    )
    result = summarize_analytics(start_date="2026-10-06", end_date="2026-10-06")
    cell = next(c for c in result["heatmap"] if c["date"] == "2026-10-06")
    # 销售 +3000，拿货 −1000 → 流水净额 2000
    assert cell["sales_amount_cents"] == 3000
    assert cell["flow_net_cents"] == 2000
    assert cell["metric_value"] == 2000


def test_analytics_page_renders_flow_heatmap_labelling():
    _seed()
    html = create_app().test_client().get("/analytics/").get_data(as_text=True)
    assert "流水热力图" in html
    assert "流水净额" in html
    assert "销售热力图" not in html
