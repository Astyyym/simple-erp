from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp import create_app


def test_accounts_filters_customer_orders_by_date_range():
    init_db()
    customer_id = create_customer("日期筛选客户")
    create_order_from_typed_rows(customer_id, "DATE-001", [{"product_name": "阀门A", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}], status="saved", order_date="2026-07-01")
    create_order_from_typed_rows(customer_id, "DATE-002", [{"product_name": "阀门B", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}], status="saved", order_date="2026-07-10")
    app = create_app()
    client = app.test_client()
    response = client.get(f"/accounts/?customer_id={customer_id}&start_date=2026-07-05&end_date=2026-07-12")
    html = response.data.decode("utf-8")
    assert "DATE-002" in html
    assert "DATE-001" not in html
