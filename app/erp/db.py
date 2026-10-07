import sqlite3
from contextlib import contextmanager
from pathlib import Path

from datetime import datetime, timedelta

from .config import project_path

CURRENT_SCHEMA_VERSION = 13

# Correlated NOT EXISTS is NULL-safe for historical unbound purchases.
CUSTOMER_REFERENCE_GUARD_SQL = " AND ".join(
    f"NOT EXISTS (SELECT 1 FROM {table} WHERE {table}.customer_id=customers.id)"
    for table in ("orders", "payments", "adjustments", "purchase_orders", "purchase_return_orders", "reconciliation_snapshots", "customer_prices")
)


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
    migrate_purchase_return_decoupling()
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        apply_schema(conn)
        ensure_soft_delete_columns(conn)
        ensure_order_type_column(conn)
        ensure_product_image_column(conn)
        ensure_product_inventory_fields(conn)
        ensure_product_identity_constraint(conn)
        ensure_purchase_order_fields(conn)
        ensure_order_item_cost_fields(conn)
        ensure_return_relation_fields(conn)
        ensure_order_submission_fields(conn)
        purge_expired_recycle_bin(conn)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row[0] != "ok":
            raise RuntimeError(f"数据库完整性检查失败: {row[0]}")


def apply_schema(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if version is not None and version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError("当前程序不支持此较新数据库版本，请使用对应版本程序")
    # executescript otherwise commits DDL before the migration/version boundary.
    conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL)
    ensure_purchase_order_fields(conn)
    # Legacy orders have no request_key: add columns before creating its index.
    ensure_order_submission_fields(conn)
    ensure_reconciliation_scope_version(conn)
    existing = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
    if existing is None:
        conn.execute("INSERT INTO schema_version(version, notes) VALUES (?, ?)", (CURRENT_SCHEMA_VERSION, "initial schema"))
    elif existing["version"] < CURRENT_SCHEMA_VERSION:
        conn.execute("INSERT INTO schema_version(version, notes) VALUES (?, ?)", (CURRENT_SCHEMA_VERSION, "versioned four-source reconciliation snapshots"))


def ensure_reconciliation_scope_version(conn: sqlite3.Connection) -> None:
    """Keep v1 bytes/identities while widening the snapshot filter constraint."""
    if "scope_version" in _columns(conn, "reconciliation_snapshots"):
        return
    indexes = [row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='reconciliation_snapshots' AND sql IS NOT NULL"
    )]
    conn.execute("ALTER TABLE reconciliation_snapshots RENAME TO reconciliation_snapshots_v1")
    conn.execute(RECONCILIATION_SNAPSHOT_SQL)
    conn.execute("""INSERT INTO reconciliation_snapshots
        (id, customer_id, start_date, end_date, order_type_filter, scope_json,
         selected_json, scope_hash, created_at, updated_at, scope_version)
        SELECT id, customer_id, start_date, end_date, order_type_filter, scope_json,
               selected_json, scope_hash, created_at, updated_at, 1
        FROM reconciliation_snapshots_v1""")
    # Preserve AUTOINCREMENT's high-water mark even if the highest ID was deleted.
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='reconciliation_snapshots_v1'").fetchone()
    if sequence:
        conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='reconciliation_snapshots'", (sequence[0],))
    conn.execute("DROP TABLE reconciliation_snapshots_v1")
    for sql in indexes:
        conn.execute(sql)


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


def ensure_product_image_column(conn: sqlite3.Connection) -> None:
    if "image_path" not in _columns(conn, "products"):
        conn.execute("ALTER TABLE products ADD COLUMN image_path TEXT NOT NULL DEFAULT ''")


def ensure_product_inventory_fields(conn: sqlite3.Connection) -> None:
    if "safety_stock_3dp" not in _columns(conn, "products"):
        conn.execute("ALTER TABLE products ADD COLUMN safety_stock_3dp INTEGER NOT NULL DEFAULT 0 CHECK(safety_stock_3dp >= 0)")


def ensure_product_identity_constraint(conn: sqlite3.Connection) -> None:
    """Keep the name/spec business identity unique, including soft-deleted rows."""
    conflicts = conn.execute(
        """
        SELECT TRIM(name) AS normalized_name,
               TRIM(COALESCE(spec, '')) AS normalized_spec,
               COUNT(*) AS row_count
        FROM products
        GROUP BY TRIM(name), TRIM(COALESCE(spec, ''))
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if conflicts is not None:
        spec = conflicts["normalized_spec"] or "（空型号）"
        raise RuntimeError(
            f"商品名称和型号存在重复，无法启用唯一约束: {conflicts['normalized_name']} / {spec}"
        )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_business_identity
        ON products(TRIM(name), TRIM(COALESCE(spec, '')))
        """
    )


