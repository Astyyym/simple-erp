-- Frozen pre-v13 (pre-decoupling) schema: the frozen v14 schema PLUS the `spec` columns
-- and NOT NULL source ids that `migrate_purchase_return_decoupling` (v13) exists to relax.
-- Synthetic test fixture only. It is the only shape that actually makes v13 fire, so it is
-- what proves the v13 -> v14 -> v15 upgrade chain on a real store-shaped database.

-- Frozen v14 schema (frozen 2026-10-16, before the v15 inventory-system drop).
-- Synthetic test fixture only: this is the exact pre-v15 SCHEMA_SQL, kept so that the
-- v15 migration test can build a genuine v14 database (inventory tables + cost columns)
-- instead of bootstrapping with the new code and ALTERing columns back.

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
    pinyin_full TEXT DEFAULT '',
    origin TEXT,
    brand TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    delete_reason TEXT DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    safety_stock_3dp INTEGER NOT NULL DEFAULT 0 CHECK(safety_stock_3dp >= 0)
);
CREATE TABLE IF NOT EXISTS product_inventory_state (
    product_id INTEGER PRIMARY KEY REFERENCES products(id),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
    quantity_3dp INTEGER NOT NULL DEFAULT 0 CHECK(quantity_3dp >= 0),
    cost_total_micro INTEGER NOT NULL DEFAULT 0 CHECK(cost_total_micro >= 0),
    avg_cost_micro INTEGER,
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    last_posting_seq INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_initializations (
    product_id INTEGER PRIMARY KEY REFERENCES products(id),
    quantity_3dp INTEGER NOT NULL CHECK(quantity_3dp >= 0),
    cost_total_micro INTEGER NOT NULL CHECK(cost_total_micro >= 0),
    business_date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    cost_source TEXT NOT NULL DEFAULT '',
    request_key TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    revision_no INTEGER NOT NULL DEFAULT 0 CHECK(revision_no >= 0),
    locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_postings (
    posting_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    quantity_delta_3dp INTEGER NOT NULL,
    cost_delta_micro INTEGER NOT NULL,
    business_date TEXT NOT NULL,
    posted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_inventory_postings_product_seq
    ON inventory_postings(product_id, posting_seq);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    customer_name TEXT NOT NULL DEFAULT '',
    order_no TEXT NOT NULL UNIQUE,
    business_date TEXT NOT NULL,
    total_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(total_amount_cents >= 0),
    status TEXT NOT NULL DEFAULT 'saved' CHECK(status IN ('draft','saved','void')),
    source_notes TEXT NOT NULL DEFAULT '',
    void_reason TEXT NOT NULL DEFAULT '',
    request_key TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    delete_reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS purchase_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,
    quantity_3dp INTEGER NOT NULL CHECK(quantity_3dp > 0),
    unit_cost_cents INTEGER NOT NULL CHECK(unit_cost_cents >= 0),
    subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents >= 0)
);
CREATE INDEX IF NOT EXISTS idx_purchase_order_items_product
    ON purchase_order_items(product_id, purchase_order_id);
CREATE TABLE IF NOT EXISTS purchase_return_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    customer_name TEXT NOT NULL,
    source_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    order_no TEXT NOT NULL UNIQUE,
    business_date TEXT NOT NULL,
    total_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(total_amount_cents >= 0),
    status TEXT NOT NULL CHECK(status IN ('draft','saved','void')),
    notes TEXT NOT NULL DEFAULT '',
    request_key TEXT NOT NULL UNIQUE,
    creation_payload_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    posted_once INTEGER NOT NULL DEFAULT 0 CHECK(posted_once IN (0,1)),
    void_reason TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    delete_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- 历史遗留：旧版退拿货单号流水表。退拿货已改用与拿货共用的
-- purchase_number_sequences，本表仅为兼容既有数据库保留，不再写入。
CREATE TABLE IF NOT EXISTS purchase_return_number_sequences (
    business_date TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL CHECK(last_sequence BETWEEN 1 AND 9999)
);
-- 拿货/退拿货共用同一套 NH 日流水（对齐销售+退货共用 MD 的规则）。
CREATE TABLE IF NOT EXISTS purchase_number_sequences (
    business_date TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL CHECK(last_sequence BETWEEN 1 AND 9999)
);
-- 销售/退货共用 MD 日流水的**已用高位**。正常取号仍以 orders 表为准；本表只在
-- 「清空所有数据」时写入清空前的当日最大流水，保证同一天的新单不会与已打出的
-- 纸质单撞号（清空后 orders 为空，仅靠它才能续排高位）。
CREATE TABLE IF NOT EXISTS order_number_sequences (
    business_date TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL CHECK(last_sequence BETWEEN 1 AND 9999)
);
CREATE TABLE IF NOT EXISTS purchase_return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_return_id INTEGER NOT NULL REFERENCES purchase_return_orders(id) ON DELETE CASCADE,
    source_item_id INTEGER NOT NULL REFERENCES purchase_order_items(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,
    quantity_3dp INTEGER NOT NULL CHECK(quantity_3dp > 0),
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents >= 0),
    unit_cost_micro INTEGER,
    cost_total_micro INTEGER CHECK(cost_total_micro >= 0),
    posting_seq INTEGER REFERENCES inventory_postings(posting_seq),
    UNIQUE(purchase_return_id, source_item_id)
);
CREATE INDEX IF NOT EXISTS idx_purchase_return_source ON purchase_return_orders(source_order_id);
CREATE INDEX IF NOT EXISTS idx_purchase_return_customer ON purchase_return_orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_purchase_return_item_source ON purchase_return_items(source_item_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_return_item_unique_source
    ON purchase_return_items(purchase_return_id, source_item_id) WHERE source_item_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    phone TEXT DEFAULT '',
    address TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    origin TEXT,
    pinyin_initials TEXT DEFAULT '',
    pinyin_full TEXT DEFAULT '',
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
    delete_reason TEXT DEFAULT '',
    source_order_id INTEGER REFERENCES orders(id),
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    request_key TEXT,
    payload_hash TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    product_name TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents >= 0),
    unit_cost_micro INTEGER,
    cost_total_micro INTEGER,
    cost_source TEXT,
    source_item_id INTEGER REFERENCES order_items(id)
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

CREATE TABLE IF NOT EXISTS reconciliation_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    order_type_filter TEXT NOT NULL DEFAULT 'all'
        CHECK(order_type_filter IN ('all','sale','return','customer_return','purchase','purchase' || '_return')),
    scope_json TEXT NOT NULL,
    selected_json TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scope_version INTEGER NOT NULL DEFAULT 1 CHECK(scope_version IN (1,2))
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_snapshots_customer
    ON reconciliation_snapshots(customer_id, created_at);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
