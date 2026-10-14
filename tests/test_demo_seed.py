"""演示数据生成器契约测试（packaging/seed_demo_data.py）。

绿色文件夹版「打开即用」自带 200 笔演示数据，seed 脚本是唯一生成入口。
本测试用隔离子进程跑**真实生成**，钉住三件事：
1. 200 笔业务单据的类型分配与场景覆盖硬指标；
2. 库存守恒（过账流水之和 == 当前状态）与数据库完整性；
3. 目标库已存在时拒绝覆盖（防把演示数据混进别的库）。

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


def _run_seed(data_root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT), "--data-root", str(data_root), *extra],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=900,
    )


def _readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


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

    db_path = data_root / "data" / "erp.db"
    assert db_path.exists()
    # 不得留下 WAL 边车（打包前检查点由脚本负责）——必须在任何连接打开之前查
    assert not (data_root / "data" / "erp.db-wal").exists()
    assert not (data_root / "data" / "erp.db-shm").exists()
    conn = _readonly(db_path)
    try:
        coverage = manifest["coverage"]
        # 四类单据的作废 / 回收站 / 关联退货都必须有真实数据
        assert coverage["sale_void"] >= 1
        assert coverage["return_void"] >= 1
        assert coverage["purchase_void"] >= 1
        assert coverage["pr_void"] >= 1
        assert coverage["recycled_orders"] >= 2
        assert coverage["recycled_purchases"] >= 1
        assert coverage["recycled_prs"] >= 1
        assert coverage["linked_returns"] >= 20
        assert coverage["missing_cost_sales"] >= 3
        assert coverage["backfilled_items"] >= 3
        assert coverage["payments"] >= 15
        assert coverage["adjustments"] >= 6
        assert coverage["snapshots"] >= 4
        # 库存守恒：过账流水之和必须等于当前库存状态（数量与成本都验）
        drift = conn.execute(
            """
            SELECT s.product_id FROM product_inventory_state s
            LEFT JOIN (
                SELECT product_id, SUM(quantity_delta_3dp) q, SUM(cost_delta_micro) c
                FROM inventory_postings GROUP BY product_id
            ) p ON p.product_id = s.product_id
            WHERE s.quantity_3dp != COALESCE(p.q, 0) OR s.cost_total_micro != COALESCE(p.c, 0)
            """
        ).fetchall()
        assert drift == [], f"库存不守恒: {[row['product_id'] for row in drift]}"
        # 完整性
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # 退货金额必须为负、销售为正（账款口径）
        signs = conn.execute(
            "SELECT order_type, MIN(total_amount_cents) lo, MAX(total_amount_cents) hi "
            "FROM orders GROUP BY order_type"
        ).fetchall()
        by_type = {row["order_type"]: row for row in signs}
        assert by_type["sale"]["lo"] >= 0
        assert by_type["return"]["hi"] <= 0
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
