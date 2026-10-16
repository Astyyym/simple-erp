"""Phase 2 数据层：把 `spec` 并进 `name` 的迁移（含预检中止 + 迁移前自动备份）。

本文件只测数据层：迁移本身、`init_db()` 接线、schema 版本、索引与空库一致性。
界面/服务层的去型号在其后面的切片里验证。
"""
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from erp import config as config_module
from erp import db as db_module
from erp.db import CURRENT_SCHEMA_VERSION, get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows

SPEC_TABLES = ("products", "order_items", "purchase_order_items", "purchase_return_items")

# v13 建表语句里 `spec` 独占一行；夹具按同样写法插回，否则迁移的列定位会失败。
SPEC_INSERT_AFTER = {
    "products": "name TEXT NOT NULL,",
    "order_items": "product_name TEXT NOT NULL,",
    "purchase_order_items": "product_name TEXT NOT NULL,",
    "purchase_return_items": "product_name TEXT NOT NULL,",
}


def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _add_spec_columns_back():
    """把已建好的库改回 v13 形态：四表按 v13 写法重建（`spec` 独占一行），
    唯一索引换回含 spec 的旧定义，schema 版本退回 13。

    版本必须退回 13：v14 的迁移对「schema 版本 ≥ 当前版本」的库会直接跳过（与
    `migrate_purchase_return_decoupling` 同规矩），而本夹具是用新程序建库后再改回旧形态的。
    幂等：列已存在就跳过。
    """
    with get_db() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in SPEC_TABLES:
            if "spec" in _columns(conn, table):
                continue
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()["sql"]
            lines, inserted = [], False
            for line in ddl.splitlines():
                lines.append(line)
                if not inserted and line.strip() == SPEC_INSERT_AFTER[table]:
                    lines.append("    spec TEXT NOT NULL DEFAULT '',")
                    inserted = True
            assert inserted, f"{table}: 找不到插入 spec 列的位置"
            kept = _columns(conn, table)
            rows = [tuple(row) for row in conn.execute(f"SELECT {', '.join(kept)} FROM {table}")]
            # 原表整表替换（不改名，保住建表语句原文——迁移依赖它定位表名与列）。
            conn.execute(f"DROP TABLE {table}")
            conn.execute("\n".join(lines))
            placeholders = ", ".join("?" for _ in kept)
            conn.executemany(
                f"INSERT INTO {table} ({', '.join(kept)}) VALUES ({placeholders})", rows
            )
        conn.execute("DROP INDEX IF EXISTS idx_products_business_identity")
        conn.execute(
            "CREATE UNIQUE INDEX idx_products_business_identity "
            "ON products(TRIM(name), TRIM(COALESCE(spec, '')))"
        )
        conn.execute("UPDATE schema_version SET version=13")


def build_v13_database(*, with_conflict=False):
    """构造一个「迁移前」形态的库：真实业务数据 + spec 列 + 含 spec 的唯一索引。"""
    init_db()
    customer_id = create_customer("迁移客户")
    create_order_from_typed_rows(
        customer_id,
        "MD202610130001",
        [{"product_name": "闸阀", "unit": "个", "unit_price_yuan": "100", "quantity": "1"}],
        status="saved",
        order_date="2026-10-13",
    )
    _add_spec_columns_back()
    with get_db() as conn:
        conn.execute("UPDATE products SET spec='DN50' WHERE name='闸阀'")
        conn.execute("UPDATE order_items SET spec='DN50'")
        if with_conflict:
            # 「闸阀」+型号 DN50 与已存在的「闸阀DN50」合并后撞名。
            conn.execute(
                "INSERT INTO products (name, spec, unit) VALUES ('闸阀DN50', '', '个')"
            )
    return customer_id


