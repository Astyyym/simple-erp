from uuid import uuid4
from datetime import date
import re

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer


def _post_save_print(client, route: str, customer_id: int, customer_name: str, order_no: str):
    return client.post(
        route,
        data={
            "customer_id": str(customer_id),
            "customer_name": customer_name,
            "order_no": order_no,
            "order_date": "2026-07-10",
            "status": "saved",
            "product_name": ["测试商品"],
            "unit": ["个"],
            "unit_price": ["12.34"],
            "quantity": ["1"],
            "save_action": "save_print",
        },
        headers={"X-Requested-With": "fetch"},
    )


@pytest.mark.parametrize(
    ("route", "expected_next_url"),
    [
        ("/orders/create", "/orders/new"),
        ("/orders/return/create", "/orders/return/new"),
    ],
)
def test_fetch_save_print_returns_json_with_get_urls(route, expected_next_url):
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"异步保存客户-{suffix}"
    customer_id = create_customer(customer_name)
    client = create_app().test_client()

    response = _post_save_print(client, route, customer_id, customer_name, f"IGNORED-{suffix}")

    assert response.status_code == 200
    assert response.is_json
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["order_no"].startswith("MD20260710")
    assert payload["next_url"] == expected_next_url
    assert payload["detail_url"].startswith("/orders/")
    assert payload["pdf_url"].startswith("http://")
    assert payload["pdf_url"].endswith("/pdf")
    with get_db() as conn:
        saved = conn.execute("SELECT id, order_date FROM orders WHERE order_no=?", (payload["order_no"],)).fetchone()
    assert saved is not None
    assert payload["order_id"] == saved["id"]
    assert saved["order_date"] == "2026-07-10"


def test_order_entry_disables_browser_history_but_keeps_erp_suggestions():
    init_db()
    client = create_app().test_client()

    for path in ("/orders/new", "/orders/return/new"):
        html = client.get(path).get_data(as_text=True)
        assert 'id="orderForm" autocomplete="off"' in html
        input_tags = re.findall(r'<input[^>]*>', html)
        customer_input = next(tag for tag in input_tags if 'id="customer_name"' in tag)
        assert 'autocomplete="new-password"' in customer_input
        assert 'name="quantity" class="quantity" autocomplete="new-password"' in html
        assert 'name="unit_price" class="unit-price" autocomplete="new-password"' in html
        assert 'data-lpignore="true"' in html
        # 商品名改由两级原生下拉呈现，浏览器不再对商品名做历史/密码管理器填充。
        assert 'class="form-select form-select-sm picker-name"' in html
        assert 'class="form-select form-select-sm picker-spec"' in html
        assert "fetchJson('/orders/api/customers?q='" in html
        assert "fetchJson('/orders/api/products?q='" in html


def test_order_entry_uses_fetch_success_panel_without_blank_post_target():
    init_db()
    client = create_app().test_client()

    html = client.get("/orders/new").get_data(as_text=True)

    assert "event.preventDefault();" in html
    assert "fetch(orderForm.action" in html
    assert "X-Requested-With" in html
    assert "订单已保存成功" in html
    assert "打开打印预览" in html
    assert "查看订单" in html
    assert "继续开单" in html
    assert "e.currentTarget.target = '_blank'" not in html
    assert "setTimeout(() => { window.location.href" not in html
    # C1：两个按钮都在提交期间禁用（保存 / 保存并打印）。
    assert "savePrintButton.disabled = true" in html
    assert "saveButton.disabled = true" in html


def test_order_entry_shows_server_validation_text_on_failed_async_save():
    """A non-JSON 400 message must remain useful; no preview on failed save."""
    init_db()
    html = create_app().test_client().get("/orders/new").get_data(as_text=True)
    assert "response.headers.get('content-type')" in html
    assert "await response.text()" in html
    assert "订单未保存" in html
