from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, customer_balance_cents


def test_typed_order_rows_create_product_dictionary_and_snapshot_editable_fields():
    init_db()
    customer_id = create_customer("输入客户")
    order_id = create_order_from_typed_rows(
        customer_id=customer_id,
        order_no="TYPE-001",
        rows=[{"product_name": "A商品", "unit": "个", "unit_price_yuan": "50", "quantity": "2"}],
        status="saved",
        order_date="2026-07-10",
    )
    assert customer_balance_cents(customer_id) == 10000
    with get_db() as conn:
        product = conn.execute("SELECT * FROM products WHERE name='A商品'").fetchone()
        item = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchone()
    assert product is not None
    assert product["unit"] == "个"
    # 决策变更（2026-10-15）：档案不再保存售价，售价唯一来源 = 客户专属价（见下）。
    # 2026-10-16（决策 ③/schema v15）：档案里的价格列本身也已随迁移物理删除，
    # 因此这里不再能（也不必）断言档案价，改由下面的客户专属价断言守住售价口径。
    with get_db() as conn:
        learned = conn.execute(
            "SELECT price_cents FROM customer_prices WHERE customer_id=? AND product_id=?",
            (customer_id, product["id"]),
        ).fetchone()
    assert learned["price_cents"] == 5000
    assert item["product_name"] == "A商品"
    assert item["unit"] == "个"
    assert item["unit_price_cents"] == 5000


def test_typed_order_row_reuses_existing_product_and_allows_unit_price_override():
    init_db()
    customer_id = create_customer("复购客户")
    create_order_from_typed_rows(customer_id, "TYPE-002", [{"product_name": "A商品", "unit": "个", "unit_price_yuan": "50", "quantity": "1"}], status="saved")
    create_order_from_typed_rows(customer_id, "TYPE-003", [{"product_name": "A商品", "unit": "箱", "unit_price_yuan": "45", "quantity": "1"}], status="saved")
    with get_db() as conn:
        products = conn.execute("SELECT * FROM products WHERE name='A商品'").fetchall()
        last_item = conn.execute("SELECT * FROM order_items ORDER BY id DESC LIMIT 1").fetchone()
    assert len(products) == 1
    assert products[0]["unit"] == "箱"
    # 档案不保存售价（列已随 schema v15 删除）；第二单的 45 元覆盖为新的客户专属价。
    with get_db() as conn:
        customer_price = conn.execute("SELECT price_cents FROM customer_prices WHERE customer_id=? AND product_id=?", (customer_id, products[0]["id"])).fetchone()
    assert customer_price["price_cents"] == 4500
    assert last_item["unit"] == "箱"
    assert last_item["unit_price_cents"] == 4500
    assert customer_balance_cents(customer_id) == 9500
