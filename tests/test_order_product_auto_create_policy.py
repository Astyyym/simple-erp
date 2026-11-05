"""开单商品自动建档口径（2026-11-05 用户决策）。

- 销售单 / 拿货单：未建档的手输名称 → 自动建档（origin='order'）。
- 退货单 / 退拿货单：只能选已建档商品，未建档拒单、不建档。
- 客户专属价（售价）只在**销售**路径学习；拿货、退货、退拿货都不写 customer_prices。
"""
import pytest

from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
)
from erp.services.purchases import create_purchase_order


def _customer(name="口径测试往来"):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM customers WHERE name=?", (name,)).fetchone()
    return row["id"] if row else create_customer(name)


def _product(name, unit="个"):
    return create_product(name, unit)


def _price_rows():
    with get_db() as conn:
        return conn.execute("SELECT customer_id, product_id, price_cents FROM customer_prices").fetchall()


def test_sale_typed_new_product_auto_creates_and_learns_price():
    init_db()
    customer_id = _customer()
    create_order_from_typed_rows(
        customer_id, "SALE-AUTO-001",
        [{"product_name": "销售新商品A", "unit": "个", "unit_price_yuan": "12.5", "quantity": "1"}],
        status="saved", order_type="sale",
    )
    with get_db() as conn:
        product = conn.execute("SELECT id, origin FROM products WHERE name='销售新商品A'").fetchone()
    assert product is not None and product["origin"] == "order"
    rows = _price_rows()
    assert len(rows) == 1 and rows[0]["price_cents"] == 1250


def test_return_typed_new_product_is_rejected_and_not_created():
    """退货单（路由层 enforce_existing_products=True）未建档名称 → 拒单，不建档。"""
    init_db()
    customer_id = _customer()
    with pytest.raises(ValueError, match="退货单只能选择已建档商品"):
        create_order_from_typed_rows(
            customer_id, "RET-AUTO-001",
            [{"product_name": "退货新商品B", "unit": "个", "unit_price_yuan": "9", "quantity": "1"}],
            status="saved", order_type="return", enforce_existing_products=True,
        )
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_return_typed_existing_name_resolves_without_learning_price():
    """退货按原销售单价退：命中已有商品、绑定其 id，但绝不回写客户专属价。"""
    init_db()
    customer_id = _customer()
    product_id = _product("退货已有商品C")
    create_order_from_typed_rows(
        customer_id, "RET-AUTO-002",
        [{"product_name": "退货已有商品C", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved", order_type="return",
    )
    with get_db() as conn:
        item = conn.execute("SELECT product_id FROM order_items ORDER BY id DESC LIMIT 1").fetchone()
    assert item["product_id"] == product_id
    assert _price_rows() == []


def test_purchase_typed_new_product_auto_creates_without_learning_price():
    init_db()
    customer_id = _customer("拿货口径往来")
    create_purchase_order(
        "NH202610130001", "2026-10-13",
        [{"product_name": "拿货新商品D", "quantity": "1", "unit_cost_yuan": "9", "unit": "个"}],
        request_key="purchase-auto-001", customer_id=customer_id,
    )
    with get_db() as conn:
        product = conn.execute("SELECT id, origin FROM products WHERE name='拿货新商品D'").fetchone()
    assert product is not None and product["origin"] == "order"
    assert _price_rows() == []


def test_return_route_rejects_unknown_product_without_creating_it():
    """真实退货单页面提交未建档名称 → 400，且不新建商品（路由层 enforce_existing_products）。"""
    from erp import create_app

    init_db()
    client = create_app().test_client()
    resp = client.post(
        "/orders/return/create",
        data={
            "customer_name": "退货路由未建档客户",
            "status": "saved",
            "product_name": ["路由新商品Z"],
            "unit": ["个"],
            "unit_price": ["5"],
            "quantity": ["1"],
        },
    )
    assert resp.status_code == 400
    assert "已建档商品" in resp.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_normalized_name_matches_existing_product():
    """全/半角括号、空格差异视为同一商品，避免重复建档。"""
    init_db()
    from erp.services.product_registry import find_product_row, resolve_existing_product

    product_id = _product("沟槽闸阀DN65（开维喜）", "台")
    with get_db() as conn:
        assert find_product_row(conn, "沟槽闸阀DN65(开维喜)")["id"] == product_id
        assert find_product_row(conn, "沟槽闸阀 DN65（开维喜）")["id"] == product_id
        assert resolve_existing_product(conn, "沟槽闸阀DN65(开维喜)")["id"] == product_id
