import pytest

from erp.db import get_db, init_db
from erp.services.accounting import create_product


def test_same_name_different_spec_are_distinct_products():
    init_db()

    first_id = create_product("沟槽闸阀", "DN100", "个", 1200)
    second_id = create_product("沟槽闸阀", "DN150", "个", 1500)

    assert first_id != second_id
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, spec FROM products WHERE name=? ORDER BY id",
            ("沟槽闸阀",),
        ).fetchall()
    assert [(row["id"], row["spec"]) for row in rows] == [
        (first_id, "DN100"),
        (second_id, "DN150"),
    ]


def test_product_identity_trims_name_and_spec_and_empty_spec_is_unique():
    init_db()

    product_id = create_product("  消防水带  ", "  DN65  ", "卷", 3000)

    with pytest.raises(ValueError, match="商品名称和型号已存在"):
        create_product("消防水带", "DN65", "卷", 3000)

    empty_spec_id = create_product("灭火器", "   ", "具", 5000)
    assert empty_spec_id != product_id
    with pytest.raises(ValueError, match="商品名称和型号已存在"):
        create_product(" 灭火器 ", "", "具", 5000)

    with get_db() as conn:
        stored = conn.execute("SELECT name, spec FROM products WHERE id=?", (product_id,)).fetchone()
    assert stored["name"] == "消防水带"
    assert stored["spec"] == "DN65"


def test_soft_deleted_product_still_blocks_same_identity():
    init_db()
    product_id = create_product("软删商品", "S1", "个", 1000)

    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (product_id,))

    with pytest.raises(ValueError, match="商品名称和型号已存在"):
        create_product("软删商品", "S1", "个", 1000)


def test_existing_product_id_remains_stable_when_typed_order_uses_trimmed_identity():
    init_db()
    product_id = create_product("稳定商品", "S2", "个", 1000)

    from erp.services.accounting import create_customer, create_order_from_typed_rows

    customer_id = create_customer("身份测试客户")
    create_order_from_typed_rows(
        customer_id,
        "IDENTITY-001",
        [{"product_name": " 稳定商品 ", "spec": " S2 ", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, spec FROM products WHERE name=? AND spec=?",
            ("稳定商品", "S2"),
        ).fetchall()
    assert [(row["id"], row["name"], row["spec"]) for row in rows] == [(product_id, "稳定商品", "S2")]
