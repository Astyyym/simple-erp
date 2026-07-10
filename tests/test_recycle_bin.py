from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product, customer_balance_cents
from erp.utils.money import yuan_to_cents


def test_deleted_order_moves_to_recycle_and_is_excluded_from_balance(client_app=None):
    init_db()
    customer_id = create_customer("删除测试客户")
    product_id = create_product("删除测试商品", "", "个", 1000)
    order_id = create_order_from_typed_rows(customer_id, "DEL-001", [{"product_name": "删除测试商品", "unit": "个", "unit_price_yuan": "10", "quantity": "2"}], status="saved")
    assert customer_balance_cents(customer_id) == 2000
    with get_db() as conn:
        conn.execute("UPDATE orders SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
    assert customer_balance_cents(customer_id) == 0
    with get_db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE deleted_at IS NOT NULL").fetchone()
    assert row["order_no"] == "DEL-001"
