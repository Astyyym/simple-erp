"""v14 商品身份：型号已并入名称，身份就是「名称」本身。

原用例用「同名不同型号 = 两个商品」表达「两个不同商品」；合并后同一意图由
「名称不同 = 两个商品、名称相同 = 同一个商品」承担，断言强度不降。
"""
import pytest

from erp.db import get_db, init_db
from erp.services.accounting import create_product


def test_different_names_are_distinct_products_and_duplicate_name_is_rejected():
    init_db()

    first_id = create_product("沟槽闸阀DN100", "个", 1200)
    second_id = create_product("沟槽闸阀DN150", "个", 1500)

    assert first_id != second_id
    with get_db() as conn:
        rows = conn.execute("SELECT id, name FROM products ORDER BY id").fetchall()
    assert [(row["id"], row["name"]) for row in rows] == [
        (first_id, "沟槽闸阀DN100"),
        (second_id, "沟槽闸阀DN150"),
    ]
    # 同名（含首尾空白差异）是同一个身份 → 拒绝重复建档。
    with pytest.raises(ValueError, match="商品名称已存在"):
        create_product("  沟槽闸阀DN100  ", "个", 1200)


def test_product_name_is_trimmed_and_blank_name_is_rejected():
    init_db()

    product_id = create_product("  消防水带DN65  ", "卷", 3000)

    with get_db() as conn:
        stored = conn.execute("SELECT name FROM products WHERE id=?", (product_id,)).fetchone()
        total = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    assert stored["name"] == "消防水带DN65"
    assert total == 1
    # 空名称不再有「型号」兜底，直接拒绝（原「空型号另算一个身份」的语义随列删除）。
    with pytest.raises(ValueError, match="商品名称不能为空"):
        create_product("   ", "具", 5000)


def test_soft_deleted_product_still_blocks_same_identity():
    init_db()
    product_id = create_product("软删商品S1", "个", 1000)

    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (product_id,))

    with pytest.raises(ValueError, match="商品名称已存在"):
        create_product("软删商品S1", "个", 1000)


def test_existing_product_id_remains_stable_when_typed_order_uses_trimmed_identity():
    init_db()
    product_id = create_product("稳定商品S2", "个", 1000)

    from erp.services.accounting import create_customer, create_order_from_typed_rows

    customer_id = create_customer("身份测试客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "IDENTITY-001",
        [{"product_name": " 稳定商品S2 ", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )

    with get_db() as conn:
        rows = conn.execute("SELECT id, name FROM products WHERE name=?", ("稳定商品S2",)).fetchall()
        item = conn.execute(
            "SELECT product_id, product_name FROM order_items WHERE order_id=?", (order_id,)
        ).fetchone()
    assert [(row["id"], row["name"]) for row in rows] == [(product_id, "稳定商品S2")]
    assert (item["product_id"], item["product_name"]) == (product_id, "稳定商品S2")