def ensure_purchase_order_fields(conn: sqlite3.Connection) -> None:
    """Add recycle-bin fields to purchase orders from the first Phase 2 slice."""
    cols = _columns(conn, "purchase_orders")
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE purchase_orders ADD COLUMN deleted_at TEXT")
    if "delete_reason" not in cols:
        conn.execute("ALTER TABLE purchase_orders ADD COLUMN delete_reason TEXT DEFAULT ''")
    if "void_reason" not in cols:
        conn.execute("ALTER TABLE purchase_orders ADD COLUMN void_reason TEXT DEFAULT ''")
    if "customer_id" not in cols:
        conn.execute("ALTER TABLE purchase_orders ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
    if "customer_name" not in cols:
        conn.execute("ALTER TABLE purchase_orders ADD COLUMN customer_name TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_customer ON purchase_orders(customer_id)")


def ensure_order_item_cost_fields(conn: sqlite3.Connection) -> None:
    """Add nullable sale-time cost snapshots without rewriting legacy orders."""
    cols = _columns(conn, "order_items")
    if "unit_cost_micro" not in cols:
        conn.execute("ALTER TABLE order_items ADD COLUMN unit_cost_micro INTEGER")
    if "cost_total_micro" not in cols:
        conn.execute("ALTER TABLE order_items ADD COLUMN cost_total_micro INTEGER")


def ensure_return_relation_fields(conn: sqlite3.Connection) -> None:
    """Keep inventory returns tied to one immutable sale and its stable rows."""
    order_cols = _columns(conn, "orders")
    if "source_order_id" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN source_order_id INTEGER REFERENCES orders(id)")
    item_cols = _columns(conn, "order_items")
    if "source_item_id" not in item_cols:
        conn.execute("ALTER TABLE order_items ADD COLUMN source_item_id INTEGER REFERENCES order_items(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_source_order ON orders(source_order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_items_source_item ON order_items(source_item_id)")


def ensure_order_submission_fields(conn: sqlite3.Connection) -> None:
    """Add optimistic-version and retry identity fields without rewriting legacy orders."""
    cols = _columns(conn, "orders")
    if "version" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0)")
    if "request_key" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN request_key TEXT")
    if "payload_hash" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN payload_hash TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_request_key ON orders(request_key) WHERE request_key IS NOT NULL"
    )


PURCHASE_RETURN_DECOUPLED_REBUILD_SQL = """
CREATE TABLE purchase_return_orders_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    customer_name TEXT NOT NULL,
    source_order_id INTEGER REFERENCES purchase_orders(id),
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
INSERT INTO purchase_return_orders_new (
    id, customer_id, customer_name, source_order_id, order_no, business_date,
    total_amount_cents, status, notes, request_key, creation_payload_hash,
    version, posted_once, void_reason, deleted_at, delete_reason, created_at, updated_at
) SELECT
    id, customer_id, customer_name, source_order_id, order_no, business_date,
    total_amount_cents, status, notes, request_key, creation_payload_hash,
    version, posted_once, void_reason, deleted_at, delete_reason, created_at, updated_at
FROM purchase_return_orders;
DROP TABLE purchase_return_orders;
ALTER TABLE purchase_return_orders_new RENAME TO purchase_return_orders;

CREATE TABLE purchase_return_items_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_return_id INTEGER NOT NULL REFERENCES purchase_return_orders(id) ON DELETE CASCADE,
    source_item_id INTEGER REFERENCES purchase_order_items(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,
    quantity_3dp INTEGER NOT NULL CHECK(quantity_3dp > 0),
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents >= 0),
    unit_cost_micro INTEGER,
    cost_total_micro INTEGER CHECK(cost_total_micro >= 0),
    posting_seq INTEGER REFERENCES inventory_postings(posting_seq)
);
INSERT INTO purchase_return_items_new (
    id, purchase_return_id, source_item_id, product_id, product_name, spec, unit,
    quantity_3dp, unit_price_cents, subtotal_cents, unit_cost_micro, cost_total_micro, posting_seq
) SELECT
    id, purchase_return_id, source_item_id, product_id, product_name, spec, unit,
    quantity_3dp, unit_price_cents, subtotal_cents, unit_cost_micro, cost_total_micro, posting_seq
FROM purchase_return_items;
DROP TABLE purchase_return_items;
ALTER TABLE purchase_return_items_new RENAME TO purchase_return_items;

CREATE INDEX idx_purchase_return_source ON purchase_return_orders(source_order_id);
CREATE INDEX idx_purchase_return_customer ON purchase_return_orders(customer_id);
CREATE INDEX idx_purchase_return_item_source ON purchase_return_items(source_item_id);
CREATE UNIQUE INDEX idx_purchase_return_item_unique_source
    ON purchase_return_items(purchase_return_id, source_item_id) WHERE source_item_id IS NOT NULL;
"""


