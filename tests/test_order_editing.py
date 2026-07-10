from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, customer_balance_cents, update_order_from_typed_rows


def test_update_order_replaces_items_and_recalculates_balance():
    init_db()
    customer_id = create_customer("编辑客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "EDIT-001",
        [{"product_name": "旧商品", "unit": "个", "unit_price_yuan": "10", "quantity": "2"}],
        status="saved",
        order_date="2026-07-10",
    )
    assert customer_balance_cents(customer_id) == 2000
    update_order_from_typed_rows(
        order_id,
        customer_id,
        "EDIT-001",
        [{"product_name": "新商品", "unit": "套", "unit_price_yuan": "30", "quantity": "3"}],
        status="saved",
        order_date="2026-07-10",
    )
    assert customer_balance_cents(customer_id) == 9000
    with get_db() as conn:
        items = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    assert len(items) == 1
    assert items[0]["product_name"] == "新商品"
    assert items[0]["unit"] == "套"