def snapshot():
    with get_db() as conn:
        data = {
            "products": [tuple(row) for row in conn.execute("SELECT * FROM products ORDER BY id")],
            "order_items": [tuple(row) for row in conn.execute("SELECT * FROM order_items ORDER BY id")],
            "orders_amounts": [
                tuple(row) for row in conn.execute("SELECT id, total_amount_cents FROM orders ORDER BY id")
            ],
            "columns": {table: _columns(conn, table) for table in SPEC_TABLES},
        }
        for table in ("inventory_postings", "product_inventory_state", "inventory_initializations"):
            # v15 起这三张表已不存在；快照对缺失表留空，比较时另行断言（见测试体）。
            if table in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                data[table] = [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            else:
                data[table] = None
    return data


def backup_zips():
    return sorted((config_module.project_path("backups")).glob("*.zip"))


def test_init_db_merges_spec_into_name_and_drops_the_column():
    build_v13_database()
    before = snapshot()
    assert "spec" in before["columns"]["products"]

    init_db()  # 迁移必须挂在 init_db 上才会真的执行

    with get_db() as conn:
        products = {row["id"]: (row["name"], row["unit"]) for row in conn.execute("SELECT * FROM products")}
        assert products == {1: ("闸阀DN50", "个")}
        order_items = [dict(row) for row in conn.execute("SELECT * FROM order_items")]
        assert order_items[0]["product_name"] == "闸阀DN50"
        for table in SPEC_TABLES:
            assert "spec" not in _columns(conn, table), table
        index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_products_business_identity'"
        ).fetchone()["sql"]
        assert "spec" not in index_sql
        assert "TRIM(name)" in index_sql
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION == 15
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    after = snapshot()
    assert len(after["products"]) == len(before["products"])
    assert len(after["order_items"]) == len(before["order_items"])
    assert [row[0] for row in after["orders_amounts"]] == [row[0] for row in before["orders_amounts"]]
    assert [row[1:] for row in after["orders_amounts"]] == [row[1:] for row in before["orders_amounts"]]
    # v15 决策变更：库存三表已被物理删除（迁移本身见 tests/test_drop_inventory_migration.py）。
    for table in ("inventory_postings", "product_inventory_state", "inventory_initializations"):
        assert after[table] is None, table
    assert after["products"] != before["products"]  # 名称确实变了


def test_migration_creates_a_backup_before_rebuilding_tables():
    build_v13_database()
    assert backup_zips() == []

    init_db()

    zips = backup_zips()
    assert len(zips) == 1, zips
    with zipfile.ZipFile(zips[0]) as archive:
        names = archive.namelist()
    assert "erp.db" in names
    # 备份里的库仍是「迁移前」形态：spec 列还在、名称还是旧名。
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zips[0]) as archive:
            archive.extract("erp.db", tmp)
        old = sqlite3.connect(Path(tmp) / "erp.db")
        try:
            columns = [row[1] for row in old.execute("PRAGMA table_info(products)")]
            assert "spec" in columns
            name = old.execute("SELECT name FROM products WHERE id=1").fetchone()[0]
            assert name == "闸阀"
        finally:
            old.close()


def test_migration_aborts_on_name_conflict_and_leaves_everything_untouched():
    build_v13_database(with_conflict=True)
    before = snapshot()

    with pytest.raises(RuntimeError) as excinfo:
        init_db()

    message = str(excinfo.value)
    assert "闸阀DN50" in message
    assert "迁移" in message or "合并" in message

    after = snapshot()
    assert after["columns"]["products"] == before["columns"]["products"]
    assert "spec" in after["columns"]["products"]
    assert after["products"] == before["products"]
    assert backup_zips() == []  # 预检不通过就中止：连备份都不该产生（未做任何写入）


def test_migration_is_idempotent():
    build_v13_database()
    init_db()
    first = snapshot()

    init_db()  # 再跑一次：spec 已不存在，直接返回

    second = snapshot()
    assert second == first
    assert len(backup_zips()) == 1  # 没有第二次迁移，就不再产生备份


def test_fresh_database_has_no_spec_column_and_matches_migrated_columns(monkeypatch):
    build_v13_database()
    init_db()
    migrated = snapshot()["columns"]

    with get_db() as conn:
        for table in SPEC_TABLES:
            assert "spec" not in _columns(conn, table)

    fresh = db_module.db_path().parent / "fresh_erp.db"
    monkeypatch.setattr(db_module, "db_path", lambda: fresh)
    init_db()
    conn = sqlite3.connect(fresh)
    try:
        for table in SPEC_TABLES:
            assert [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] == migrated[table], table
    finally:
        conn.close()
