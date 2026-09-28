from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    add_adjustment,
    add_payment,
    create_customer,
    create_order_from_typed_rows,
)


def _order_form(customer_name):
    return {
        "customer_name": customer_name,
        "status": "saved",
        "product_name": ["测试商品"],
        "unit": ["个"],
        "unit_price": ["10"],
        "quantity": ["1"],
    }


def test_cross_origin_order_creation_is_rejected_without_side_effects():
    init_db()
    client = create_app().test_client()
    with get_db() as conn:
        before_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        before_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    response = client.post(
        "/orders/create",
        data=_order_form("跨站请求不应创建客户"),
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == before_customers
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == before_orders


def test_same_site_cross_origin_post_without_origin_is_rejected():
    init_db()
    response = create_app().test_client().post(
        "/orders/create",
        data=_order_form("同站跨源请求不应创建客户"),
        headers={"Sec-Fetch-Site": "same-site"},
    )

    assert response.status_code == 403
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_same_origin_browser_order_creation_is_allowed():
    init_db()
    response = create_app().test_client().post(
        "/orders/create",
        data=_order_form("同源请求客户"),
        headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 302
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_cross_origin_order_void_is_rejected():
    init_db()
    customer_id = create_customer("跨站作废订单客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "CSRF-ORDER-001",
        [{"product_name": "测试商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )

    response = create_app().test_client().post(
        f"/orders/{order_id}/void",
        data={"reason": "跨站尝试"},
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403
    with get_db() as conn:
        assert conn.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()[0] == "saved"


def test_cross_origin_payment_and_adjustment_voids_are_rejected():
    init_db()
    customer_id = create_customer("跨站作废账款客户")
    payment_id = add_payment(customer_id, 1000)
    adjustment_id = add_adjustment(customer_id, 500, "测试调整")
    client = create_app().test_client()
    headers = {"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"}

    payment_response = client.post(
        f"/accounts/payment/{payment_id}/void",
        data={"reason": "跨站尝试"},
        headers=headers,
    )
    adjustment_response = client.post(
        f"/accounts/adjustment/{adjustment_id}/void",
        data={"reason": "跨站尝试"},
        headers=headers,
    )

    assert payment_response.status_code == 403
    assert adjustment_response.status_code == 403
    with get_db() as conn:
        assert conn.execute("SELECT status FROM payments WHERE id=?", (payment_id,)).fetchone()[0] == "active"
        assert conn.execute("SELECT status FROM adjustments WHERE id=?", (adjustment_id,)).fetchone()[0] == "active"
