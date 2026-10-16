"""退货单「原销售单」检索式关联（2026-10-13）。

背景：此前页面只有一个**不带 `name`** 的原生 `<select>`，模板里也没有隐藏域，
`source_order_id` 从未随表单提交 → 后端 `create_return_order_from_source` 永远走不到，
`orders.source_order_id` 恒为 NULL（`git log -S'name="source_order_id"' -- app/erp/templates` 零结果，
即提交链路从未存在过，不是回归）。本文件守住"页面能选、请求真带、库里有值、详情页能看到"这条完整链路。
"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product


def _seed():
    """第一张正式销售单（明细齐全）。"""
    init_db()
    customer = create_customer("退货来源对象")
    product_id = create_product("退货来源商品", "个")

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


def _seed_second_sale():
    """第二张正式销售单（另一个客户/商品），用于验证候选与逐行关联的多单场景。"""
    customer = create_customer("缺成本对象")
    product_id = create_product("缺成本商品", "个")
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
    _customer, _product_id, sale_id, _item_id = _seed()
    client = create_app().test_client()

    payload = client.get("/orders/api/return_sources").get_json()
    assert [row["order_no"] for row in payload["orders"]] == ["MD202610130001"]
    assert payload["orders"][0]["customer_name"] == "退货来源对象"
    assert payload["orders"][0]["amount_display"] == "90.00"
    # v15：成本链删除后响应里不再有「缺成本条数」（2026-10-16 决策 ②/③）。
    assert "missing_cost_count" not in payload

    for query in ("MD2026", "退货来源对象"):
        rows = client.get(f"/orders/api/return_sources?q={query}").get_json()["orders"]
        assert [row["id"] for row in rows] == [sale_id], query
    assert client.get("/orders/api/return_sources?q=不存在").get_json()["orders"] == []


def test_source_candidates_include_every_valid_sale_and_flag_row_bindable():
    """2026-10-16 口径：候选不再按「成本是否齐备」分流；`row_bindable` = 原单有没有明细可逐行绑。

    旧用例断言缺成本的单以 `cost_ready=False` 进候选。成本链删除后该字段改名为
    `row_bindable` 且语义收窄为「有明细即可绑」——两张有明细的销售单都进候选、都可逐行绑。
    """
    _customer, _product_id, sale_id, _item_id = _seed()
    _second_customer, second_sale, _second_item = _seed_second_sale()
    payload = create_app().test_client().get("/orders/api/return_sources").get_json()
    by_id = {row["id"]: row for row in payload["orders"]}
    assert sale_id in by_id and second_sale in by_id, "所有有效销售单都应进候选"
    assert by_id[sale_id]["row_bindable"] is True
    assert by_id[second_sale]["row_bindable"] is True
    assert "cost_ready" not in by_id[sale_id]


def test_return_linked_to_source_sale_binds_rows_and_keeps_trace():
    """每行都带 source_item_id 的关联退货 → 走逐行关联路径：绑来源明细、按原单单价计价、记住原单。

    v15（决策 ②/③）：逐行关联的前置条件从「原单每行都带成本快照」放宽为
    「原单有明细可绑」（`row_bindable`），因此结果从「手输不绑来源」变成「绑来源明细」。
    """
    _seed()
    customer, source_sale, source_item = _seed_second_sale()
    client = create_app().test_client()
    response = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer),
            "customer_name": "缺成本对象",
            "source_order_id": str(source_sale),
            "source_item_id": [str(source_item)],
            "product_id": [""],
            "product_name": ["缺成本商品"],
            "unit": ["个"],
            "quantity": ["1"],
            "unit_price": ["10"],
            "order_date": "2026-10-14",
            "status": "saved",
        },
    )
    assert response.status_code == 302, "关联原单退货应保存成功"
    with get_db() as conn:
        order = conn.execute(
            "SELECT id, source_order_id, total_amount_cents FROM orders WHERE order_type='return'"
        ).fetchone()
        assert order is not None
        assert order["source_order_id"] == source_sale, "追溯信息必须记在单上"
        item = conn.execute(
            "SELECT product_name, source_item_id, subtotal_cents FROM order_items WHERE order_id=?",
            (order["id"],),
        ).fetchone()
    # 逐行关联：绑上原销售明细，金额按原单行单价计算（1 × 10 元）。
    assert item["source_item_id"] == source_item
    assert item["product_name"] == "缺成本商品"
    assert item["subtotal_cents"] == 1000
    assert order["total_amount_cents"] == -1000


def test_return_linked_to_source_sale_allocates_by_source_unit_price():
    """逐行关联的退货按原销售明细单价计价并记来源行（这里退 2/3 → 2 × 30 元 = 60.00 元）。"""
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
    assert item["source_item_id"] == item_id, "逐行关联应绑上来源明细"


def test_draft_return_linked_to_source_sale_can_be_re_edited_and_finalized():
    """关联了原销售明细的退货**草稿**：重编辑并正式保存必须走得通（不能成为死路）。

    2026-10-16（决策 ②/③/④）：逐行关联不再要求原单成本齐备（`row_bindable` = 原单有没有明细），
    正式退货单的编辑锁也一并撤除。草稿重编辑按逐行关联提交 source_item_id，
    走 `update_return_draft_from_source`；追溯关联与原单价格保持不变，数量受可退额度约束。
    """
    _seed()
    customer, source_sale, source_item = _seed_second_sale()
    client = create_app().test_client()
    created = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer),
            "customer_name": "缺成本对象",
            "source_order_id": str(source_sale),
            "source_item_id": [str(source_item)],
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
        draft_item = conn.execute(
            "SELECT source_item_id, quantity FROM order_items WHERE order_id=?", (draft["id"],)
        ).fetchone()
    assert draft["status"] == "draft" and draft["source_order_id"] == source_sale
    assert draft_item["source_item_id"] == source_item, "原单有明细 → 逐行关联成立"
    order_id = draft["id"]

    # 编辑页必须可进（草稿不因关联而锁死）
    page = client.get(f"/orders/{order_id}/edit")
    assert page.status_code == 200
    assert "无法重编辑" not in page.get_data(as_text=True)

    # 重编辑后正式保存：逐行关联路径（原单只此一行，数量上限 = 1）
    edited = client.post(
        f"/orders/{order_id}/edit",
        data={
            "customer_id": str(customer),
            "customer_name": "缺成本对象",
            "source_item_id": [str(source_item)],
            "unit": ["件"],
            "quantity": ["1"],
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
        item = conn.execute(
            "SELECT unit, quantity, source_item_id, subtotal_cents FROM order_items WHERE order_id=?",
            (order_id,),
        ).fetchone()
    assert order["status"] == "saved"
    assert order["source_order_id"] == source_sale, "重编辑后追溯关联仍要保留"
    assert order["total_amount_cents"] == -1000  # 原单明细单价 10 元 × 1
    assert item["source_item_id"] == source_item
    assert item["unit"] == "件" and item["quantity"] == "1", "重编辑按表单提交值落库"


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
            "SELECT source_item_id, quantity, subtotal_cents FROM order_items WHERE order_id=?", (return_id,)
        ).fetchone()
        assert item["source_item_id"] == item_id
        # 退货行金额按来源明细的原单价计算（2 × 30 元），单头金额取负。
        assert item["subtotal_cents"] == 6000

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
