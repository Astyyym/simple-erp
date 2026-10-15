"""四类开单取价（2026-10-15）：往来对象驱动的行重取价 + 编辑页 source_item_id 空值渲染。"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
)


def _seed_two_customers():
    """商品默认价 100；甲成交 80，乙成交 85（同一商品、不同客户）。"""
    init_db()
    first = create_customer("甲消防工程")
    second = create_customer("乙物业公司")
    product_id = create_product("干粉灭火器4kg", "具", 10000)
    create_order_from_typed_rows(
        first, "MD-A",
        [{"product_name": "干粉灭火器4kg", "unit": "具", "unit_price_yuan": "80", "quantity": "1"}],
        status="saved",
    )
    create_order_from_typed_rows(
        second, "MD-B",
        [{"product_name": "干粉灭火器4kg", "unit": "具", "unit_price_yuan": "85", "quantity": "1"}],
        status="saved",
    )
    return first, second, product_id


def test_products_api_prices_by_customer_and_falls_back_to_default():
    first, second, _ = _seed_two_customers()
    client = create_app().test_client()
    cases = {
        first: ("80.00", "100.00", "80.00", True),
        second: ("85.00", "100.00", "85.00", True),
        0: ("100.00", "100.00", None, False),
    }
    for customer_id, (unit, regular, customer_price, is_customer) in cases.items():
        data = client.get(f"/orders/api/products?q=干粉&customer_id={customer_id}").get_json()
        assert data[0]["unit_price"] == unit, customer_id
        assert data[0]["regular_price"] == regular, customer_id
        assert data[0]["customer_price"] == customer_price, customer_id
        assert data[0]["is_customer_price"] is is_customer, customer_id


def test_purchase_products_api_exposes_regular_price_without_customer_price():
    """拿货页商品接口给档案常规价，不给客户价（2026-10-15：供应商 ≠ 客户）。"""
    _seed_two_customers()
    client = create_app().test_client()
    data = client.get("/purchases/api/products?q=干粉").get_json()
    assert data[0]["default_price_cents"] == 10000
    assert "customer_price" not in data[0]
    assert "is_customer_price" not in data[0]


def test_edit_page_renders_blank_source_item_id_not_the_word_none():
    """销售单明细的 `source_item_id` 在库里是 NULL，编辑页必须渲染成空串。

    旧写法 `item.source_item_id if item else ''` 只判「有没有 item」，于是 NULL
    被 Jinja 打成字符串 `"None"`：前端据此把普通销售行误判成「原单带入行」，
    重编辑提交时还会把 `"None"` 当有效明细 id 送去 `int()`。
    """
    first, _, _ = _seed_two_customers()
    client = create_app().test_client()
    with get_db() as conn:
        order_id = conn.execute("SELECT id FROM orders WHERE order_no='MD-A'").fetchone()["id"]
    html = client.get(f"/orders/{order_id}/edit").get_data(as_text=True)
    assert 'class="source-item-id" value=""' in html
    assert 'value="None"' not in html
