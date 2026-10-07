"""回收站永久删除与清空的行为回归（2026-10-07 用户反馈）。

背景（真 bug）：`physically_deletable_order` 要求单据 `status == 'draft'`，而正式单据删除走
「先作废再软删除」，状态恒为 `void` → 回收站里的正式单据永远清不掉；七天自动清理用的是同一个
判断，于是回收站只涨不减，变成永久垃圾。本文件锁定修复后的契约。
"""
import pytest

from erp import create_app
from erp.db import get_db, init_db, physically_deletable_order, purge_order_blocker, purge_expired_recycle_bin
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
    customer_balance_cents,
)
from erp.services.inventory import create_purchase_order, initialize_product


def _product(spec="PURGE"):
    product_id = create_product("回收站清理商品", spec, "个", 2000)
    initialize_product(product_id, "10", "10", "2026-10-01", "系统上线期初", f"purge-init-{spec}", confirm_zero=False)
    return product_id


def _sale(customer_id, product_id, order_no, qty="1", date="2026-10-07"):
    return create_order_from_typed_rows(
        customer_id,
        order_no,
        [{"product_id": product_id, "product_name": "回收站清理商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": qty}],
        status="saved",
        order_date=date,
    )


def _deleted_sale(customer_id, product_id, order_no, qty="1"):
    """造一张已进回收站的正式销售单（状态是 void，这正是旧判断挡住的形态）。"""
    order_id = _sale(customer_id, product_id, order_no, qty=qty)
    assert create_app().test_client().post(f"/orders/{order_id}/delete").status_code == 302
    with get_db() as conn:
        row = conn.execute("SELECT status, deleted_at FROM orders WHERE id=?", (order_id,)).fetchone()
    assert row["status"] == "void" and row["deleted_at"] is not None
    return order_id


def test_voided_order_in_recycle_bin_is_now_deletable():
    """核心回归：状态为 void 的回收站单据必须可永久删除（旧版恒不可删）。"""
    init_db()
    product_id = _product(spec="VOID-DEL")
    customer_id = create_customer("回收站清理客户")
    order_id = _deleted_sale(customer_id, product_id, "MD202610070901")
    with get_db() as conn:
        assert physically_deletable_order(conn, "order", order_id) is True
        assert purge_order_blocker(conn, "order", order_id) is None


