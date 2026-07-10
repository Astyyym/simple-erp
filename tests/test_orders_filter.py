from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp import create_app


def test_orders_list_filters_by_customer_and_date_independently():
    init_db()
    customer_a = create_customer("筛选甲")
    customer_b = create_customer("筛选乙")
    create_order_from_typed_rows(customer_a, "O-A-OLD", [{"product_name": "A", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved", order_date="2026-07-01")
    create_order_from_typed_rows(customer_a, "O-A-NEW", [{"product_name": "A", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved", order_date="2026-07-10")
    create_order_from_typed_rows(customer_b, "O-B-NEW", [{"product_name": "B", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved", order_date="2026-07-10")
    app = create_app()
    client = app.test_client()
    html = client.get("/orders/?customer=筛选甲&start_date=2026-07-05&end_date=2026-07-12").data.decode("utf-8")
    assert "O-A-NEW" in html
    assert "O-A-OLD" not in html
    assert "O-B-NEW" not in html
