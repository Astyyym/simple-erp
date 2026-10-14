"""退货单「原销售单」检索式关联（2026-10-13）。

背景：此前页面只有一个**不带 `name`** 的原生 `<select>`，模板里也没有隐藏域，
`source_order_id` 从未随表单提交 → 后端 `create_return_order_from_source` 永远走不到，
`orders.source_order_id` 恒为 NULL（`git log -S'name="source_order_id"' -- app/erp/templates` 零结果，
即提交链路从未存在过，不是回归）。本文件守住"页面能选、请求真带、库里有值、详情页能看到"这条完整链路。
"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import initialize_product


def _seed():
    """一张有成本快照的正式销售单（商品已启用库存 → 明细带成本）。"""
    init_db()
    customer = create_customer("退货来源对象")
    product_id = create_product("退货来源商品", "个", 3000)
    initialize_product(product_id, "10", "10", "2026-10-01", "期初", "ret-src-init")
    sale_id = create_order_from_typed_rows(
        customer,
        "MD202610130001",
        [{"product_id": product_id, "product_name": "退货来源商品", "unit": "个",
          "quantity": "3", "unit_price_yuan": "30"}],
        status="saved",
        order_date="2026-10-13",
    )
    with get_db() as conn:
        item_id = conn.execute(
            "SELECT id FROM order_items WHERE order_id=?", (sale_id,)
        ).fetchone()[0]
    return customer, product_id, sale_id, item_id


def _seed_costless_sale():
    """第二张销售单：商品未启用库存 → 明细无成本快照（不能作为关联退货来源）。"""
    customer = create_customer("缺成本对象")
    product_id = create_product("缺成本商品", "个", 1000)
    sale_id = create_order_from_typed_rows(
        customer,
        "MD202610130002",
        [{"product_id": product_id, "product_name": "缺成本商品", "unit": "个",
          "quantity": "1", "unit_price_yuan": "10"}],
        status="saved",
        order_date="2026-10-13",
    )
    return sale_id


def test_return_page_submits_source_order_id():
    """页面必须真的把选中的原单提交上去（旧 `<select>` 无 name，这是缺陷根因）。"""
    _seed()
    client = create_app().test_client()
    html = client.get("/orders/return/new").get_data(as_text=True)
    assert "原销售单（可选）" in html
    assert 'id="returnSourceInput"' in html
    assert 'id="sourceOrderId"' in html and 'name="source_order_id"' in html
    assert 'id="returnSourceSelect"' not in html
    # 检索式候选：候选按需走 API，页面不再服务端渲染候选（无 option 列表）
    assert "/orders/api/return_sources?q=" in html
    assert '<option value="1">MD202610130001' not in html


def test_source_candidates_list_recent_orders_and_filter_by_number_or_customer():
    customer, product_id, sale_id, item_id = _seed()
    client = create_app().test_client()

    payload = client.get("/orders/api/return_sources").get_json()
    assert [row["order_no"] for row in payload["orders"]] == ["MD202610130001"]
    assert payload["orders"][0]["customer_name"] == "退货来源对象"
    assert payload["orders"][0]["amount_display"] == "90.00"
    assert payload["missing_cost_count"] == 0

    for query in ("MD2026", "退货来源对象"):
        rows = client.get(f"/orders/api/return_sources?q={query}").get_json()["orders"]
        assert [row["id"] for row in rows] == [sale_id], query
    assert client.get("/orders/api/return_sources?q=不存在").get_json()["orders"] == []


def test_source_candidates_hide_costless_orders_and_only_list_linkable_ones():
    """成本门槛保留：缺成本的单不进候选（选了也会被后端硬拒），只记张数用于提示。"""
    _seed()
    costless_sale = _seed_costless_sale()
    payload = create_app().test_client().get("/orders/api/return_sources").get_json()
    assert costless_sale not in [row["id"] for row in payload["orders"]]
    assert payload["missing_cost_count"] == 1


def test_return_created_from_source_keeps_link_and_source_item_in_database():
    customer, product_id, sale_id, item_id = _seed()
    client = create_app().test_client()
    response = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer),
            "customer_name": "退货来源对象",
            "source_order_id": str(sale_id),
            "source_item_id": [str(item_id)],
            "unit": ["个"],
            "quantity": ["2"],
            "order_date": "2026-10-13",
            "status": "saved",
        },
    )
    assert response.status_code == 302
    with get_db() as conn:
        orders = conn.execute(
            "SELECT id, source_order_id, total_amount_cents FROM orders WHERE order_type='return'"
        ).fetchall()
        assert len(orders) == 1
        return_id = orders[0]["id"]
        assert orders[0]["source_order_id"] == sale_id
        assert orders[0]["total_amount_cents"] == -6000  # 2 × 30 元，按来源原单价
        item = conn.execute(
            "SELECT source_item_id, quantity FROM order_items WHERE order_id=?", (return_id,)
        ).fetchone()
        assert item["source_item_id"] == item_id
        # 关联退货回冲库存（商品已启用库存）
        state = conn.execute(
            "SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)
        ).fetchone()[0]
    assert state == 9000  # 期初 10 − 销售 3 + 退 2

    detail = client.get(f"/orders/{return_id}").get_data(as_text=True)
    assert "原销售单" in detail and f"/orders/{sale_id}" in detail
    # 详情页曾经把上下文变量写成 `order.source_order_available`（sqlite3.Row 上恒 undefined），
    # 于是永远渲染成「原单已删除」——这里守住修复，别再退化。
    assert "原单已删除" not in detail


def test_return_with_source_but_hand_typed_rows_keeps_trace_only():
    """原单关联只作**参考 / 追溯**：明细手输时按手输路径保存，单据仍记住原单号。

    2026-10-13 用户口径：「关联的作用只是显示出来，作开单的参考」「允许客户手动输入」。
    旧契约（选了原单就必须逐行关联，否则拒单）会把"带入后又新增一行"挡在门外。
    """
    customer, product_id, sale_id, item_id = _seed()
    client = create_app().test_client()
    response = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer),
            "customer_name": "退货来源对象",
            "source_order_id": str(sale_id),
            "source_item_id": [""],
            "product_id": [str(product_id)],
            "product_name": ["退货源商品"],
            "quantity": ["1"],
            "unit": ["个"],
            "unit_price": ["30"],
            "order_date": "2026-10-13",
            "status": "saved",
        },
    )
    assert response.status_code == 302
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, source_order_id, total_amount_cents FROM orders WHERE order_type='return'"
        ).fetchone()
        assert row is not None
        # 追溯字段仍然记住原单，但明细行没有被强制关联。
        assert row["source_order_id"] == sale_id
        assert row["total_amount_cents"] == -3000
        item = conn.execute("SELECT source_item_id FROM order_items WHERE order_id=?", (row["id"],)).fetchone()
        assert item["source_item_id"] is None
    detail = client.get(f"/orders/{row['id']}").get_data(as_text=True)
    assert f"/orders/{sale_id}" in detail
    assert "原单已删除" not in detail
