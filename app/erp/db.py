import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from datetime import datetime, timedelta

from .config import project_path

CURRENT_SCHEMA_VERSION = 14

# Phase 2（v14）：商品身份 = 单一名称。这四张表都带 `spec` 快照列，迁移时整表重建。
PRODUCT_SPEC_TABLES = ("products", "order_items", "purchase_order_items", "purchase_return_items")
# 合并公式必须与 tools/precheck_product_identity_merge.py 逐字一致。
MERGED_PRODUCT_NAME_SQL = "TRIM(COALESCE(name, '')) || TRIM(COALESCE(spec, ''))"

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
    # v14：spec 并进 name（必须在 apply_schema 之前——SCHEMA_SQL 已无 spec 列，
    # 老库要先被重建，否则两套 schema 分叉）。内部会先预检、再自动备份。
    migrate_merge_product_spec_into_name()
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
        ensure_order_item_cost_source(conn)
        ensure_return_relation_fields(conn)
        ensure_order_submission_fields(conn)
        ensure_master_data_origin_fields(conn)
        ensure_product_brand_field(conn)
        ensure_initialization_cost_source(conn)
        ensure_customer_pinyin_field(conn)
        ensure_pinyin_full_fields(conn)
        backfill_product_pinyin_initials(conn)
        backfill_customer_pinyin_initials(conn)
        backfill_product_pinyin_full(conn)
        backfill_customer_pinyin_full(conn)
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


def ensure_master_data_origin_fields(conn: sqlite3.Connection) -> None:
    """G-0a：商品/客户档案来源标记。

    取值 `manual`（手工新建）/`import`（批量导入）/`order`（开单时自动建档）。
    旧数据一律留空（NULL），读取时按「未知」处理——**不根据 usage_count 等间接指标猜测来源**。
    幂等：列已存在即跳过。
    """
    for table in ("products", "customers"):
        if "origin" not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN origin TEXT")


def ensure_product_brand_field(conn: sqlite3.Connection) -> None:
    """E-2：商品品牌（可选）。

    旧软件的商品档案带品牌，本系统原先无此字段，迁移即丢数据。取值自由文本；
    **可空、不做必填、不参与业务唯一键**（唯一键是 `TRIM(name)`），
    因此加列不会阻塞迁移、不会与既有商品冲突。幂等：列已存在即跳过。
    """
    if "brand" not in _columns(conn, "products"):
        conn.execute("ALTER TABLE products ADD COLUMN brand TEXT")


def ensure_initialization_cost_source(conn: sqlite3.Connection) -> None:
    """C-1：期初成本来源结构化表达。

    取值 `known`（已知成本）/`estimated`（用户确认估算）/`zero`（明确零成本）。
    旧数据留空，读取时按「未标注」处理，不猜。
    """
    if "cost_source" not in _columns(conn, "inventory_initializations"):
        conn.execute("ALTER TABLE inventory_initializations ADD COLUMN cost_source TEXT NOT NULL DEFAULT ''")


def ensure_product_inventory_fields(conn: sqlite3.Connection) -> None:
    if "safety_stock_3dp" not in _columns(conn, "products"):
        conn.execute("ALTER TABLE products ADD COLUMN safety_stock_3dp INTEGER NOT NULL DEFAULT 0 CHECK(safety_stock_3dp >= 0)")


def backfill_product_pinyin_initials(conn: sqlite3.Connection) -> None:
    """E7：为 pinyin_initials 为空的商品回填拼音首字母（幂等，不覆盖已有值）。

    纯派生字段，无业务影响；旧库/旧商品升级后拼音搜索才能生效。
    """
    from erp.utils.pinyin import pinyin_initials

    empty_rows = conn.execute(
        "SELECT id, name FROM products WHERE COALESCE(pinyin_initials, '') = ''"
    ).fetchall()
    for row in empty_rows:
        initials = pinyin_initials(row["name"])
        if initials:
            conn.execute("UPDATE products SET pinyin_initials=? WHERE id=?", (initials, row["id"]))


def ensure_customer_pinyin_field(conn: sqlite3.Connection) -> None:
    """Phase 3 S3：客户拼音首字母（纯派生字段，用于客户联想/搜索）。

    与 `products.pinyin_initials` 同规矩：可空、不参与业务唯一键、幂等加列。
    **幂等加列不推进 schema 版本**（与既有 `ensure_*` 一致）。
    """
    if "pinyin_initials" not in _columns(conn, "customers"):
        conn.execute("ALTER TABLE customers ADD COLUMN pinyin_initials TEXT DEFAULT ''")


