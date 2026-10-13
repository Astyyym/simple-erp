"""2026-10-11 历史成本人工补录（销售单）— 行为测试。

设计冻结见 `开发短计划/2026-10-11_历史成本补录_执行计划.md`：

- 只限销售单（`order_type='sale'`）；逐行补录；
- 真实销售时快照行不可覆盖；`cost_source='backfill'` 行可再修正，
  但该单已产生有效退货时拒绝再修正；
- 原因必填并写审计（与补录写入同事务）；
- 补录后该单禁止重编辑（GET/POST 双拦截）；
- **只补利润、不动库存**：绝不触碰 `inventory_postings` /
  `product_inventory_state` / `inventory_initializations`。
"""
from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import initialize_product
from erp.services.profit import summarize_profit


def _seed_sale_without_cost(*, order_no="MD202610110001", name="缺成本商品", spec="A",
                            price="100", quantity="2", order_date="2026-10-11"):
    """零建档路径：商品没有库存状态行 → 销售时不写成本快照（NULL）。"""
    init_db()
    product_id = create_product((name) + (spec), "个", 1000)
    customer_id = create_customer(f"{name}客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        order_no,
        [{"product_id": product_id, "product_name": (name) + (spec), "unit": "个",
          "unit_price_yuan": price, "quantity": quantity}],
        status="saved",
        order_date=order_date,
    )
    with get_db() as conn:
        item_id = int(conn.execute(
            "SELECT id FROM order_items WHERE order_id=? ORDER BY id LIMIT 1", (order_id,)
        ).fetchone()["id"])
    return order_id, item_id, product_id, customer_id


def _item(item_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM order_items WHERE id=?", (item_id,)).fetchone()


def _inventory_fingerprint():
    with get_db() as conn:
        states = [tuple(row) for row in conn.execute(
            "SELECT * FROM product_inventory_state ORDER BY product_id"
        ).fetchall()]
        postings = int(conn.execute("SELECT COUNT(*) AS n FROM inventory_postings").fetchone()["n"])
        initializations = [tuple(row) for row in conn.execute(
            "SELECT * FROM inventory_initializations ORDER BY product_id"
        ).fetchall()]
    return states, postings, initializations


# --------------------------- Phase 1：数据层 ---------------------------

def test_order_items_gains_idempotent_cost_source_column():
    """`cost_source` 幂等加列；重复 init_db 不报错、不重复加列。"""
    init_db()
    init_db()
    with get_db() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(order_items)")}
    assert "cost_source" in columns


def test_cost_source_column_does_not_bump_schema_version():
    """对齐既有惯例：幂等 ALTER TABLE 不推进 CURRENT_SCHEMA_VERSION。"""
    from erp.db import CURRENT_SCHEMA_VERSION

    init_db()
    with get_db() as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version == CURRENT_SCHEMA_VERSION


# --------------------------- Phase 1：服务层核心 ---------------------------

def test_backfill_writes_snapshot_and_marks_backfill_source():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    assert _item(item_id)["cost_total_micro"] is None

    result = backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "迁移补录")

    assert result["updated"] == 1
    row = _item(item_id)
    assert row["unit_cost_micro"] == 10 * 1_000_000
    assert row["cost_total_micro"] == 2 * 10 * 1_000_000
    assert row["cost_source"] == "backfill"


def test_backfill_total_cost_backs_out_unit_cost():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    # 15 元 / 2 件 = 7.5 元/件
    backfill_order_costs(order_id, [{"item_id": item_id, "total_cost": "15"}], "按总额补录")

    row = _item(item_id)
    assert row["unit_cost_micro"] == 7_500_000
    assert row["cost_total_micro"] == 15_000_000


def test_backfill_never_touches_inventory_tables():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    before = _inventory_fingerprint()
    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "只补利润")
    assert _inventory_fingerprint() == before


def test_backfill_makes_profit_complete_and_deducts_backfilled_cost():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    before = summarize_profit(start_date="2026-10-01", end_date="2026-10-31")
    assert before["cost_complete"] is False
    assert before["unknown_order_count"] == 1

    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "补录后毛利可算")

    after = summarize_profit(start_date="2026-10-01", end_date="2026-10-31")
    assert after["cost_complete"] is True
    assert after["unknown_order_count"] == 0
    # 200 元销售额 − 20 元补录成本 = 180 元
    assert after["net_cost_cents"] == 2000
    assert after["gross_profit_cents"] == 18000


def test_backfill_keeps_order_out_of_return_sources_until_cost_present():
    """补录后该单出现在退货来源下拉（EXISTS 条件自动满足）。"""
    from erp.routes.orders import _return_source_orders
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    with get_db() as conn:
        assert [row["id"] for row in _return_source_orders(conn)] == []

    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "补录后可退")

    with get_db() as conn:
        assert [row["id"] for row in _return_source_orders(conn)] == [order_id]


