from decimal import Decimal

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import (
    initialize_product,
    post_inventory_event,
    revise_initialization,
)
from erp.routes.recycle import _purge_one


def _product(name="期初商品", spec="S1"):
    init_db()
    return create_product(name, spec, "个", 2000)


def test_initialize_product_creates_enabled_state_and_is_idempotent():
    product_id = _product()

    first = initialize_product(
        product_id,
        quantity="10",
        unit_cost="10",
        business_date="2026-10-01",
        source="系统上线期初",
        request_key="init-001",
    )
    retry = initialize_product(
        product_id,
        quantity="10.000",
        unit_cost=Decimal("10.000000"),
        business_date="2026-10-01",
        source="系统上线期初",
        request_key="init-001",
    )

    assert retry == first
    with get_db() as conn:
        state = conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        postings = conn.execute("SELECT * FROM inventory_postings WHERE product_id=?", (product_id,)).fetchall()
        initialization = conn.execute("SELECT * FROM inventory_initializations WHERE product_id=?", (product_id,)).fetchone()
    assert state["enabled"] == 1
    assert state["quantity_3dp"] == 10000
    assert state["cost_total_micro"] == 100_000_000
    assert state["avg_cost_micro"] == 10_000_000
    assert state["version"] == 1
    assert initialization["locked"] == 0
    assert len(postings) == 1
    assert postings[0]["source_type"] == "initialization"

    with pytest.raises(ValueError, match="幂等键内容不一致"):
        initialize_product(product_id, "9", "10", "2026-10-01", "改内容", "init-001")
    with pytest.raises(ValueError, match="已经初始化"):
        initialize_product(product_id, "9", "10", "2026-10-01", "重复初始化", "init-002")


def test_initialization_rejects_unknown_cost_and_requires_explicit_zero_confirmation():
    product_id = _product("成本校验商品")

    with pytest.raises(ValueError, match="数量大于零时必须填写期初成本"):
        initialize_product(product_id, "1", None, "2026-10-01", "期初", "cost-001")
    with pytest.raises(ValueError, match="零期初必须明确确认"):
        initialize_product(product_id, "0", "0", "2026-10-01", "期初", "cost-002")

    result = initialize_product(
        product_id,
        "0",
        "0",
        "2026-10-01",
        "期初无库存",
        "cost-003",
        confirm_zero=True,
    )
    assert result["version"] == 1
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert tuple(state) == (0, 0, None)


def test_initialization_revision_is_allowed_until_first_later_event_then_locks():
    product_id = _product("修订商品")
    initialize_product(product_id, "10", "10", "2026-10-01", "期初", "rev-001")

    revised = revise_initialization(
        product_id,
        quantity="8",
        unit_cost="12",
        reason="盘点后修正",
        expected_version=1,
    )
    assert revised["version"] == 2
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        initialization = conn.execute("SELECT quantity_3dp, cost_total_micro, revision_no, locked FROM inventory_initializations WHERE product_id=?", (product_id,)).fetchone()
        postings = conn.execute("SELECT source_type, quantity_delta_3dp, cost_delta_micro FROM inventory_postings WHERE product_id=? ORDER BY posting_seq", (product_id,)).fetchall()
    assert tuple(state) == (8000, 96_000_000, 12_000_000)
    assert tuple(initialization) == (8000, 96_000_000, 1, 0)
    assert [(row["source_type"], row["quantity_delta_3dp"], row["cost_delta_micro"]) for row in postings] == [
        ("initialization", 10000, 100_000_000),
        ("initialization_revision", -2000, -4_000_000),
    ]

    with pytest.raises(ValueError, match="版本冲突"):
        revise_initialization(product_id, "7", "12", "过期修订", expected_version=1)

    post_inventory_event(product_id, quantity_delta="1", cost_delta="14", source_type="purchase", source_id="P-001", business_date="2026-10-02")
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp, cost_total_micro, avg_cost_micro, version FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        initialization = conn.execute("SELECT locked FROM inventory_initializations WHERE product_id=?", (product_id,)).fetchone()
    assert tuple(state) == (9000, 110_000_000, 12_222_222, 3)
    assert initialization["locked"] == 1

    with pytest.raises(ValueError, match="已锁定"):
        revise_initialization(product_id, "9", "12", "锁定后修订", expected_version=3)


