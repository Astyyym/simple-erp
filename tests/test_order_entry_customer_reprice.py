"""四类开单取价（2026-10-15）：往来对象驱动的行重取价 + 编辑页 source_item_id 空值渲染。"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
)


def _seed_two_customers():
    """同一商品、不同客户：甲成交 80，乙成交 85（售价唯一来源 = 开单学习出的客户专属价）。"""
    init_db()
    first = create_customer("甲消防工程")
    second = create_customer("乙物业公司")
    product_id = create_product("干粉灭火器4kg", "具")
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


def test_products_api_prices_by_customer_and_leaves_blank_without_one():
    """取价口径（2026-10-15）：单价只认**该客户的专属价**；没有专属价时给 null（页面留空手输）。

    旧契约是 `COALESCE(客户价, 档案默认价)` 并把档案价作为「常规价」一起下发；
    默认价下线后档案已无售价，接口只可能给出「客户专属价」或「无价」。
    """
    first, second, _ = _seed_two_customers()
    client = create_app().test_client()
    cases = {
        first: ("80.00", True),
        second: ("85.00", True),
        0: (None, False),
    }
    for customer_id, (unit, is_customer) in cases.items():
        data = client.get(f"/orders/api/products?q=干粉&customer_id={customer_id}").get_json()
        assert data[0]["unit_price"] == unit, customer_id
        assert data[0]["customer_price"] == unit, customer_id
        assert data[0]["is_customer_price"] is is_customer, customer_id
        assert "regular_price" not in data[0], "档案已无售价，不应再下发「常规价」"


def test_purchase_products_api_exposes_no_sale_price_at_all():
    """拿货页商品接口不给售价、也不给客户价（2026-10-15：供应商 ≠ 客户；档案已无售价）。"""
    _seed_two_customers()
    client = create_app().test_client()
    data = client.get("/purchases/api/products?q=干粉").get_json()
    assert data[0]["unit"] == "具"
    assert "default_price_cents" not in data[0]
    assert "unit_price" not in data[0]
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