# --------------------------- Phase 1：快照保护与状态保护 ---------------------------

def test_backfill_refuses_to_overwrite_real_sale_snapshot():
    from erp.services.cost_backfill import backfill_order_costs

    init_db()
    product_id = create_product("真实快照商品A", "个", 1000)
    customer_id = create_customer("真实快照客户")
    initialize_product(product_id, "10", "10", "2026-10-01", "期初", "bf-init-1")
    order_id = create_order_from_typed_rows(
        customer_id, "MD202610110002",
        [{"product_id": product_id, "product_name": "真实快照商品A", "unit": "个",
          "unit_price_yuan": "25", "quantity": "2"}],
        status="saved", order_date="2026-10-11",
    )
    with get_db() as conn:
        item_id = int(conn.execute(
            "SELECT id FROM order_items WHERE order_id=?", (order_id,)
        ).fetchone()["id"])
    assert _item(item_id)["cost_total_micro"] is not None

    try:
        backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "99"}], "不该允许")
        assert False, "真实销售时成本快照不可被补录覆盖"
    except ValueError as exc:
        assert "成本" in str(exc)
    assert _item(item_id)["cost_total_micro"] == 20 * 1_000_000


def test_backfilled_line_can_be_corrected_again():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "第一次")
    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "12"}], "填错了修正")

    row = _item(item_id)
    assert row["unit_cost_micro"] == 12 * 1_000_000
    assert row["cost_source"] == "backfill"
    with get_db() as conn:
        logs = conn.execute(
            "SELECT summary FROM audit_logs WHERE action='backfill_order_cost' AND target_id=? ORDER BY id",
            (str(order_id),),
        ).fetchall()
    assert len(logs) == 2
    assert "第一次" in logs[0]["summary"] and "填错了修正" in logs[1]["summary"]


def test_backfill_rejects_correction_when_order_has_live_return():
    """已有有效退货 → 拒绝再修正（避免留下永久差额）。"""
    from erp.services.accounting import create_return_order_from_source
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, product_id, customer_id = _seed_sale_without_cost()
    initialize_product(product_id, "10", "10", "2026-10-01", "事后启用", "bf-init-2")
    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "先补录")
    create_return_order_from_source(
        customer_id, "MD202610110003", order_id,
        [{"source_item_id": item_id, "quantity": "1"}],
        order_date="2026-10-11",
    )

    try:
        backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "12"}], "有退货后修正")
        assert False, "已有有效退货时必须拒绝再修正"
    except ValueError as exc:
        assert "退货" in str(exc)
    assert _item(item_id)["unit_cost_micro"] == 10 * 1_000_000


def test_backfill_rejects_non_sale_order_and_uneditable_states():
    from erp.services.cost_backfill import backfill_order_costs

    init_db()
    product_id = create_product("状态保护商品A", "个", 1000)
    customer_id = create_customer("状态保护客户")

    draft_id = create_order_from_typed_rows(
        customer_id, "MD202610110004",
        [{"product_id": product_id, "product_name": "状态保护商品A", "unit": "个",
          "unit_price_yuan": "10", "quantity": "1"}],
        status="draft", order_date="2026-10-11",
    )
    with get_db() as conn:
        draft_item = int(conn.execute(
            "SELECT id FROM order_items WHERE order_id=?", (draft_id,)
        ).fetchone()["id"])
    try:
        backfill_order_costs(draft_id, [{"item_id": draft_item, "unit_cost": "10"}], "草稿不许")
        assert False, "草稿单不可补录"
    except ValueError as exc:
        assert "草稿" in str(exc)

    return_id = create_order_from_typed_rows(
        customer_id, "MD202610110005",
        [{"product_id": product_id, "product_name": "状态保护商品A", "unit": "个",
          "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-11", order_type="return",
    )
    with get_db() as conn:
        return_item = int(conn.execute(
            "SELECT id FROM order_items WHERE order_id=?", (return_id,)
        ).fetchone()["id"])
    try:
        backfill_order_costs(return_id, [{"item_id": return_item, "unit_cost": "10"}], "退货单不许")
        assert False, "退货单不可补录"
    except ValueError as exc:
        assert "销售单" in str(exc)


def test_backfill_requires_non_empty_reason():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    for reason in ("", "   "):
        try:
            backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], reason)
            assert False, "原因必填"
        except ValueError as exc:
            assert "原因" in str(exc)
    assert _item(item_id)["cost_total_micro"] is None


def test_backfill_rejects_invalid_amounts():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    for bad in ("-1", "abc", "1.1234567"):
        try:
            backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": bad}], "非法金额")
            assert False, f"{bad} 应被拒绝"
        except ValueError:
            pass
    assert _item(item_id)["cost_total_micro"] is None


