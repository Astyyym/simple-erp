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
    """第二张销售单：商品未启用库存 → 明细无成本快照（关联它只作追溯，不能逐行分摊成本）。"""
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
    with get_db() as conn:
        item_id = conn.execute(
            "SELECT id FROM order_items WHERE order_id=?", (sale_id,)
        ).fetchone()[0]
    return customer, sale_id, item_id


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


def test_source_candidates_include_costless_orders_flagged_trace_only():
    """2026-10-15 口径：缺成本的单**也进候选**，用 `cost_ready=False` 标注「仅追溯」。

    旧实现把缺成本的单整单挡在候选外（选了也会被来源分摊路径硬拒），但自 2026-10-13 起
    关联已降级为「只作参考」，挡在门外等于让门店给旧单留个关联线索都做不到。
    现在候选全放行，保存时按成本是否齐备分流（见下一条回归）。
    """
    _seed()
    _costless_customer, costless_sale, _costless_item = _seed_costless_sale()
    payload = create_app().test_client().get("/orders/api/return_sources").get_json()
    by_id = {row["id"]: row for row in payload["orders"]}
    assert costless_sale in by_id, "缺成本单应出现在候选里"
    assert by_id[costless_sale]["cost_ready"] is False
    ready = [row for row in payload["orders"] if row["cost_ready"]]
    assert len(ready) == 1 and ready[0]["cost_ready"] is True
    assert payload["missing_cost_count"] == 1


def test_return_linked_to_costless_sale_saves_as_manual_return():
    """关联缺成本原单（即便每行都带 source_item_id）→ 必须能存下来，走手输路径。

    这是本次改动的核心风险：候选放行了，若保存仍走来源分摊，
    `_prepare_return_items` 会以「原销售明细缺少可靠成本」整单拒掉 → 变成「能选不能存」。
    """
    _seed()
    customer, costless_sale, costless_item = _seed_costless_sale()
    client = create_app().test_client()
    response = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer),
            "customer_name": "缺成本对象",
            "source_order_id": str(costless_sale),
            "source_item_id": [str(costless_item)],
            "product_id": [""],
            "product_name": ["缺成本商品"],
            "unit": ["个"],
            "quantity": ["1"],
            "unit_price": ["10"],
            "order_date": "2026-10-14",
            "status": "saved",
        },
    )
    assert response.status_code == 302, "关联缺成本单退货应保存成功"
    with get_db() as conn:
        order = conn.execute(
            "SELECT id, source_order_id, total_amount_cents FROM orders WHERE order_type='return'"
        ).fetchone()
        assert order is not None
        assert order["source_order_id"] == costless_sale, "追溯信息必须记在单上"
        item = conn.execute(
            "SELECT product_name, source_item_id FROM order_items WHERE order_id=?", (order["id"],)
        ).fetchone()
    # 走手输路径：不逐行绑定来源明细，成本由 post_typed_return_order 按当前移动平均回冲
    assert item["source_item_id"] is None, "缺成本关联退货应走手输路径（不逐行绑来源）"
    assert item["product_name"] == "缺成本商品"


def test_return_linked_to_cost_ready_sale_still_uses_source_allocation():
    """成本齐备的原单不变：关联 + 逐行 source_item_id 仍走来源分摊（按原成本回冲、记来源行）。"""
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
        order = conn.execute(
            "SELECT id, source_order_id, total_amount_cents FROM orders WHERE order_type='return'"
        ).fetchone()
        assert order["source_order_id"] == sale_id
        assert order["total_amount_cents"] == -6000
        item = conn.execute(
            "SELECT source_item_id FROM order_items WHERE order_id=?", (order["id"],)
        ).fetchone()
    assert item["source_item_id"] == item_id, "成本齐备应走来源分摊路径（逐行绑来源）"


def test_draft_return_linked_to_costless_sale_can_be_re_edited_and_finalized():
    """关联缺成本原单的退货**草稿**：重编辑并正式保存必须走得通（不能成为死路）。

    旧编辑分支只要 `source_order_id` 非空就强制走 `update_return_draft_from_source`
    → `_prepare_return_items`，而该路径要求逐行绑定来源明细且原明细必须有成本。
    候选放行缺成本单之后，若这里不分流，用户就会「能关联、能存草稿、改不了」。
    """
    _seed()
    customer, costless_sale, costless_item = _seed_costless_sale()
    client = create_app().test_client()
    created = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer),
            "customer_name": "缺成本对象",
            "source_order_id": str(costless_sale),
            "source_item_id": [str(costless_item)],
            "product_id": [""],
            "product_name": ["缺成本商品"],
            "unit": ["个"],
            "quantity": ["1"],
            "unit_price": ["10"],
            "order_date": "2026-10-14",
            "status": "draft",
        },
    )
    assert created.status_code == 302
    with get_db() as conn:
        draft = conn.execute(
            "SELECT id, status, source_order_id, version FROM orders WHERE order_type='return'"
        ).fetchone()
    assert draft["status"] == "draft" and draft["source_order_id"] == costless_sale
    order_id = draft["id"]

    # 编辑页必须可进（草稿不因关联而锁死）
    page = client.get(f"/orders/{order_id}/edit")
    assert page.status_code == 200
    assert "无法重编辑" not in page.get_data(as_text=True)

    # 改数量后正式保存：走手输路径，不该被「缺少可靠成本」拦下
    edited = client.post(
        f"/orders/{order_id}/edit",
        data={
            "customer_id": str(customer),
            "customer_name": "缺成本对象",
            "product_id": [""],
            "product_name": ["缺成本商品"],
            "unit": ["个"],
            "quantity": ["2"],
            "unit_price": ["10"],
            "status": "saved",
            "version": str(draft["version"]),
        },
    )
    assert edited.status_code == 302
    body = client.get(edited.headers["Location"]).get_data(as_text=True)
    assert "无法重编辑" not in body
    with get_db() as conn:
        order = conn.execute(
            "SELECT id, status, source_order_id, total_amount_cents FROM orders WHERE id=?", (order_id,)
        ).fetchone()
    assert order["status"] == "saved"
    assert order["source_order_id"] == costless_sale, "重编辑后追溯关联仍要保留"
    assert order["total_amount_cents"] == -2000  # 2 × 10 元


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
