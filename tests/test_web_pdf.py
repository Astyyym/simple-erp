from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_product, create_order


def test_health_endpoint_ok():
    init_db()
    app = create_app()
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"
    assert response.json["database"] == "ok"


def test_order_pdf_endpoint_generates_real_pdf():
    init_db()
    customer_id = create_customer("PDF客户")
    product_id = create_product("测试阀门", "DN20", "个", 1234)
    order_id = create_order(customer_id, "PDF-001", [{"product_id": product_id, "quantity": "2"}], status="saved")
    app = create_app()
    client = app.test_client()
    response = client.get(f"/orders/{order_id}/pdf")
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")
