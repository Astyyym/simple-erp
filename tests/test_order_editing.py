from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, customer_balance_cents, update_order_from_typed_rows
from erp import create_app


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


def test_return_order_edit_keeps_negative_total_and_balance():
    init_db()
    customer_id = create_customer("退货重编辑客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "RETURN-EDIT-001",
        [{"product_name": "退回阀门", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_date="2026-09-25",
        order_type="return",
    )
    assert customer_balance_cents(customer_id) == -2000

    response = create_app().test_client().post(
        f"/orders/{order_id}/edit",
        data={
            "customer_id": str(customer_id),
            "customer_name": "退货重编辑客户",
            "status": "saved",
            "product_name": ["退回阀门"],
            "unit": ["个"],
            "unit_price": ["30"],
            "quantity": ["1"],
            "notes": "",
        },
    )

    assert response.status_code == 302
    assert customer_balance_cents(customer_id) == -3000
    with get_db() as conn:
        order = conn.execute("SELECT order_type, total_amount_cents FROM orders WHERE id=?", (order_id,)).fetchone()
    assert order["order_type"] == "return"
    assert order["total_amount_cents"] == -3000


def test_return_order_create_review_edit_review_flow_keeps_negative_amount():
    init_db()
    client = create_app().test_client()

    created = client.post(
        "/orders/return/create",
        data={
            "customer_name": "退货用户路径客户",
            "status": "saved",
            "product_name": ["消防阀门"],
            "unit": ["个"],
            "unit_price": ["20"],
            "quantity": ["1"],
        },
    )
    assert created.status_code == 302
    with get_db() as conn:
        order = conn.execute(
            "SELECT o.id, o.customer_id FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.name=?",
            ("退货用户路径客户",),
        ).fetchone()
    order_id = order["id"]
    customer_id = order["customer_id"]

    first_review = client.get(f"/orders/{order_id}").get_data(as_text=True)
    assert first_review.count("-20.00") >= 2
    assert customer_balance_cents(customer_id) == -2000

    edited = client.post(
        f"/orders/{order_id}/edit",
        data={
            "customer_id": str(customer_id),
            "customer_name": "退货用户路径客户",
            "status": "saved",
            "product_name": ["消防阀门"],
            "unit": ["个"],
            "unit_price": ["30"],
            "quantity": ["1"],
        },
    )
    assert edited.status_code == 302
    second_review = client.get(f"/orders/{order_id}").get_data(as_text=True)
    assert second_review.count("-30.00") >= 2
    assert customer_balance_cents(customer_id) == -3000


def test_service_edit_reads_existing_return_type_when_caller_omits_type():
    init_db()
    customer_id = create_customer("服务层退货客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "RETURN-SERVICE-001",
        [{"product_name": "退回喷头", "unit": "个", "unit_price_yuan": "12", "quantity": "1"}],
        status="saved",
        order_type="return",
    )

    update_order_from_typed_rows(
        order_id,
        customer_id,
        "RETURN-SERVICE-001",
        [{"product_name": "退回喷头", "unit": "个", "unit_price_yuan": "15", "quantity": "1"}],
        status="saved",
        order_date="2026-09-25",
    )

    assert customer_balance_cents(customer_id) == -1500
