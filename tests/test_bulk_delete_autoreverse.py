"""列表删除正式单据 = 自动冲回（作废）+ 软删除的行为回归。

设计契约（用户已确认）：入口与文案按「正式单据可直接删除」实现，但底下的守恒机制必须保留——
正式单据被删除前先走 ``void_order`` / ``void_purchase_order`` / ``purchase_returns.void`` 作废，
再软删除进回收站。v15（2026-10-16 决策 ②/③）库存系统已删除，作废不再反向过账库存；
本文件改为验证「作废状态机 + 账款回到删除前的数值 + 不静默跳过任何一张单据」这三点。
"""
import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    AUTO_REVERSE_REASON,
    create_customer,
    create_order_from_typed_rows,
    create_product,
    customer_balance_cents,
)
from erp.services import purchase_returns
from erp.services.purchases import create_purchase_order


def _product(name="自动冲回商品", spec="AR"):
    product_id = create_product((name) + (spec), "个")

    return product_id


def _order_row(order_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT status, void_reason, deleted_at, delete_reason FROM orders WHERE id=?", (order_id,)
        ).fetchone()


def test_saved_sale_delete_voids_and_reverses_balance_then_soft_deletes():
    init_db()
    product_id = _product(spec="AR-SALE")
    customer_id = create_customer("自动冲回销售客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070101",
        [{"product_id": product_id, "product_name": "自动冲回商品AR-SALE", "unit": "个",
          "unit_price_yuan": "30", "quantity": "3"}],
        status="saved",
        order_date="2026-10-07",
    )
    assert customer_balance_cents(customer_id) == 9000  # 3 × 30 元

    response = create_app().test_client().post(f"/orders/{order_id}/delete")
    assert response.status_code == 302

    row = _order_row(order_id)
    assert row["status"] == "void"
    assert row["void_reason"] == AUTO_REVERSE_REASON
    assert row["deleted_at"] and row["delete_reason"] == AUTO_REVERSE_REASON
    # 账款回到删除前（= 该销售单不存在时的数值）：作废后不再计入余额。
    assert customer_balance_cents(customer_id) == 0


def test_saved_purchase_delete_voids_and_reverses_balance_then_soft_deletes():
    init_db()
    product_id = _product(spec="AR-PUR")
    customer_id = create_customer("自动冲回拿货对象")
    purchase = create_purchase_order(
        "NH202610070101",
        "2026-10-07",
        [{"product_id": product_id, "quantity": "5", "unit_cost_yuan": "12"}],
        customer_id=customer_id,
        request_key="ar-purchase-1",
    )
    purchase_id = purchase["order_id"]
    balance_after_purchase = customer_balance_cents(customer_id)  # 拿货减少对账余额

    response = create_app().test_client().post(f"/purchases/{purchase_id}/delete")
    assert response.status_code == 302

    with get_db() as conn:
        row = conn.execute(
            "SELECT status, void_reason, deleted_at FROM purchase_orders WHERE id=?", (purchase_id,)
        ).fetchone()
    assert row["status"] == "void" and row["void_reason"] == AUTO_REVERSE_REASON and row["deleted_at"]
    # 作废后这张拿货单不再影响余额（回到「没这张单」的值）。
    assert balance_after_purchase != 0
    assert customer_balance_cents(customer_id) == 0


def test_saved_purchase_return_delete_voids_and_soft_deletes():
    init_db()
    product_id = _product(spec="AR-RET")
    customer_id = create_customer("自动冲回退拿货对象")
    purchase = create_purchase_order(
        "NH202610070201",
        "2026-10-07",
        [{"product_id": product_id, "quantity": "4", "unit_cost_yuan": "10"}],
        customer_id=customer_id,
        request_key="ar-return-source",
    )
    with get_db() as conn:
        source_item_id = conn.execute(
            "SELECT id FROM purchase_order_items WHERE purchase_order_id=?", (purchase["order_id"],)
        ).fetchone()[0]
    balance_before_return = customer_balance_cents(customer_id)
    returned = purchase_returns.create(
        customer_id=customer_id,
        source_order_id=purchase["order_id"],
        business_date="2026-10-07",
        items=[{"source_item_id": source_item_id, "quantity": "1"}],
        request_key="ar-return-1",
    )
    return_id = returned["order_id"]
    balance_with_return = customer_balance_cents(customer_id)
    assert balance_with_return != balance_before_return, "退拿货单要影响对账余额"

    response = create_app().test_client().post(
        f"/purchases/return/{return_id}/delete", data={"version": str(returned["version"])}
    )
    assert response.status_code == 302

    with get_db() as conn:
        row = conn.execute(
            "SELECT status, void_reason, deleted_at FROM purchase_return_orders WHERE id=?", (return_id,)
        ).fetchone()
    assert row["status"] == "void" and row["deleted_at"]
    # 作废的退拿货单不再计入余额：回到「这笔退货不存在」时的值（源拿货单本身的影响保留）。
    assert customer_balance_cents(customer_id) == balance_before_return