def migrate_purchase_return_decoupling() -> None:
    """Relax the source coupling on purchase returns in place (schema v13).

    Older databases fix purchase_return_orders.source_order_id and
    purchase_return_items.source_item_id as NOT NULL with a composite UNIQUE,
    which blocks manual (decoupled) returns. SQLite cannot drop NOT NULL/UNIQUE
    through ALTER, so both tables are rebuilt on their own connection outside the
    main init_db transaction, copying every row verbatim.
    """
    path = db_path()
    if not path.exists():
        return
    conn = sqlite3.connect(path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchase_return_orders'").fetchone() is None:
            return
        # Never run migration DDL against a database from a newer program version.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone() is not None:
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            if version is not None and version > CURRENT_SCHEMA_VERSION:
                return
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(purchase_return_orders)")}
        if "source_order_id" not in columns or not columns["source_order_id"][3]:
            return
        conn.executescript(
            "PRAGMA foreign_keys=OFF;\nPRAGMA legacy_alter_table=ON;\nBEGIN IMMEDIATE;\n"
            + PURCHASE_RETURN_DECOUPLED_REBUILD_SQL
            + "\nCOMMIT;\nPRAGMA legacy_alter_table=OFF;\nPRAGMA foreign_keys=ON;\n"
        )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"退拿货解耦迁移后外键校验失败: {violations[:3]}")
    finally:
        conn.close()


class PurgeBlockedByRecycledReference(ValueError):
    """该记录被另一条**仍在回收站**的记录引用，先清引用方再试。

    与「被活单据引用」不同：后者是用户需要先处理的业务事实，前者只是清理顺序问题，
    `_purge_orders_in_safe_order` 会在下一趟自动重试。
    """


def _live_document_reference(conn, kind: str, item_id: int) -> str | None:
    """返回阻止永久删除的「活引用」原因；没有则返回 None。

    只拦**未删除**的引用：引用方自己也进了回收站时，先清引用方再清本单即可
    （`_purge_orders_in_safe_order` 用多趟扫描自动满足这个先后关系）。
    """
    if kind == 'order':
        if conn.execute(
            "SELECT 1 FROM orders WHERE source_order_id=? AND deleted_at IS NULL LIMIT 1", (item_id,)
        ).fetchone():
            return "有未删除的退货单引用它"
        if conn.execute(
            """SELECT 1 FROM order_items d JOIN order_items s ON s.id=d.source_item_id
               JOIN orders o ON o.id=d.order_id
               WHERE s.order_id=? AND o.deleted_at IS NULL LIMIT 1""",
            (item_id,),
        ).fetchone():
            return "有未删除的单据引用了它的明细行"
    elif kind == 'purchase_order':
        if conn.execute(
            "SELECT 1 FROM purchase_return_orders WHERE source_order_id=? AND deleted_at IS NULL LIMIT 1",
            (item_id,),
        ).fetchone():
            return "有未删除的退拿货单引用它"
        if conn.execute(
            """SELECT 1 FROM purchase_return_items d JOIN purchase_order_items s ON s.id=d.source_item_id
               JOIN purchase_return_orders o ON o.id=d.purchase_return_id
               WHERE s.purchase_order_id=? AND o.deleted_at IS NULL LIMIT 1""",
            (item_id,),
        ).fetchone():
            return "有未删除的单据引用了它的明细行"
    return None


_ORDER_PURGE_TABLES = {
    'order': ('orders', 'order_items', 'order_id', ('sale', 'return')),
    'purchase_order': ('purchase_orders', 'purchase_order_items', 'purchase_order_id', ('purchase',)),
    'purchase_return': ('purchase_return_orders', 'purchase_return_items', 'purchase_return_id', ('purchase_return',)),
}


