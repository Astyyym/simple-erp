from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp import create_app


def test_new_order_page_uses_next_available_order_number_when_first_exists():
    init_db()
    customer_id = create_customer("单号客户")
    create_order_from_typed_rows(customer_id, "20260710-001", [{"product_name": "阀门", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved", order_date="2026-07-10")
    app = create_app()
    client = app.test_client()
    html = client.get("/orders/new?date=2026-07-10").data.decode("utf-8")
    assert "20260710-002" in html


def test_duplicate_order_number_post_does_not_500_and_auto_advances():
    init_db()
    customer_id = create_customer("重复单号客户")
    create_order_from_typed_rows(customer_id, "DUP-001", [{"product_name": "阀门", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved")
    app = create_app()
    client = app.test_client()
    response = client.post("/orders/create", data={
        "customer_id": str(customer_id),
        "customer_name": "重复单号客户",
        "order_no": "DUP-001",
        "order_date": "2026-07-10",
        "status": "saved",
        "product_name": ["阀门2"],
        "unit": ["个"],
        "unit_price": ["2"],
        "quantity": ["1"],
    })
    assert response.status_code == 302
    with get_db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_no='DUP-002'").fetchone()
    assert row is not None
