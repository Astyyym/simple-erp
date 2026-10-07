-- Frozen schema 2 from the pre-inventory database; synthetic test data only.

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
