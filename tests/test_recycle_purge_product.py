from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.routes.recycle import _purge_one


def test_purge_deleted_product_clears_order_item_reference_without_losing_snapshot():
    init_db()
    customer_id = create_customer("回收站客户")
    product_id = create_product("回收站商品", "", "个", 1200)
    order_id = create_order_from_typed_rows(customer_id, "REC-001", [{"product_name": "回收站商品", "unit": "个", "unit_price_yuan": "12", "quantity": "1"}], status="saved")
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (product_id,))
        _purge_one(conn, "product", product_id)
        product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        item = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchone()
    assert product is None
    assert item["product_id"] is None
    assert item["product_name"] == "回收站商品"
