"""Phase 4 / S2 — 缺陷 C：拿货/退拿货侧单位输入。

决策（计划 §4.3 / §六 #16 + #20）：
- 拿货/退拿货明细**可提交单位**，只作用于本单明细，**不回写 `products.unit`**。
- 拿货（无源单）：提交空则回落商品档案单位。
- 退拿货（源绑定）：**默认沿用源拿货单快照单位**，允许表单覆盖。
- 手工退拿货（decoupled）：无源单，回落商品档案单位。

RED 预期：当前 `_prepared_purchase_items` 完全不接 `unit`，明细 unit 一律抄档案。
"""
import pytest

from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product
from erp.services.purchases import create_purchase_order
from erp.services import purchase_returns


def _seed(product_name="拿货单位商品", unit="个", price=2000):
    init_db()
    cid = create_customer("拿货单位对象")
    pid = create_product(product_name, unit)

    return cid, pid


def test_purchase_submitted_unit_written_to_items_and_not_back_to_archive():
    """拿货提交 unit=台 → 明细写「台」，商品档案**保持**「个」。"""
    cid, pid = _seed()
    create_purchase_order(
        "NH202610130001", "2026-10-13",
        [{"product_id": pid, "quantity": "2", "unit_cost_yuan": "20", "unit": "台"}],
        customer_id=cid, request_key="p4-purchase-unit-1", status="saved",
    )
    with get_db() as conn:
        item = conn.execute("SELECT unit FROM purchase_order_items ORDER BY id DESC LIMIT 1").fetchone()
        product = conn.execute("SELECT unit FROM products WHERE id=?", (pid,)).fetchone()
    assert item["unit"] == "台", "拿货明细必须采纳提交的单位"
    assert product["unit"] == "个", "拿货路径不得回写商品档案单位"


def test_purchase_empty_unit_falls_back_to_archive():
    """拿货提交空 unit → 回落商品档案单位。"""
    cid, pid = _seed("拿货空单位商品", "套", 2000)
    create_purchase_order(
        "NH202610130002", "2026-10-13",
        [{"product_id": pid, "quantity": "1", "unit_cost_yuan": "20", "unit": ""}],
        customer_id=cid, request_key="p4-purchase-unit-2", status="saved",
    )
    with get_db() as conn:
        item = conn.execute("SELECT unit FROM purchase_order_items ORDER BY id DESC LIMIT 1").fetchone()
    assert item["unit"] == "套"


def test_purchase_custom_unit_does_not_change_line_amounts():
    """§4.6：拿货改单位不做任何数量/金额换算。"""
    cid, pid = _seed("拿货单价单位商品", "个", 2000)
    create_purchase_order(
        "NH202610130003", "2026-10-13",
        [{"product_id": pid, "quantity": "5", "unit_cost_yuan": "20", "unit": "箱"}],
        customer_id=cid, request_key="p4-purchase-unit-3", status="saved",
    )
    with get_db() as conn:
        item = conn.execute(
            "SELECT unit, quantity_3dp, unit_cost_cents, subtotal_cents FROM purchase_order_items ORDER BY id DESC LIMIT 1"
        ).fetchone()
    # 单位「箱」只作为本单明细标签，数量 5 与金额 5×20=100 元逐字不变（无换算系数）。
    assert tuple(item) == ("箱", 5000, 2000, 10000)


def test_purchase_return_source_bound_keeps_source_unit_and_allows_override():
    """退拿货（源绑定）默认沿用源拿货单快照单位，且允许覆盖。"""
    cid, pid = _seed("退拿货单位商品", "个", 2000)
    # 先建一张拿货单，明细单位是「台」
    create_purchase_order(
        "NH202610130010", "2026-10-13",
        [{"product_id": pid, "quantity": "10", "unit_cost_yuan": "20", "unit": "台"}],
        customer_id=cid, request_key="p4-pr-source", status="saved",
    )
    with get_db() as conn:
        src = conn.execute("SELECT id FROM purchase_orders WHERE order_no='NH202610130010'").fetchone()
        src_item = conn.execute(
            "SELECT id FROM purchase_order_items WHERE purchase_order_id=?", (src["id"],)
        ).fetchone()

    # 默认：不传 unit → 沿用源单快照「台」
    purchase_returns.create(
        customer_id=cid, business_date="2026-10-13",
        items=[{"source_item_id": src_item["id"], "quantity": "2"}],
        request_key="p4-pr-default", status="saved", source_order_id=src["id"],
    )
    with get_db() as conn:
        default_unit = conn.execute(
            "SELECT unit FROM purchase_return_items ORDER BY id DESC LIMIT 1"
        ).fetchone()["unit"]
    assert default_unit == "台", "退拿货默认应沿用源拿货单快照单位"

    # 覆盖：传 unit=箱 → 以提交值为准
    purchase_returns.create(
        customer_id=cid, business_date="2026-10-13",
        items=[{"source_item_id": src_item["id"], "quantity": "1", "unit": "箱"}],
        request_key="p4-pr-override", status="saved", source_order_id=src["id"],
    )
    with get_db() as conn:
        override_unit = conn.execute(
            "SELECT unit FROM purchase_return_items ORDER BY id DESC LIMIT 1"
        ).fetchone()["unit"]
        product_unit = conn.execute("SELECT unit FROM products WHERE id=?", (pid,)).fetchone()["unit"]
    assert override_unit == "箱", "退拿货允许覆盖源单单位"
    assert product_unit == "个", "退拿货不得回写商品档案单位"
