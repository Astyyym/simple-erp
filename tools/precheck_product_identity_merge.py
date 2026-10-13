"""商品身份合并（name + spec → 单一 name）的**只读**预检脚本。

Phase 2 会把 `products` / `order_items` / `purchase_order_items` / `purchase_return_items`
四张表的 `spec` 列并进 `name`（直接拼接、不加分隔符），并重建表。合并后若出现重名，
唯一索引 `ON products(TRIM(name))` 会撞车，因此在**任何机器上执行迁移之前**先跑本脚本：
它只读打开目标库，预演合并、输出冲突清单与影响行数，绝不写库。

用法::

    python tools/precheck_product_identity_merge.py "C:/Users/<用户>/Documents/简单ERP数据/data/erp.db"
    python tools/precheck_product_identity_merge.py <db> --json

退出码：0 = 无冲突（可迁移）；1 = 发现冲突（禁止迁移，先人工裁决）；2 = 库/结构不可读。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# spec 列的四个落点表（与需要重建的表一致）。
SPEC_TABLES = ("products", "order_items", "purchase_order_items", "purchase_return_items")
# 合并后名称的公式，必须与迁移实现逐字一致。
MERGED_NAME_SQL = "TRIM(COALESCE(name, '')) || TRIM(COALESCE(spec, ''))"


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    # 只读连接上也允许读 WAL 内容；不改动任何数据。
    conn.execute("PRAGMA query_only = ON")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _schema_version(conn: sqlite3.Connection) -> int | None:
    if "schema_version" not in _tables(conn):
        return None
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] if row and row[0] is not None else None


def _count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return conn.execute(sql).fetchone()[0]


def precheck(db_path: Path) -> dict:
    conn = connect_readonly(db_path)
    try:
        tables = _tables(conn)
        report: dict = {
            "db": str(db_path),
            "schema_version": _schema_version(conn),
            "spec_columns": {t: (t in tables and "spec" in _columns(conn, t)) for t in SPEC_TABLES},
            "row_counts": {t: (_count(conn, t) if t in tables else None) for t in SPEC_TABLES},
            "note": "只读预检：本脚本不写库、不建表、不改任何行。",
        }
        if "products" not in tables or not report["spec_columns"]["products"]:
            report["blocked"] = "products 表缺失或没有 spec 列（可能已迁移过，或不是本产品的库）"
            return report

        merged = MERGED_NAME_SQL
        report["products"] = {
            "rows": _count(conn, "products"),
            "rows_with_spec": _count(conn, "products", "TRIM(COALESCE(spec, '')) <> ''"),
            "rows_renamed": _count(conn, "products", f"TRIM(COALESCE(spec, '')) <> ''"),
            "rows_merged_name_empty": _count(conn, "products", f"{merged} = ''"),
        }

        conflict_rows = conn.execute(
            f"""
            SELECT {merged} AS merged_name,
                   COUNT(*) AS row_count,
                   GROUP_CONCAT(id) AS ids,
                   GROUP_CONCAT(TRIM(COALESCE(name, '')) || '｜' || TRIM(COALESCE(spec, ''))) AS originals
            FROM products
            GROUP BY merged_name
            HAVING row_count > 1
            ORDER BY row_count DESC, merged_name
            """
        ).fetchall()
        conflicts = [
            {
                "merged_name": row["merged_name"],
                "row_count": row["row_count"],
                "ids": [int(part) for part in str(row["ids"]).split(",")],
                "originals": str(row["originals"]).split(","),
            }
            for row in conflict_rows
        ]
        report["conflicts"] = conflicts
        report["conflict_groups"] = len(conflicts)
        report["conflict_rows_involved"] = sum(item["row_count"] for item in conflicts)
        report["ok"] = not conflicts and report["products"]["rows_merged_name_empty"] == 0
        if report["products"]["rows_merged_name_empty"]:
            report["blocked"] = "合并后有商品的名称会变成空串（name 与 spec 都为空）"
        return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="商品身份合并只读预检（name + spec → name）")
    parser.add_argument("db", help="目标 erp.db 路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认也是 JSON；保留开关便于脚本调用）")
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(json.dumps({"db": str(db_path), "error": "文件不存在"}, ensure_ascii=False, indent=2))
        return 2

    try:
        report = precheck(db_path)
    except sqlite3.DatabaseError as exc:
        print(json.dumps({"db": str(db_path), "error": f"无法读取数据库：{exc}"}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("blocked"):
        return 1
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