def purge_order_blocker(conn, kind: str, item_id: int) -> str | None:
    """单据能否永久删除；不能则返回可读原因。

    2026-10-07 修：原实现要求 `status == 'draft'`，而正式单据删除走的是「先作废再软删除」，
    状态恒为 `void` → 回收站里的正式单据永远清不掉，七天自动清理也用的是同一判断，
    于是变成只涨不减的永久垃圾。现在改为「在回收站 + 无活引用」即可清理。
    """
    if kind not in _ORDER_PURGE_TABLES:
        return "未知回收站类型"
    table = _ORDER_PURGE_TABLES[kind][0]
    row = conn.execute(f'SELECT deleted_at FROM {table} WHERE id=?', (item_id,)).fetchone()
    if row is None:
        return "记录不存在"
    if row['deleted_at'] is None:
        return "该记录不在回收站"
    return _live_document_reference(conn, kind, item_id)


def physically_deletable_order(conn, kind: str, item_id: int) -> bool:
    """回收站里这条记录现在能否永久删除（供 UI 判定）。"""
    return purge_order_blocker(conn, kind, item_id) is None


def purge_order_item(conn, kind: str, item_id: int) -> None:
    """永久删除一张回收站单据：明细 + 它自己的库存流水 + 单据本体。

    用户口径（2026-10-07）：单据不要了就该连流水一起清掉，不留孤儿记录。
    删除顺序受外键约束：先明细（`purchase_return_items.posting_seq` 指向流水），
    再流水，最后单据本体。`order_items` 因 `order_id ... ON DELETE CASCADE` 本可自动级联，
    但显式删除让意图更清楚、也不依赖级联行为。

    还有一层约束：销售/退货的明细行之间用 `source_item_id` 自引用（退货明细指向销售明细）。
    若被引用的那张单还在库里（哪怕也已删除），删它会撞外键——这时转成可读的 ValueError，
    让调用方走「先清引用方」的多趟流程或把原因告诉用户，而不是抛 500。
    """
    blocker = purge_order_blocker(conn, kind, item_id)
    if blocker is not None:
        raise ValueError(blocker)
    table, items_table, owner, types = _ORDER_PURGE_TABLES[kind]
    try:
        # 1) 明细必须先删：purchase_return_items.posting_seq 引用 inventory_postings。
        conn.execute(f'DELETE FROM {items_table} WHERE {owner}=?', (item_id,))
        # 2) 再删它自己的库存流水（含 :void 反向流水）。
        namespaces = types + tuple(t + '_void' for t in types)
        placeholders = ','.join('?' for _ in namespaces)
        conn.execute(
            f'DELETE FROM inventory_postings WHERE source_type IN ({placeholders}) AND (source_id=? OR source_id LIKE ?)',
            (*namespaces, str(item_id), f'{item_id}:%'),
        )
        # 3) 最后删单据本体。
        conn.execute(f'DELETE FROM {table} WHERE id=?', (item_id,))
    except sqlite3.IntegrityError:
        raise PurgeBlockedByRecycledReference("还有回收站记录引用它的明细，请先清理引用它的记录") from None


def purge_product_blocker(conn, item_id: int) -> str | None:
    """商品能否永久删除；不能则返回可读原因。"""
    row = conn.execute('SELECT deleted_at FROM products WHERE id=?', (item_id,)).fetchone()
    if row is None:
        return "商品不存在"
    if row['deleted_at'] is None:
        return "该商品不在回收站"
    # 未删除的单据还在用它 → 不能删（否则活单据会失去商品指向）。
    live = conn.execute(
        """SELECT 1 FROM order_items i JOIN orders o ON o.id=i.order_id
             WHERE i.product_id=? AND o.deleted_at IS NULL
           UNION ALL
           SELECT 1 FROM purchase_order_items i JOIN purchase_orders o ON o.id=i.purchase_order_id
             WHERE i.product_id=? AND o.deleted_at IS NULL
           UNION ALL
           SELECT 1 FROM purchase_return_items i JOIN purchase_return_orders o ON o.id=i.purchase_return_id
             WHERE i.product_id=? AND o.deleted_at IS NULL
           LIMIT 1""",
        (item_id, item_id, item_id),
    ).fetchone()
    if live is not None:
        return "有未删除的单据还在使用该商品"
    return None


