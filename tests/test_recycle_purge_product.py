from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.routes.recycle import _purge_one
import pytest


def test_purge_deleted_product_clears_order_item_reference_without_losing_snapshot():
    """2026-10-07 起：商品没有**未删除**单据引用时即可永久删除。

    这里造的是「单据还在但商品已删」——旧版允许，新版改为要求单据先删
    （否则活单据会失去商品指向）。所以先把单据删掉再清商品。
    （2026-10-16 schema v15 起不再有库存状态/期初/流水可清。）
    """
    init_db()
    customer_id = create_customer("回收站客户")
    product_id = create_product("回收站商品", "个")
    order_id = create_order_from_typed_rows(customer_id, "REC-001", [{"product_name": "回收站商品", "unit": "个", "unit_price_yuan": "12", "quantity": "1"}], status="saved")
    # 先把引用它的单据删掉，商品才满足「无活引用」。
    assert create_app().test_client().post(f"/orders/{order_id}/delete").status_code == 302
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (product_id,))
        _purge_one(conn, "product", product_id)
        product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        item = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchone()
    assert product is None
    # 历史明细保留商品名快照（快照字段独立于商品字典）。
    assert item["product_name"] == "回收站商品"


def test_product_purge_is_blocked_while_live_document_references_it():
    """有未删除单据还在用该商品时，必须保留并说明原因。

    （2026-10-16 自 `test_inventory_initialization.py` 迁入：该断言本就与库存无关，
    原文件随库存系统整体退休。）
    """
    init_db()
    product_id = create_product("活引用商品", "个")
    customer_id = create_customer("活引用客户")
    create_order_from_typed_rows(
        customer_id, "MD202610070001",
        [{"product_id": product_id, "product_name": "活引用商品", "unit": "个",
          "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-07",
    )
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=datetime('now', '-8 days') WHERE id=?", (product_id,))
        with pytest.raises(ValueError, match="未删除的单据还在使用"):
            _purge_one(conn, "product", product_id)
        assert conn.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone() is not None
