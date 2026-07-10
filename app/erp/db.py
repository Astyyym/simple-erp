import sqlite3
from contextlib import contextmanager
from pathlib import Path

from datetime import datetime, timedelta

from .config import project_path

CURRENT_SCHEMA_VERSION = 2


def db_path() -> Path:
    return project_path("data", "erp.db")


@contextmanager
def get_db():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    project_path("data").mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        apply_schema(conn)
        ensure_soft_delete_columns(conn)
        ensure_order_type_column(conn)
        purge_expired_recycle_bin(conn)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row[0] != "ok":
            raise RuntimeError(f"数据库完整性检查失败: {row[0]}")


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    existing = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
    if existing is None:
        conn.execute("INSERT INTO schema_version(version, notes) VALUES (?, ?)", (CURRENT_SCHEMA_VERSION, "initial schema"))


def integrity_check() -> str:
    with get_db() as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_soft_delete_columns(conn: sqlite3.Connection) -> None:
    for table in ["orders", "products", "customers"]:
        cols = _columns(conn, table)
        if "deleted_at" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT")
        if "delete_reason" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN delete_reason TEXT DEFAULT ''")


def ensure_order_type_column(conn: sqlite3.Connection) -> None:
    cols = _columns(conn, "orders")
    if "order_type" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'sale' CHECK(order_type IN ('sale','return'))")


def purge_expired_recycle_bin(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE deleted_at IS NOT NULL AND deleted_at < ?)", (cutoff,))
    conn.execute("DELETE FROM orders WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))
    conn.execute("DELETE FROM customer_prices WHERE product_id IN (SELECT id FROM products WHERE deleted_at IS NOT NULL AND deleted_at < ?)", (cutoff,))
    conn.execute("DELETE FROM products WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))
    conn.execute("DELETE FROM customers WHERE deleted_at IS NOT NULL AND deleted_at < ? AND id NOT IN (SELECT customer_id FROM orders) AND id NOT IN (SELECT customer_id FROM payments) AND id NOT IN (SELECT customer_id FROM adjustments)", (cutoff,))


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    spec TEXT DEFAULT '',
    unit TEXT NOT NULL,
    default_price_cents INTEGER NOT NULL CHECK(default_price_cents >= 0),
    notes TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    usage_count INTEGER NOT NULL DEFAULT 0,
    pinyin_initials TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    delete_reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    phone TEXT DEFAULT '',
    address TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    delete_reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS customer_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(customer_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date TEXT NOT NULL,
    total_amount_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','saved','printed','void')),
    order_type TEXT NOT NULL DEFAULT 'sale' CHECK(order_type IN ('sale','return')),
    notes TEXT DEFAULT '',
    void_reason TEXT DEFAULT '',
    print_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    delete_reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    product_name TEXT NOT NULL,
    spec TEXT DEFAULT '',
    unit TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents >= 0)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    payment_date TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '现金',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','void')),
    void_reason TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL,
    adjustment_type TEXT NOT NULL DEFAULT 'other',
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','void')),
    void_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    voided_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
