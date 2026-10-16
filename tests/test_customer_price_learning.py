from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp import create_app


def _learned(customer_id: int, product_id: int):
    with get_db() as conn:
        return conn.execute(
            "SELECT price_cents FROM customer_prices WHERE customer_id=? AND product_id=?",
            (customer_id, product_id),
        ).fetchone()


def test_typed_order_learns_customer_specific_price_when_price_is_positive():
    """学习口径（2026-10-15）：手输价 > 0 就写客户专属价（不再与档案价比对）。"""
    init_db()
    customer_id = create_customer("客户价客户")
    product_id = create_product("客户价商品", "个")
    create_order_from_typed_rows(customer_id, "CP-001", [{"product_name": "客户价商品", "unit": "个", "unit_price_yuan": "45", "quantity": "1"}], status="saved")
    row = _learned(customer_id, product_id)
    assert row is not None
    assert row["price_cents"] == 4500


def test_typed_order_does_not_learn_zero_price():
    """0 元不学（用户 2026-10-15 明确：「0元手术价格不学」）。"""
    init_db()
    customer_id = create_customer("零价客户")
    product_id = create_product("零价商品", "个")
    create_order_from_typed_rows(customer_id, "CP-002", [{"product_name": "零价商品", "unit": "个", "unit_price_yuan": "0", "quantity": "1"}], status="saved")
    assert _learned(customer_id, product_id) is None


def test_typed_order_learns_price_for_brand_new_product_too():
    """开单自动建档的商品也要学：否则第一单的价丢了，下一单还是空的。"""
    init_db()
    customer_id = create_customer("自动建档客户")
    create_order_from_typed_rows(customer_id, "CP-003", [{"product_name": "手输新商品", "unit": "个", "unit_price_yuan": "12.5", "quantity": "1"}], status="saved")
    with get_db() as conn:
        product_id = conn.execute("SELECT id FROM products WHERE name='手输新商品'").fetchone()["id"]
    row = _learned(customer_id, product_id)
    assert row is not None and row["price_cents"] == 1250


def test_product_api_returns_customer_specific_price_when_customer_selected():
    init_db()
    customer_id = create_customer("客户价API")
    create_product("API商品", "个")
    create_order_from_typed_rows(customer_id, "CP-API", [{"product_name": "API商品", "unit": "个", "unit_price_yuan": "44", "quantity": "1"}], status="saved")
    app = create_app()
    client = app.test_client()
    response = client.get(f"/orders/api/products?q=API&customer_id={customer_id}")
    data = response.get_json()
    assert data[0]["unit_price"] == "44.00"
    assert data[0]["customer_price"] == "44.00"
    assert data[0]["is_customer_price"] is True
    # 决策变更（2026-10-15）：档案已无售价，接口不再下发「常规价」。
    assert "regular_price" not in data[0]


def test_product_api_returns_no_price_without_customer_price():
    """没有专属价时 `unit_price` 为 null（页面留空手输），不回落任何档案价。"""
    init_db()
    customer_id = create_customer("无价API客户")
    create_product("无价API商品", "个")
    client = create_app().test_client()
    data = client.get(f"/orders/api/products?q=无价API&customer_id={customer_id}").get_json()
    assert data[0]["unit_price"] is None
    assert data[0]["customer_price"] is None
    assert data[0]["is_customer_price"] is False
