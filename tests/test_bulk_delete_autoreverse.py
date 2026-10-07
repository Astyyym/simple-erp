"""列表删除正式单据 = 自动冲回（作废）+ 软删除的行为回归。

设计契约（用户已确认）：入口与文案按「正式单据可直接删除」实现，但底下的守恒机制必须保留——
正式单据被删除前先走 ``void_order`` / ``void_purchase_order`` / ``purchase_returns.void`` 反向过账，
再软删除进回收站。本文件验证冲回后库存与账款回到删除前的数值，且拒绝场景会以可见错误返回，
不会静默跳过。
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
from erp.services.inventory import create_purchase_order, initialize_product


def _product(name="自动冲回商品", spec="AR"):
    product_id = create_product(name, spec, "个", 2000)
    initialize_product(product_id, "10", "10", "2026-10-01", "系统上线期初", f"ar-init-{spec}", confirm_zero=False)
    return product_id


def _stock(product_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
    return (int(row["quantity_3dp"]), int(row["cost_total_micro"]))


def _order_row(order_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT status, void_reason, deleted_at, delete_reason FROM orders WHERE id=?", (order_id,)
        ).fetchone()


def test_saved_sale_delete_reverses_stock_and_balance_then_soft_deletes():
    init_db()
    product_id = _product(spec="AR-SALE")
    customer_id = create_customer("自动冲回销售客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070101",
        [{"product_id": product_id, "product_name": "自动冲回商品", "spec": "AR-SALE", "unit": "个",
          "unit_price_yuan": "30", "quantity": "3"}],
        status="saved",
        order_date="2026-10-07",
    )
    after_sale = _stock(product_id)
    after_sale_balance = customer_balance_cents(customer_id)
    assert after_sale == (7000, 70_000_000)
    assert after_sale_balance == 9000

    response = create_app().test_client().post(f"/orders/{order_id}/delete")
    assert response.status_code == 302

    row = _order_row(order_id)
    assert row["status"] == "void"
    assert row["void_reason"] == AUTO_REVERSE_REASON
    assert row["deleted_at"] and row["delete_reason"] == AUTO_REVERSE_REASON
    # 库存与账款回到删除前（= 该销售单不存在时的数值）。
    assert _stock(product_id) == (10000, 100_000_000)
    assert customer_balance_cents(customer_id) == 0


def test_saved_purchase_delete_reverses_stock_and_soft_deletes():
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
    assert _stock(product_id) == (15000, 160_000_000)

    response = create_app().test_client().post(f"/purchases/{purchase_id}/delete")
    assert response.status_code == 302

    with get_db() as conn:
        row = conn.execute(
            "SELECT status, void_reason, deleted_at FROM purchase_orders WHERE id=?", (purchase_id,)
        ).fetchone()
    assert row["status"] == "void" and row["void_reason"] == AUTO_REVERSE_REASON and row["deleted_at"]
    assert _stock(product_id) == (10000, 100_000_000)


def test_saved_purchase_return_delete_reverses_outbound_postings():
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
    returned = purchase_returns.create(
        customer_id=customer_id,
        source_order_id=purchase["order_id"],
        business_date="2026-10-07",
        items=[{"source_item_id": source_item_id, "quantity": "1"}],
        request_key="ar-return-1",
    )
    return_id = returned["order_id"]
    assert _stock(product_id) == (13000, 130_000_000)

    response = create_app().test_client().post(
        f"/purchases/return/{return_id}/delete", data={"version": str(returned["version"])}
    )
    assert response.status_code == 302

    with get_db() as conn:
        row = conn.execute(
            "SELECT status, void_reason, deleted_at FROM purchase_return_orders WHERE id=?", (return_id,)
        ).fetchone()
    assert row["status"] == "void" and row["deleted_at"]
    assert _stock(product_id) == (14000, 140_000_000)


def test_bulk_delete_mixes_all_four_types_and_stays_conserved():
    init_db()
    sale_product = _product(name="批量销售商品", spec="AR-B1")
    purchase_product = _product(name="批量拿货商品", spec="AR-B2")
    customer_id = create_customer("批量删除客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070301",
        [{"product_id": sale_product, "product_name": "批量销售商品", "spec": "AR-B1", "unit": "个",
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
    assert _stock(sale_product) == (10000, 100_000_000)
    with get_db() as conn:
        purchase_row = conn.execute(
            "SELECT status, deleted_at FROM purchase_orders WHERE id=?", (purchase["order_id"],)
        ).fetchone()
        draft_row = conn.execute(
            "SELECT status, void_reason, deleted_at FROM orders WHERE id=?", (draft_id,)
        ).fetchone()
    assert purchase_row["status"] == "void" and purchase_row["deleted_at"]
    assert _stock(purchase_product) == (10000, 100_000_000)
    # 草稿没有过账：直接进回收站，不写自动冲回原因。
    assert draft_row["status"] == "draft" and draft_row["void_reason"] in (None, "") and draft_row["deleted_at"]
    assert customer_balance_cents(customer_id) == 0

    # 三条都在回收站可见。
    recycle_html = create_app().test_client().get("/recycle/").get_data(as_text=True)
    for order_no in ("MD202610070301", "NH202610070301", "MD202610070302"):
        assert order_no in recycle_html, order_no


def test_delete_allows_non_last_inventory_posting_and_reverses_what_it_can():
    """2026-10-07 用户决策：非末笔也允许删除。

    反向过账后库存数量、销售金额、账款余额精确回到删除前的正确值；只有移动均价会漂
    （移动加权平均固有性质，成本不进对外打印）。这里锁定「数量/账款精确、均价可能漂」这一契约。
    """
    init_db()
    product_id = _product(spec="AR-LAST")
    customer_id = create_customer("末笔校验客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070401",
        [{"product_id": product_id, "product_name": "自动冲回商品", "spec": "AR-LAST", "unit": "个",
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
    qty_after_sale = _stock(product_id)[0]

    response = create_app().test_client().post(f"/orders/{sale_id}/delete")
    assert response.status_code == 302

    row = _order_row(sale_id)
    assert row["status"] == "void" and row["deleted_at"] is not None
    # 数量与账款必须精确回到「这笔销售不存在」时的值。
    assert _stock(product_id)[0] == qty_after_sale + 1000
    assert customer_balance_cents(customer_id) == balance_after_sale - 3000


def test_bulk_delete_mixes_reversible_and_draft_rows_without_skipping_any():
    """批量删除里正式单据与非末笔单据都照常处理，草稿直接进回收站，没有任何行被静默跳过。"""
    init_db()
    product_id = _product(spec="AR-MIX")
    customer_id = create_customer("批量失败客户")
    blocked_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070501",
        [{"product_id": product_id, "product_name": "自动冲回商品", "spec": "AR-MIX", "unit": "个",
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
