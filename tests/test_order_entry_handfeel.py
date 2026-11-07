"""1-F 开单手手感：复制上一单 API + 数量默认 1 + 回车跳格脚本。"""
from __future__ import annotations

from uuid import uuid4

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.purchases import create_purchase_order


def test_last_order_api_returns_recent_items_for_customer():
    init_db()
    suffix = uuid4().hex[:6]
    customer_id = create_customer(f"手感客户{suffix}")
    create_order_from_typed_rows(
        customer_id,
        f"MD2026010{suffix[:4]}",
        [
            {"product_name": f"手感商品A{suffix}", "unit": "个", "unit_price_yuan": "10.00", "quantity": "3"},
            {"product_name": f"手感商品B{suffix}", "unit": "套", "unit_price_yuan": "20.00", "quantity": "1"},
        ],
        status="saved",
        order_date="2026-01-01",
        order_type="sale",
    )
    client = create_app().test_client()
    response = client.get(f"/orders/api/last_order?type=sale&customer_id={customer_id}")
    payload = response.get_json()
    assert payload["ok"] is True
    assert len(payload["items"]) == 2
    assert payload["items"][0]["quantity"] == "3"
    assert payload["items"][0]["product_name"].startswith("手感商品A")


def test_last_order_api_empty_when_no_history():
    init_db()
    client = create_app().test_client()
    payload = client.get("/orders/api/last_order?type=sale").get_json()
    assert payload["ok"] is False
    assert payload["items"] == []


def test_purchase_last_order_api():
    init_db()
    suffix = uuid4().hex[:6]
    customer_id = create_customer(f"拿货手感{suffix}")
    product_name = f"拿货手感商品{suffix}"
    create_product(product_name, "个")
    with get_db() as conn:
        pid = conn.execute("SELECT id FROM products WHERE name=?", (product_name,)).fetchone()["id"]
    create_purchase_order(
        f"NH2026010{suffix[:4]}",
        "2026-01-01",
        [{"product_id": pid, "product_name": product_name, "unit": "个", "quantity": "5", "unit_cost_yuan": "8.00"}],
        customer_id=customer_id,
        request_key=f"hf-{suffix}",
        status="saved",
    )
    client = create_app().test_client()
    payload = client.get(f"/purchases/api/last_order?customer_id={customer_id}").get_json()
    assert payload["ok"] is True
    assert payload["items"][0]["quantity"] == "5"
    assert payload["items"][0]["unit_price"] == "8.00"


def test_new_order_page_has_copy_button_and_quantity_default_and_enter_script():
    init_db()
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert 'id="copyLastOrderButton"' in html
    # 数量默认 1（首个空白行）
    assert 'name="quantity"' in html
    assert 'value="1"' in html
    assert "/orders/api/last_order" in html
    # 回车跳格脚本存在
    assert "event.key !== 'Enter'" in html
