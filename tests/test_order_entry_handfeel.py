"""1-F 开单手手感：复制上一单 API + 数量留空 + 回车跳格脚本。

2026-11-25 用户口径变更：数量**不再预填 1**，一律留空（灰色 0 是 placeholder，
表示「待补充数量」），用户自己填。原先预填 1 的代价是每次都要先删掉那个 1。
"""
from __future__ import annotations

import re
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


def test_new_order_page_has_copy_button_and_empty_quantity_and_enter_script():
    init_db()
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert 'id="copyLastOrderButton"' in html
    # 数量留空（2026-11-25 用户口径）：placeholder 是灰色 0（＝待补充数量），value 必须是空的。
    # 不能再用「html 里出现 value="1"」这种松断言 —— 页面别处也有 value="1"，说明不了这一格。
    quantity_input = re.search(r'<input name="quantity"[^>]*>', html)
    assert quantity_input is not None, "开单页应当有数量输入框"
    assert 'placeholder="0"' in quantity_input.group(0), "灰色 0 是「待补充数量」的提示，要保留"
    assert 'value=""' in quantity_input.group(0), "数量不能预填 1，一律留空让用户自己填"
    # 漏填数量的兜底：后端会静默丢掉「有名称没数量」的行，前端必须拦。
    assert "findRowMissingQuantity" in html
    assert "/orders/api/last_order" in html
    # 回车跳格脚本存在
    assert "event.key !== 'Enter'" in html
