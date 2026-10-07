"""Real pre-inventory schema must upgrade without losing historical business rows."""
import sqlite3
from pathlib import Path

import pytest

from erp import create_app
from erp import db

LEGACY_SCHEMA = Path(__file__).parent / "fixtures" / "legacy_schema_v2.sql"
LEGACY_TABLES = (
    "products", "customers", "customer_prices", "orders", "order_items",
    "payments", "adjustments", "audit_logs",
)


def seed_legacy_database(conn):
    """Only fictional records; do not copy or read a user's business database."""
    conn.executescript(LEGACY_SCHEMA.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO schema_version(version, notes) VALUES (2, 'synthetic legacy')")
    conn.execute("INSERT INTO products(id,name,spec,unit,default_price_cents) VALUES (7,'旧结构测试商品','测试型号','个',1000)")
    conn.execute("INSERT INTO customers(id,name,opening_balance_cents) VALUES (9,'旧结构测试对象',4321)")
    conn.execute("INSERT INTO customer_prices(customer_id,product_id,price_cents) VALUES (9,7,900)")
    for oid, kind, amount in ((11, "sale", 5678), (12, "return", -678)):
        conn.execute(
            "INSERT INTO orders(id,order_no,customer_id,order_date,total_amount_cents,status,order_type) VALUES (?,?,9,'2026-09-29',?,'saved',?)",
            (oid, f"MD20260929{oid:04d}", amount, kind),
        )
        conn.execute(
            "INSERT INTO order_items(order_id,product_id,product_name,spec,unit,quantity,unit_price_cents,subtotal_cents) VALUES (?,7,'旧结构测试商品','测试型号','个',?,678,678)",
            (oid, "1.250" if kind == "sale" else "1"),
        )
    conn.execute("INSERT INTO payments(customer_id,amount_cents,payment_date) VALUES (9,789,'2026-09-29')")
    conn.execute("INSERT INTO adjustments(customer_id,amount_cents,reason) VALUES (9,-200,'虚构测试调整')")
    conn.execute("INSERT INTO audit_logs(action,target_type,target_id,summary) VALUES ('test','order','11','虚构历史审计')")
    return legacy_snapshot(conn)


def legacy_snapshot(conn):
    return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")] for table in LEGACY_TABLES}


def assert_legacy_rows_unchanged(conn, before):
    for table, rows in before.items():
        current = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        assert len(current) == len(rows), table
        assert [{key: row[key] for key in original} for row, original in zip(current, rows)] == rows, table


def test_schema2_application_startup_preserves_legacy_business_rows():
    with db.get_db() as conn:
        before = seed_legacy_database(conn)
        assert "request_key" not in db._columns(conn, "orders")
    for _ in range(2):
        app = create_app()
        client = app.test_client()
        assert client.get("/health").get_json()["status"] == "ok"
        for path in ("/", "/orders/new", "/products/", "/customers/", "/accounts/", "/analytics/", "/settings/"):
            assert client.get(path).status_code == 200, path
    with db.get_db() as conn:
        assert_legacy_rows_unchanged(conn, before)
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 13
        assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=13").fetchone()[0] == 1
        assert {"request_key", "payload_hash", "version"} <= db._columns(conn, "orders")
        assert any(row[1] == "idx_orders_request_key" and row[2] for row in conn.execute("PRAGMA index_list(orders)"))
        assert [tuple(row) for row in conn.execute("SELECT unit_cost_micro,cost_total_micro,source_item_id FROM order_items")] == [(None, None, None), (None, None, None)]
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM product_inventory_state").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema2_failed_index_creation_rolls_back_and_can_retry():
    with db.get_db() as conn:
        before = seed_legacy_database(conn)
    with db.get_db() as conn:
        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_CREATE_INDEX and arg1 == "idx_orders_request_key":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            with conn:
                db.apply_schema(conn)
        conn.set_authorizer(None)
        assert "request_key" not in db._columns(conn, "orders")
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='purchase_orders'").fetchone() is None
        assert_legacy_rows_unchanged(conn, before)
    db.init_db()
    with db.get_db() as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 13
        assert_legacy_rows_unchanged(conn, before)
