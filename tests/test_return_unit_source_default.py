"""Phase 4 / S5 — 退货单（销售来源带入）单位默认沿用源销售单快照（§六 #20）。

决策（2026-10-13 用户拍板）：退货单从销售单带入时，明细单位**优先沿用源销售单快照单位**，
同时允许用户修改（改本单明细，不回写商品档案）。
"""
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer, create_product, create_order_from_typed_rows,
    create_return_order_from_source,
)
from erp.services.inventory import initialize_product


def _seed():
    init_db()
    cid = create_customer("退货单位客户")
    pid = create_product("退货单位商品", "个", 5000)
    # 退货要求源销售明细有可靠成本 → 商品必须先启用库存（期初入库）。
    initialize_product(pid, "100", "50", "2026-10-01", "期初", "p4-ret-init")
    return cid, pid


def _sale_with_unit(cid, pid, unit, order_no="MD202610130200"):
    return create_order_from_typed_rows(
        cid, order_no,
        [{"product_id": str(pid), "product_name": "退货单位商品", "unit": unit,
          "unit_price_yuan": "50", "quantity": "10"}],
        status="saved", order_date="2026-10-13",
    )


def test_return_source_bound_defaults_to_source_sale_unit():
    """不提交 unit → 退货明细沿用源销售单快照单位「台」。"""
    cid, pid = _seed()
    sale_id = _sale_with_unit(cid, pid, "台")
    with get_db() as conn:
        src_item = conn.execute(
            "SELECT id, unit FROM order_items WHERE order_id=?", (sale_id,)
        ).fetchone()
    assert src_item["unit"] == "台"

    create_return_order_from_source(
        cid, "MD202610130201", sale_id,
        [{"source_item_id": src_item["id"], "quantity": "2"}],
        status="saved", order_date="2026-10-13", request_key="p4-ret-default",
    )
    with get_db() as conn:
        ret_item = conn.execute(
            "SELECT unit FROM order_items WHERE order_id IN "
            "(SELECT id FROM orders WHERE source_order_id=?)", (sale_id,)
        ).fetchone()
    assert ret_item["unit"] == "台", "退货默认应沿用源销售单快照单位"


def test_return_source_bound_allows_unit_override():
    """提交不同 unit → 以提交值为准（但仍不回写商品档案）。"""
    cid, pid = _seed()
    sale_id = _sale_with_unit(cid, pid, "台", order_no="MD202610130210")
    with get_db() as conn:
        src_item = conn.execute(
            "SELECT id FROM order_items WHERE order_id=?", (sale_id,)
        ).fetchone()

    create_return_order_from_source(
        cid, "MD202610130211", sale_id,
        [{"source_item_id": src_item["id"], "quantity": "1", "unit": "箱"}],
        status="saved", order_date="2026-10-13", request_key="p4-ret-override",
    )
    with get_db() as conn:
        ret_item = conn.execute(
            "SELECT unit FROM order_items WHERE order_id IN "
            "(SELECT id FROM orders WHERE source_order_id=?)", (sale_id,)
        ).fetchone()
    assert ret_item["unit"] == "箱", "退货单允许覆盖源单单位"