def test_initialization_rejects_bad_precision_negative_values_and_deleted_products():
    product_id = _product("精度商品")
    cases = [
        ("1.0001", "10", "数量最多保留3位小数"),
        ("-1", "10", "数量不能为负数"),
        ("1", "-0.1", "成本不能为负数"),
        ("NaN", "10", "数量必须是有限数字"),
        ("1", "10.1234567", "成本最多保留6位小数"),
    ]
    for quantity, cost, message in cases:
        with pytest.raises(ValueError, match=message):
            initialize_product(product_id, quantity, cost, "2026-10-01", "期初", f"bad-{quantity}-{cost}")

    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (product_id,))
    with pytest.raises(ValueError, match="商品不存在或已删除"):
        initialize_product(product_id, "1", "10", "2026-10-01", "期初", "deleted-001")


def test_product_edit_page_exposes_initialization_and_lock_state():
    product_id = _product("页面期初商品")
    client = create_app().test_client()

    response = client.get("/products/")
    assert response.status_code == 200
    assert "页面期初商品" in response.get_data(as_text=True)
    assert "未启用" in response.get_data(as_text=True)

    response = client.get(f"/products/{product_id}/edit")
    assert response.status_code == 200
    assert "未启用" in response.get_data(as_text=True)

    response = client.post(
        f"/products/{product_id}/inventory/initialize",
        data={
            "quantity": "10",
            "unit_cost": "10",
            "business_date": "2026-10-01",
            "source": "系统上线期初",
            "request_key": "page-init-001",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "期初库存已启用" in html
    assert "允许首笔库存流水前修订" in html

    post_inventory_event(product_id, "1", "14", "purchase", "PAGE-P-001", "2026-10-02")
    response = client.get(f"/products/{product_id}/edit")
    assert response.status_code == 200
    assert "期初已锁定" in response.get_data(as_text=True)


def test_cost_total_uses_half_up_rounding_at_micro_unit_boundary():
    product_id = _product("成本舍入商品")
    initialize_product(product_id, "0.001", "0.000001", "2026-10-01", "期初", "round-001")

    with get_db() as conn:
        state = conn.execute(
            "SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
    assert tuple(state) == (1, 0, 0)


def test_inventory_history_no_longer_blocks_product_purge():
    """2026-10-07 起：商品没有**未删除**单据引用时，库存历史不再阻止永久删除。

    用户口径：确认删除商品会连库存状态/期初/流水一起清掉，不留孤儿记录。
    """
    product_id = _product("库存历史商品")
    initialize_product(product_id, "2", "10", "2026-10-01", "期初", "purge-001")
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=datetime('now', '-8 days') WHERE id=?", (product_id,))
        _purge_one(conn, "product", product_id)
        assert conn.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM inventory_initializations WHERE product_id=?", (product_id,)).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_product_purge_is_blocked_while_live_document_references_it():
    """有未删除单据还在用该商品时，必须保留并说明原因。"""
    product_id = _product("活引用商品")
    initialize_product(product_id, "2", "10", "2026-10-01", "期初", "purge-002")
    customer_id = create_customer("活引用客户")
    create_order_from_typed_rows(
        customer_id, "MD202610070001",
        [{"product_id": product_id, "product_name": "活引用商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-07",
    )
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=datetime('now', '-8 days') WHERE id=?", (product_id,))
        with pytest.raises(ValueError, match="未删除的单据还在使用"):
            _purge_one(conn, "product", product_id)
        assert conn.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone() is not None