def test_bulk_delete_mixes_types_and_keeps_recycle_bin_and_balance_consistent():
    init_db()
    sale_product = _product(name="批量销售商品", spec="AR-B1")
    purchase_product = _product(name="批量拿货商品", spec="AR-B2")
    customer_id = create_customer("批量删除客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070301",
        [{"product_id": sale_product, "product_name": "批量销售商品AR-B1", "unit": "个",
          "unit_price_yuan": "30", "quantity": "2"}],
        status="saved",
        order_date="2026-10-07",
    )
    purchase = create_purchase_order(
        "NH202610070301",
        "2026-10-07",
        [{"product_id": purchase_product, "quantity": "3", "unit_cost_yuan": "10"}],
        customer_id=customer_id,
        request_key="ar-bulk-purchase",
    )
    draft_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070302",
        [{"product_name": "批量草稿商品", "unit": "个", "unit_price_yuan": "5", "quantity": "1"}],
        status="draft",
        order_date="2026-10-07",
    )

    response = create_app().test_client().post(
        "/orders/bulk_delete",
        data={"ids": [f"sale:{sale_id}", f"purchase:{purchase['order_id']}", f"sale:{draft_id}"]},
    )
    assert response.status_code == 302

    assert _order_row(sale_id)["status"] == "void"
    with get_db() as conn:
        purchase_row = conn.execute(
            "SELECT status, deleted_at FROM purchase_orders WHERE id=?", (purchase["order_id"],)
        ).fetchone()
        draft_row = conn.execute(
            "SELECT status, void_reason, deleted_at FROM orders WHERE id=?", (draft_id,)
        ).fetchone()
    assert purchase_row["status"] == "void" and purchase_row["deleted_at"]
    # 草稿不是正式单：直接进回收站，不写自动冲回原因。
    assert draft_row["status"] == "draft" and draft_row["void_reason"] in (None, "") and draft_row["deleted_at"]
    assert customer_balance_cents(customer_id) == 0

    # 三条都在回收站可见。
    recycle_html = create_app().test_client().get("/recycle/").get_data(as_text=True)
    for order_no in ("MD202610070301", "NH202610070301", "MD202610070302"):
        assert order_no in recycle_html, order_no


def test_delete_saved_sale_reverses_only_its_own_balance_and_keeps_others():
    """删除一张正式销售单：只回冲它自己的账款影响，同商品上的其它单据一律不动。

    旧用例的前提是「库存按序过账、删除非末笔单要按序反向冲回，移动均价可能漂」——
    库存系统与成本链已整体删除（2026-10-16 决策 ②/③），改为钉住仍然成立的那部分：
    作废+软删除后账款精确回到「这笔销售不存在」时的值，同商品的拿货单状态不受影响。
    """
    init_db()
    product_id = _product(spec="AR-LAST")
    customer_id = create_customer("末笔校验客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070401",
        [{"product_id": product_id, "product_name": "自动冲回商品AR-LAST", "unit": "个",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-10-07",
    )
    # 之后又有一笔同商品拿货 → 销售单不再是该商品末笔库存业务。
    create_purchase_order(
        "NH202610070401",
        "2026-10-07",
        [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "10"}],
        customer_id=customer_id,
        request_key="ar-last-purchase",
    )
    balance_after_sale = customer_balance_cents(customer_id)
    with get_db() as conn:
        purchase_before = conn.execute(
            "SELECT status, deleted_at FROM purchase_orders WHERE order_no='NH202610070401'"
        ).fetchone()
    assert purchase_before["status"] == "saved" and purchase_before["deleted_at"] is None

    response = create_app().test_client().post(f"/orders/{sale_id}/delete")
    assert response.status_code == 302

    row = _order_row(sale_id)
    assert row["status"] == "void" and row["deleted_at"] is not None
    # 账款必须精确回到「这笔销售不存在」时的值（只回冲这张单的 3000 分）。
    assert customer_balance_cents(customer_id) == balance_after_sale - 3000
    # 同一商品上的拿货单完全不受影响。
    with get_db() as conn:
        purchase_after = conn.execute(
            "SELECT status, deleted_at FROM purchase_orders WHERE order_no='NH202610070401'"
        ).fetchone()
    assert purchase_after["status"] == "saved" and purchase_after["deleted_at"] is None


def test_bulk_delete_mixes_reversible_and_draft_rows_without_skipping_any():
    """批量删除里正式单据与草稿都照常处理，草稿直接进回收站，没有任何行被静默跳过。"""
    init_db()
    product_id = _product(spec="AR-MIX")
    customer_id = create_customer("批量失败客户")
    blocked_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070501",
        [{"product_id": product_id, "product_name": "自动冲回商品AR-MIX", "unit": "个",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-10-07",
    )
    create_purchase_order(
        "NH202610070501",
        "2026-10-07",
        [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "10"}],
        customer_id=customer_id,
        request_key="ar-mix-purchase",
    )
    draft_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070502",
        [{"product_name": "批量草稿商品", "unit": "个", "unit_price_yuan": "5", "quantity": "1"}],
        status="draft",
        order_date="2026-10-07",
    )

    response = create_app().test_client().post(
        "/orders/bulk_delete", data={"ids": [f"sale:{blocked_id}", f"sale:{draft_id}"]}
    )
    assert response.status_code == 302
    # 两张都进回收站；正式那张已被自动冲回。
    assert _order_row(blocked_id)["status"] == "void"
    assert _order_row(blocked_id)["deleted_at"] is not None
    assert _order_row(draft_id)["deleted_at"] is not None