def test_purge_removes_order_items_and_its_inventory_postings():
    """确认删除会连明细与它自己的库存流水一起清掉，不留孤儿记录。"""
    init_db()
    product_id = _product(spec="PURGE-FULL")
    customer_id = create_customer("回收站清理客户2")
    order_id = _deleted_sale(customer_id, product_id, "MD202610070902", qty="2")
    with get_db() as conn:
        items_before = conn.execute("SELECT COUNT(*) FROM order_items WHERE order_id=?", (order_id,)).fetchone()[0]
        postings_before = conn.execute(
            "SELECT COUNT(*) FROM inventory_postings WHERE source_id=? OR source_id LIKE ?",
            (str(order_id), f"{order_id}:%"),
        ).fetchone()[0]
    assert items_before > 0 and postings_before > 0

    response = create_app().test_client().post(f"/recycle/order/{order_id}/purge")
    assert response.status_code == 302
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM orders WHERE id=?", (order_id,)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM order_items WHERE order_id=?", (order_id,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM inventory_postings WHERE source_id=? OR source_id LIKE ?",
            (str(order_id), f"{order_id}:%"),
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_live_return_blocks_sale_purge_with_readable_reason():
    """有未删除的退货单引用时拒绝，并给出可读原因（不是笼统的『不能删除』）。"""
    init_db()
    product_id = _product(spec="LIVE-REF")
    customer_id = create_customer("活引用客户")
    sale_id = _sale(customer_id, product_id, "MD202610070903", qty="3")
    return_id = create_order_from_typed_rows(
        customer_id, "MD202610070904",
        [{"product_id": product_id, "product_name": "回收站清理商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date="2026-10-07", order_type="return",
    )
    with get_db() as conn:
        conn.execute("UPDATE orders SET source_order_id=? WHERE id=?", (sale_id, return_id))
    # 销售单进回收站
    assert create_app().test_client().post(f"/orders/{sale_id}/delete").status_code == 302
    with get_db() as conn:
        reason = purge_order_blocker(conn, "order", sale_id)
    assert reason and "退货单引用" in reason

    page = create_app().test_client().post(f"/recycle/order/{sale_id}/purge")
    assert page.status_code == 400
    assert "退货单引用" in page.get_data(as_text=True)


def test_both_deleted_return_and_sale_can_both_be_purged():
    """引用方自己也在回收站时，两者都能清掉（顺序由多趟扫描自动处理）。"""
    init_db()
    product_id = _product(spec="BOTH-DEL")
    customer_id = create_customer("双删客户")
    sale_id = _sale(customer_id, product_id, "MD202610070905", qty="3")
    return_id = create_order_from_typed_rows(
        customer_id, "MD202610070906",
        [{"product_id": product_id, "product_name": "回收站清理商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date="2026-10-07", order_type="return",
    )
    with get_db() as conn:
        conn.execute("UPDATE orders SET source_order_id=? WHERE id=?", (sale_id, return_id))
    client = create_app().test_client()
    # 退货单先删（引用方），再删销售单
    assert client.post(f"/orders/{return_id}/delete").status_code == 302
    assert client.post(f"/orders/{sale_id}/delete").status_code == 302

    # 先单独清销售单：退货单虽已删除但仍在库里，会撞外键 → 必须是可读的 400 而不是 500
    blocked = client.post(f"/recycle/order/{sale_id}/purge")
    assert blocked.status_code == 400
    assert "回收站记录引用" in blocked.get_data(as_text=True)

    # 用清空回收站统一处理：多趟扫描先清退货单，再清销售单
    empty = client.post("/recycle/empty")
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM orders WHERE id=?", (sale_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM orders WHERE id=?", (return_id,)).fetchone() is None
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert empty.status_code == 302


def test_empty_recycle_bin_clears_orders_and_keeps_accounted_customers():
    """清空回收站：单据能清的清掉，有账务的客户保留并报出原因。"""
    init_db()
    product_id = _product(spec="EMPTY")
    customer_id = create_customer("清空客户")
    _deleted_sale(customer_id, product_id, "MD202610070907")
    client = create_app().test_client()
    client.post(f"/customers/{customer_id}/delete")

    response = client.post("/recycle/empty")
    # 单据清掉了，但客户有账务 → 部分成功，用 warning 语义的 400 呈现
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "已清空" in body and "只能保留" in body
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE deleted_at IS NOT NULL").fetchone()[0] == 0
        # 客户仍在（账务追溯底线）
        assert conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone() is not None
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_seven_day_cleanup_now_actually_removes_voided_orders():
    """七天自动清理用的是同一判断，旧版对 void 单据恒不生效 → 回收站只涨不减。"""
    init_db()
    product_id = _product(spec="AUTO")
    customer_id = create_customer("自动清理客户")
    order_id = _deleted_sale(customer_id, product_id, "MD202610070908")
    with get_db() as conn:
        conn.execute("UPDATE orders SET deleted_at='2020-01-01 00:00:00' WHERE id=?", (order_id,))
        purge_expired_recycle_bin(conn)
        assert conn.execute("SELECT 1 FROM orders WHERE id=?", (order_id,)).fetchone() is None
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_product_purge_removes_inventory_state_and_postings():
    """商品没有活单据引用时，确认删除会连库存状态/期初/流水一起清掉。"""
    init_db()
    product_id = _product(spec="PROD-DEL")
    customer_id = create_customer("商品清理客户")
    client = create_app().test_client()
    client.post(f"/products/{product_id}/delete")

    # 该商品还没有任何单据 → 可删
    response = client.post(f"/recycle/product/{product_id}/purge")
    assert response.status_code == 302
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings WHERE product_id=?", (product_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM inventory_initializations WHERE product_id=?", (product_id,)).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_product_with_live_document_is_kept_with_reason():
    """有未删除单据还在用该商品时，拒绝并说明原因。"""
    init_db()
    product_id = _product(spec="PROD-LIVE")
    customer_id = create_customer("商品活引用客户")
    _sale(customer_id, product_id, "MD202610070909")  # 活单据
    client = create_app().test_client()
    client.post(f"/products/{product_id}/delete")

    page = client.post(f"/recycle/product/{product_id}/purge")
    assert page.status_code == 400
    assert "未删除的单据还在使用" in page.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone() is not None


def test_purge_keeps_balance_correct_and_does_not_resurrect_effects():
    """永久删除只清历史痕迹，不得让已冲回的账款/库存复活。"""
    init_db()
    product_id = _product(spec="NO-REVIVE")
    customer_id = create_customer("不复活客户")
    order_id = _deleted_sale(customer_id, product_id, "MD202610070910", qty="2")
    with get_db() as conn:
        stock_before = tuple(conn.execute(
            "SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)
        ).fetchone())
    balance_before = customer_balance_cents(customer_id)

    assert create_app().test_client().post(f"/recycle/order/{order_id}/purge").status_code == 302
    with get_db() as conn:
        stock_after = tuple(conn.execute(
            "SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)
        ).fetchone())
    assert stock_after == stock_before
    assert customer_balance_cents(customer_id) == balance_before