def backfill_customer_pinyin_initials(conn: sqlite3.Connection) -> None:
    """客户拼音首字母回填（幂等，不覆盖已有值）。

    同 `backfill_product_pinyin_initials`：只补空值，纯派生、无业务影响。
    """
    from erp.utils.pinyin import pinyin_initials

    empty_rows = conn.execute(
        "SELECT id, name FROM customers WHERE COALESCE(pinyin_initials, '') = ''"
    ).fetchall()
    for row in empty_rows:
        initials = pinyin_initials(row["name"])
        if initials:
            conn.execute("UPDATE customers SET pinyin_initials=? WHERE id=?", (initials, row["id"]))


def ensure_pinyin_full_fields(conn: sqlite3.Connection) -> None:
    """全拼派生列（商品 + 客户），2026-10-14 加。

    首字母只能命中「xf」这类缩写，门店手输完整拼音（xiaofang / shuiqiang）匹配不到；
    `pinyin_full` 与 `pinyin_initials` 并存，两者都不参与业务唯一键。
    **幂等加列，不推进 schema 版本**（与既有 `ensure_*` 一致）。
    """
    for table in ("products", "customers"):
        if "pinyin_full" not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN pinyin_full TEXT DEFAULT ''")


def backfill_product_pinyin_full(conn: sqlite3.Connection) -> None:
    """商品全拼回填（幂等，只补空值，不覆盖已有值）。"""
    from erp.utils.pinyin import pinyin_full

    empty_rows = conn.execute(
        "SELECT id, name FROM products WHERE COALESCE(pinyin_full, '') = ''"
    ).fetchall()
    for row in empty_rows:
        full = pinyin_full(row["name"])
        if full:
            conn.execute("UPDATE products SET pinyin_full=? WHERE id=?", (full, row["id"]))


def backfill_customer_pinyin_full(conn: sqlite3.Connection) -> None:
    """客户全拼回填（幂等，只补空值，不覆盖已有值）。"""
    from erp.utils.pinyin import pinyin_full

    empty_rows = conn.execute(
        "SELECT id, name FROM customers WHERE COALESCE(pinyin_full, '') = ''"
    ).fetchall()
    for row in empty_rows:
        full = pinyin_full(row["name"])
        if full:
            conn.execute("UPDATE customers SET pinyin_full=? WHERE id=?", (full, row["id"]))