def test_backfill_rejects_item_from_another_order():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    other_id, other_item, _, _ = _seed_sale_without_cost(
        order_no="MD202610110006", name="另一个商品", spec="B"
    )
    try:
        backfill_order_costs(order_id, [{"item_id": other_item, "unit_cost": "10"}], "跨单")
        assert False, "明细必须属于该单"
    except ValueError as exc:
        assert "明细" in str(exc)
    assert _item(other_item)["cost_total_micro"] is None


def test_backfill_audit_row_is_written_in_same_transaction():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "留痕检查")
    with get_db() as conn:
        log = conn.execute(
            "SELECT * FROM audit_logs WHERE action='backfill_order_cost' AND target_id=?",
            (str(order_id),),
        ).fetchone()
    assert log is not None
    assert "留痕检查" in log["summary"]
    assert log["target_type"] == "order"


# --------------------------- Phase 2：入口与编辑保护 ---------------------------

def test_backfilled_order_is_not_editable_and_edit_route_refuses():
    from erp.routes.orders import _document_editable, _document_edit_block_reason
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    assert _document_editable("sale", "saved", False) is True
    backfill_order_costs(order_id, [{"item_id": item_id, "unit_cost": "10"}], "补录后禁编辑")
    assert _document_editable("sale", "saved", False, has_backfill=True) is False
    assert "补录" in _document_edit_block_reason("sale", "saved", False, has_backfill=True)

    client = create_app().test_client()
    assert client.get(f"/orders/{order_id}/edit").status_code == 400

    # 验收 #5：列表页「编辑」置灰且原因准确（不是直接隐藏按钮）。
    listing = client.get("/orders/").get_data(as_text=True)
    assert "重编辑会清除补录数据" in listing
    assert f'href="/orders/{order_id}/edit"' not in listing
    # POST 编辑路由同样拒绝，不能绕过。
    blocked = client.post(f"/orders/{order_id}/edit", data={
        "customer_name": "缺成本商品客户", "status": "saved",
        "product_name": ["缺成本商品"], "unit": ["个"], "unit_price": ["100"], "quantity": ["3"],
    })
    assert blocked.status_code == 400
    assert _item(item_id)["cost_total_micro"] == 2 * 10 * 1_000_000


def test_backfill_page_renders_form_and_discloses_edit_lock():
    order_id, item_id, _, _ = _seed_sale_without_cost()
    html = create_app().test_client().get(f"/orders/{order_id}/cost_backfill").get_data(as_text=True)
    assert 'name="reason"' in html
    assert f'name="unit_cost_{item_id}"' in html
    assert "人工补录" in html
    assert "不能再重编辑" in html


def test_backfill_page_rejects_non_sale_and_void_orders():
    order_id, item_id, _, _ = _seed_sale_without_cost()
    with get_db() as conn:
        conn.execute("UPDATE orders SET status='void', void_reason='测试作废' WHERE id=?", (order_id,))
    resp = create_app().test_client().get(f"/orders/{order_id}/cost_backfill")
    assert resp.status_code == 400
    assert "作废" in resp.get_data(as_text=True)


def test_backfill_post_writes_and_redirects_with_notice():
    from erp.services.cost_backfill import backfill_order_costs  # noqa: F401  (RED 锚点)

    order_id, item_id, _, _ = _seed_sale_without_cost()
    client = create_app().test_client()
    resp = client.post(
        f"/orders/{order_id}/cost_backfill",
        data={"reason": "网页补录", f"unit_cost_{item_id}": "10"},
    )
    assert resp.status_code in (302, 303)
    assert _item(item_id)["cost_total_micro"] == 2 * 10 * 1_000_000

    detail = client.get(f"/orders/{order_id}").get_data(as_text=True)
    assert "人工补录" in detail


# --------------------------- Phase 3：发现路径 ---------------------------

def test_list_marks_missing_cost_sale_and_filters_it():
    from erp.services.cost_backfill import backfill_order_costs

    order_id, item_id, _, _ = _seed_sale_without_cost()
    ok_id, ok_item, _, _ = _seed_sale_without_cost(
        order_no="MD202610110007", name="有成本商品", spec="C"
    )
    backfill_order_costs(ok_id, [{"item_id": ok_item, "unit_cost": "5"}], "有成本")

    client = create_app().test_client()
    html = client.get("/orders/").get_data(as_text=True)
    assert "缺成本" in html

    filtered = client.get("/orders/?cost=missing").get_data(as_text=True)
    assert f"MD202610110001" in filtered
    assert "MD202610110007" not in filtered

    # 非销售分支固定 1=0：勾了缺成本 + 只看拿货，不应返回任何单据。
    none_side = client.get("/orders/?cost=missing&order_type=purchase").get_data(as_text=True)
    assert "当前筛选范围暂无单据" in none_side
