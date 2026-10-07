from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.routes.recycle import _purge_one


def test_purge_deleted_product_clears_order_item_reference_without_losing_snapshot():
    """2026-10-07 起：商品没有**未删除**单据引用时即可永久删除，连带库存状态/期初/流水。

    这里造的是「单据还在但商品已删」——旧版允许，新版改为要求单据先删（否则活单据会
    失去商品指向，而 `void_order` 依赖库存流水做反向过账）。所以先把单据删掉再清商品。
    """
    init_db()
    customer_id = create_customer("回收站客户")
    product_id = create_product("回收站商品", "", "个", 1200)
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