def ensure_product_identity_constraint(conn: sqlite3.Connection) -> None:
    """Keep the product name unique, including soft-deleted rows.

    v14 起商品身份只有「名称」一列（`spec` 已并入名称），故唯一键与冲突提示
    都不再提型号。
    """
    conflicts = conn.execute(
        """
        SELECT TRIM(name) AS normalized_name,
               COUNT(*) AS row_count
        FROM products
        GROUP BY TRIM(name)
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if conflicts is not None:
        raise RuntimeError(f"商品名称存在重复，无法启用唯一约束: {conflicts['normalized_name']}")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_business_identity
        ON products(TRIM(name))
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


def ensure_order_item_cost_source(conn: sqlite3.Connection) -> None:
    """标记成本来源：NULL=销售时真实快照（不可改），'backfill'=人工补录（可再修正）。

    幂等加列，**不推进** `CURRENT_SCHEMA_VERSION`——与 `ensure_master_data_origin_fields`、
    `ensure_product_brand_field`、`ensure_initialization_cost_source` 同款惯例。
    旧数据一律留空，即按真实快照处理。
    """
    if "cost_source" not in _columns(conn, "order_items"):
        conn.execute("ALTER TABLE order_items ADD COLUMN cost_source TEXT")


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
    -- 保留 `spec`：这是 v13 老库的重建路径，先原样搬列，紧接着由 v14 的
    -- migrate_merge_product_spec_into_name() 把型号并进名称并删列（顺序在 init_db 里有保证），
    -- 这样老库的型号数据不会在两步之间被丢掉。
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


def _strip_spec_column_from_table_ddl(ddl: str) -> str:
    """从庫内建表语句里删掉 `spec` 列那一行（SQLite 不能 ALTER DROP COLUMN）。"""
    lines = ddl.splitlines()
    kept = [line for line in lines if not re.match(r"^\s*spec\s", line)]
    if len(kept) != len(lines) - 1:
        raise RuntimeError(f"无法从建表语句里唯一定位 spec 列，迁移中止：{ddl}")
    return "\n".join(kept)


def migrate_merge_product_spec_into_name() -> None:
    """v14：把 `spec` 并进 `name` —— 商品身份只剩单一名称。

    规则（用户 2026-10-12 拍板）：**直接拼接、中间不加任何字符**（`闸阀` + `DN50`
    → `闸阀DN50`），规格不再单独保留。

    四张带 spec 快照列的表整体重建（SQLite 不能删列）：沿用库内原 DDL 建新表（去掉 spec 行）
    → 复制数据并把名称拼上型号（`products.name` 与明细表的 `product_name` 都拼）
    → DROP + RENAME → 重建索引（唯一索引改成 `ON products(TRIM(name))`）→ 恢复自增高位
    → 外键校验（不过就整笔回滚）。

    两条硬约束：
    - 合并后会撞名 → **先预检、中止且不做任何写入**，列出冲突清单（绝不静默合并）。
    - 真的开始写库之前，先自动做一次整库快照备份（新装空库没有 spec 列，直接跳过、不备份）。
    """
    path = db_path()
    if not path.exists():
        return
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='products'").fetchone() is None:
            return
        if "spec" not in {row[1] for row in conn.execute("PRAGMA table_info(products)")}:
            return  # 已经是 v14 形态
        # 与 migrate_purchase_return_decoupling 同规矩：不对更新版本程序建的库动 DDL。
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone() is not None:
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            if version is not None and version >= CURRENT_SCHEMA_VERSION:
                return

        empty_names = conn.execute(
            f"SELECT COUNT(*) FROM products WHERE {MERGED_PRODUCT_NAME_SQL} = ''"
        ).fetchone()[0]
        if empty_names:
            raise RuntimeError(
                f"有 {empty_names} 个商品的名称与型号都为空，合并后名称会变成空串，迁移已中止（未做任何写入）。"
                "请先在旧版本里给这些商品补一个名称。"
            )
        conflicts = conn.execute(
            f"""
            SELECT {MERGED_PRODUCT_NAME_SQL} AS merged_name,
                   COUNT(*) AS row_count,
                   GROUP_CONCAT(id) AS ids
            FROM products
            GROUP BY merged_name
            HAVING COUNT(*) > 1
            ORDER BY row_count DESC, merged_name
            """
        ).fetchall()
        if conflicts:
            detail = "；".join(f"「{row['merged_name']}」id {row['ids']}" for row in conflicts[:5])
            more = "" if len(conflicts) <= 5 else f"（另有 {len(conflicts) - 5} 组未列出）"
            raise RuntimeError(
                f"商品名称与型号合并后会出现 {len(conflicts)} 组重名，迁移已中止，未做任何写入。"
                f"冲突清单：{detail}{more}。请先在旧版本里把其中一个商品改名，"
                "或运行 tools/precheck_product_identity_merge.py 查看完整清单后再升级。"
            )

        table_sql: dict[str, str] = {}
        for table in PRODUCT_SPEC_TABLES:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None or not row["sql"]:
                continue
            if "spec" not in {col[1] for col in conn.execute(f"PRAGMA table_info({table})")}:
                continue
            table_sql[table] = row["sql"]
        if not table_sql:
            return

        index_sql = [
            (name, sql)
            for table in table_sql
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            )
        ]
        sequences: dict[str, int] = {}
        try:
            for table in table_sql:
                row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
                if row is not None:
                    sequences[table] = int(row[0])
        except sqlite3.OperationalError:
            sequences = {}

        # 从这里开始才是写库：先整库快照（erp.db + config.json + 商品图片 + 旧账存档）。
        from erp.utils.backup import create_backup  # 延迟导入避免与 utils.backup 循环引用

        create_backup("pre_merge_product_spec")

        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            for table, ddl in table_sql.items():
                new_table = f"{table}_spec_merged_new"
                new_ddl, replaced = re.subn(
                    r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?" + re.escape(table) + r"\b",
                    f"CREATE TABLE {new_table}",
                    _strip_spec_column_from_table_ddl(ddl),
                    count=1,
                )
                if not replaced:
                    raise RuntimeError(f"无法重命名建表语句里的表名，迁移中止：{table}")
                kept = [col[1] for col in conn.execute(f"PRAGMA table_info({table})") if col[1] != "spec"]
                selects = []
                for column in kept:
                    if column == "name":
                        selects.append(MERGED_PRODUCT_NAME_SQL)
                    elif column == "product_name":
                        selects.append("TRIM(COALESCE(product_name, '')) || TRIM(COALESCE(spec, ''))")
                    else:
                        selects.append(f'"{column}"')
                conn.execute(new_ddl)
                conn.execute(
                    f"INSERT INTO {new_table} ({', '.join(kept)}) SELECT {', '.join(selects)} FROM {table}"
                )
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {new_table} RENAME TO {table}")
            for name, sql in index_sql:
                if name == "idx_products_business_identity":
                    continue  # 旧定义含 spec，下面用新定义重建
                conn.execute(sql)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_business_identity ON products(TRIM(name))"
            )
            for table, seq in sequences.items():
                conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?", (seq, table))
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"商品身份合并迁移后外键校验失败，已回滚: {[tuple(row) for row in violations[:3]]}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA legacy_alter_table=OFF")
            conn.execute("PRAGMA foreign_keys=ON")
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
    source_item_id INTEGER REFERENCES purchase_order_items(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
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
