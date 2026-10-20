"""演示数据生成器契约测试（packaging/seed_demo_data.py）。

绿色文件夹版「打开即用」自带 200 笔演示数据，seed 脚本是唯一生成入口。
本测试用隔离子进程跑**真实生成**，钉住四件事：
1. 200 笔业务单据的类型分配（销售 90 / 退货 35 / 拿货 45 / 退拿货 30）与场景覆盖硬指标；
2. 金额守恒（单头金额 == 明细小计之和，退货单为负号口径）与数据库完整性 / 外键；
3. 库结构就是当前 schema v15：表清单与商品 / 明细列都在白名单内——任何被 v15 物理
   删除的表或列若被重新写回，白名单断言立刻变红；
4. 目标库已存在时拒绝覆盖（防把演示数据混进别的库）。

测试数据库全部落在 pytest 临时目录，不触碰正式 data/erp.db。
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_SCRIPT = PROJECT_ROOT / "packaging" / "seed_demo_data.py"

# v15 现存的业务表（不含 sqlite 内部表）：多出任何一张都说明 schema 被改回去了。
ALLOWED_TABLES = {
    "schema_version", "customers", "products", "customer_prices",
    "orders", "order_items", "order_number_sequences",
    "purchase_orders", "purchase_order_items", "purchase_number_sequences",
    "purchase_return_orders", "purchase_return_items", "purchase_return_number_sequences",
    "payments", "adjustments", "audit_logs", "reconciliation_snapshots",
}
# v15 现有列：商品档案只保留名称 / 品牌 / 单位，销售明细只保留业务字段。
ALLOWED_PRODUCT_COLUMNS = {
    "id", "name", "unit", "notes", "is_active", "usage_count", "pinyin_initials",
    "pinyin_full", "name_norm", "origin", "brand", "created_at", "updated_at", "deleted_at",
    "delete_reason", "image_path",
}
ALLOWED_ORDER_ITEM_COLUMNS = {
    "id", "order_id", "product_id", "product_name", "unit", "quantity",
    "unit_price_cents", "subtotal_cents", "source_item_id",
}

# 场景覆盖硬指标（脚本自身校验同一批键；这里独立复验生成结果）
COVERAGE_MINIMUMS = {
    "sale_void": 1, "return_void": 1, "purchase_void": 1, "pr_void": 1,
    "recycled_orders": 2, "recycled_purchases": 1, "recycled_prs": 1,
    "printed_orders": 8, "linked_returns": 20, "bound_return_items": 25,
    "customers": 28, "products": 40, "customer_prices": 3,
    "payments": 15, "payments_void": 1, "adjustments": 6, "adjustments_void": 1,
    "snapshots": 4, "audit_logs": 100,
}


def _run_seed(data_root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT), "--data-root", str(data_root), *extra],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=900,
    )


def _readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_seed_creates_200_documents_with_full_coverage(tmp_path):
    data_root = tmp_path / "demo_data"
    result = _run_seed(data_root)
    assert result.returncode == 0, f"seed 失败:\n{result.stdout}\n{result.stderr}"

    # 子进程 stdout 末尾是 manifest JSON
    manifest = json.loads(result.stdout[result.stdout.index("{"):result.stdout.rindex("}") + 1])
    assert manifest["problems"] == []
    assert manifest["documents"] == {
        "sale": 90, "return": 35, "purchase": 45, "purchase_return": 30, "total": 200,
    }
    assert manifest["warnings"] == []

    db_path = data_root / "data" / "erp.db"
    assert db_path.exists()
    # 不得留下 WAL 边车（打包前检查点由脚本负责）——必须在任何连接打开之前查
    assert not (data_root / "data" / "erp.db-wal").exists()
    assert not (data_root / "data" / "erp.db-shm").exists()
    conn = _readonly(db_path)
    try:
        coverage = manifest["coverage"]
        # 四类单据的作废 / 回收站 / 已打印 / 关联退货都必须有真实数据
        for key, minimum in COVERAGE_MINIMUMS.items():
            assert coverage[key] >= minimum, f"{key}={coverage[key]} < {minimum}"
        # 金额守恒：单头金额必须等于明细小计之和（退货单为负号口径）
        drift = conn.execute(
            """
            SELECT o.id FROM orders o
            LEFT JOIN (
                SELECT order_id AS f, SUM(subtotal_cents) AS s FROM order_items GROUP BY order_id
            ) i ON i.f = o.id
            WHERE o.total_amount_cents != CASE WHEN o.order_type='return'
                  THEN -COALESCE(i.s, 0) ELSE COALESCE(i.s, 0) END
            """
        ).fetchall()
        assert drift == [], f"销售/退货单金额与明细小计不一致: {[row['id'] for row in drift]}"
        for order_table, item_table, fk in (
            ("purchase_orders", "purchase_order_items", "purchase_order_id"),
            ("purchase_return_orders", "purchase_return_items", "purchase_return_id"),
        ):
            drift = conn.execute(
                f"""
                SELECT o.id FROM {order_table} o
                LEFT JOIN (
                    SELECT {fk} AS f, SUM(subtotal_cents) AS s FROM {item_table} GROUP BY {fk}
                ) i ON i.f = o.id
                WHERE o.total_amount_cents != COALESCE(i.s, 0)
                """
            ).fetchall()
            assert drift == [], f"{order_table} 金额与明细小计不一致: {[row['id'] for row in drift]}"
        # 完整性 / 外键
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # 退货金额必须为负、销售为正（账款口径）
        signs = conn.execute(
            "SELECT order_type, MIN(total_amount_cents) lo, MAX(total_amount_cents) hi "
            "FROM orders GROUP BY order_type"
        ).fetchall()
        by_type = {row["order_type"]: row for row in signs}
        assert by_type["sale"]["lo"] >= 0
        assert by_type["return"]["hi"] <= 0
        # 库结构 == schema v15：表与列都在白名单内（v15 删掉的表/列被写回即红）
        tables = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row["name"].startswith("sqlite_")
        }
        assert tables <= ALLOWED_TABLES, f"出现白名单外的表: {sorted(tables - ALLOWED_TABLES)}"
        assert {"products", "orders", "order_items", "purchase_orders",
                "purchase_return_orders"} <= tables
        product_columns = _columns(conn, "products")
        assert product_columns <= ALLOWED_PRODUCT_COLUMNS, (
            f"products 出现白名单外的列: {sorted(product_columns - ALLOWED_PRODUCT_COLUMNS)}"
        )
        item_columns = _columns(conn, "order_items")
        assert item_columns <= ALLOWED_ORDER_ITEM_COLUMNS, (
            f"order_items 出现白名单外的列: {sorted(item_columns - ALLOWED_ORDER_ITEM_COLUMNS)}"
        )
        # 业务规模：28 个往来对象 / 40 个商品（另有 3 个由订单首次出现自动建档）
        assert conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"] == 28
        assert conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"] >= 40
    finally:
        conn.close()

    # 用户-facing 说明文件随包生成
    notes = tmp_path / "演示数据说明.txt"
    assert notes.exists() and "200" in notes.read_text(encoding="utf-8")


def test_seed_refuses_to_overwrite_existing_database(tmp_path):
    data_root = tmp_path / "demo_data"
    (data_root / "data").mkdir(parents=True)
    (data_root / "data" / "erp.db").write_bytes(b"not a real database")
    result = _run_seed(data_root)
    assert result.returncode == 2
    assert "拒绝执行" in result.stdout
    # 原文件未被改动
    assert (data_root / "data" / "erp.db").read_bytes() == b"not a real database"