def purge_product_item(conn, item_id: int) -> None:
    """永久删除商品：连带它的库存状态/期初/流水与客户价，历史单据保留名称快照。

    只有在「没有未删除单据引用它」时才允许，因此这里的引用都属于已删除单据，
    把 `product_id` 置空不会影响任何活单据的展示（明细自带商品名/单位/价格快照）。
    """
    blocker = purge_product_blocker(conn, item_id)
    if blocker is not None:
        raise ValueError(blocker)
    conn.execute('UPDATE order_items SET product_id=NULL WHERE product_id=?', (item_id,))
    conn.execute('UPDATE purchase_order_items SET product_id=NULL WHERE product_id=?', (item_id,))
    conn.execute('UPDATE purchase_return_items SET product_id=NULL WHERE product_id=?', (item_id,))
    conn.execute('DELETE FROM customer_prices WHERE product_id=?', (item_id,))
    conn.execute('DELETE FROM inventory_postings WHERE product_id=?', (item_id,))
    conn.execute('DELETE FROM product_inventory_state WHERE product_id=?', (item_id,))
    conn.execute('DELETE FROM inventory_initializations WHERE product_id=?', (item_id,))
    conn.execute('DELETE FROM products WHERE id=?', (item_id,))


def purge_expired_recycle_bin(conn: sqlite3.Connection) -> None:
    """启动时清理超过 7 天的回收站记录。

    顺序很重要，见 `_purge_orders_in_safe_order`：被引用的单据必须先让引用方消失。
    """
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    for kind, table in [('purchase_return', 'purchase_return_orders'), ('order', 'orders'), ('purchase_order', 'purchase_orders')]:
        ids = [
            row['id']
            for row in conn.execute(
                f'SELECT id FROM {table} WHERE deleted_at IS NOT NULL AND deleted_at < ?', (cutoff,)
            ).fetchall()
        ]
        _purge_orders_in_safe_order(conn, kind, ids)
    products = conn.execute(
        'SELECT id FROM products WHERE deleted_at IS NOT NULL AND deleted_at < ?', (cutoff,)
    ).fetchall()
    for row in products:
        if purge_product_blocker(conn, row['id']) is None:
            purge_product_item(conn, row['id'])
    conn.execute(f"DELETE FROM customers WHERE deleted_at IS NOT NULL AND deleted_at < ? AND {CUSTOMER_REFERENCE_GUARD_SQL}", (cutoff,))


def _purge_orders_in_safe_order(conn, kind: str, ids: list[int]) -> tuple[int, list[tuple[int, str]]]:
    """按「引用方先删」的顺序永久删除一批同类单据。

    为什么需要顺序：销售/退货的明细行之间有 `source_item_id` 自引用。一张退货单的明细
    可能指向一张销售单的明细，所以必须先删退货、再删销售，否则删销售明细会撞外键。
    同理退拿货的明细可能引用拿货的明细。

    多趟扫描直到没有进展：每一趟只删「当前已无活引用」的记录，剩下的等引用方被删掉后
    在下一趟通过。这样调用方不需要自己排序，单个删除入口和「清空回收站」都安全。
    """
    remaining = list(dict.fromkeys(ids))
    removed = 0
    failures: list[tuple[int, str]] = []
    while remaining:
        progressed = False
        still_blocked: list[int] = []
        for item_id in remaining:
            blocker = purge_order_blocker(conn, kind, item_id)
            if blocker is not None:
                still_blocked.append((item_id, blocker))
                continue
            try:
                purge_order_item(conn, kind, item_id)
                removed += 1
                progressed = True
            except PurgeBlockedByRecycledReference:
                # 引用方还没被清掉（例如它是另一张更靠后的单据），留到下一趟。
                still_blocked.append((item_id, "被其他回收站记录引用，稍后再试"))
        if not progressed:
            failures = still_blocked
            break
        remaining = [item_id for item_id, _ in still_blocked]
    return removed, failures


RECONCILIATION_SNAPSHOT_SQL = """
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
"""


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
    source_order_id INTEGER REFERENCES purchase_orders(id),
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
CREATE TABLE IF NOT EXISTS purchase_return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_return_id INTEGER NOT NULL REFERENCES purchase_return_orders(id) ON DELETE CASCADE,
    source_item_id INTEGER REFERENCES purchase_order_items(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,
    quantity_3dp INTEGER NOT NULL CHECK(quantity_3dp > 0),
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents >= 0),
    unit_cost_micro INTEGER,
    cost_total_micro INTEGER CHECK(cost_total_micro >= 0),
    posting_seq INTEGER REFERENCES inventory_postings(posting_seq)
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
    spec TEXT DEFAULT '',
    unit TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents >= 0),
    unit_cost_micro INTEGER,
    cost_total_micro INTEGER,
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
""" + RECONCILIATION_SNAPSHOT_SQL + """
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
"""
