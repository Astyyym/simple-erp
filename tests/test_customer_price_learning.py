from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp import create_app


def test_typed_order_saves_customer_specific_price_when_price_differs_from_default():
    init_db()
    customer_id = create_customer("客户价客户")
    product_id = create_product("客户价商品", "", "个", 5000)
    create_order_from_typed_rows(customer_id, "CP-001", [{"product_name": "客户价商品", "unit": "个", "unit_price_yuan": "45", "quantity": "1"}], status="saved")
    with get_db() as conn:
        row = conn.execute("SELECT price_cents FROM customer_prices WHERE customer_id=? AND product_id=?", (customer_id, product_id)).fetchone()
    assert row is not None
    assert row["price_cents"] == 4500


def test_product_api_returns_customer_specific_price_when_customer_selected():
    init_db()
    customer_id = create_customer("客户价API")
    create_product("API商品", "", "个", 5000)
    create_order_from_typed_rows(customer_id, "CP-API", [{"product_name": "API商品", "unit": "个", "unit_price_yuan": "44", "quantity": "1"}], status="saved")
    app = create_app()
    client = app.test_client()
    response = client.get(f"/orders/api/products?q=API&customer_id={customer_id}")
    data = response.get_json()
    assert data[0]["unit_price"] == "44.00"
    assert data[0]["regular_price"] == "50.00"
    assert data[0]["customer_price"] == "44.00"
    assert data[0]["is_customer_price"] is True
