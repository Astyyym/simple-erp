"""v15 数据层：删除库存系统（3 张库存表 + 9 列）的迁移。

用户 2026-10-16 决策 ③：物理删表删列（老库不可逆）。本文件只测数据层：迁移本身、
`init_db()` 接线、schema 版本、业务数据守恒、迁移前备份、幂等，以及两种真实库形态：

- `tests/fixtures/schema_v14_full.sql`：改动前的完整 v14 schema（含库存三表与全部成本列）。
- `tests/fixtures/schema_v13_pre_decoupling.sql`：v13 解耦迁移**之前**的形态
  （`spec` 列 + `source_order_id`/`source_item_id` NOT NULL）。仓库里只有这一种形态
  会让 `migrate_purchase_return_decoupling` 真的动手，所以它才真正验证了
  v13 → v14 → v15 整条链（v13 会 rename 两张退拿货表，SQLite 把新表名写成带引号形态，
  v14 的表名正则必须兼容它）。
"""
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from erp import config as config_module
from erp import db as db_module
from erp.db import CURRENT_SCHEMA_VERSION, get_db, init_db

FIXTURES = Path(__file__).parent / "fixtures"
V14_FIXTURE = FIXTURES / "schema_v14_full.sql"
V13_FIXTURE = FIXTURES / "schema_v13_pre_decoupling.sql"

DROPPED_TABLES = ("inventory_postings", "inventory_initializations", "product_inventory_state")
DROPPED_COLUMNS = {
    "products": ("safety_stock_3dp", "default_price_cents"),
    "order_items": ("unit_cost_micro", "cost_total_micro", "cost_source"),
    "purchase_return_items": ("unit_cost_micro", "cost_total_micro", "posting_seq"),
    "purchase_return_orders": ("posted_once",),
}
SPEC_TABLES = ("products", "order_items", "purchase_order_items", "purchase_return_items")
# 迁移后必须逐行守恒的业务表（库存三表不在其中——它们被删掉）。
BUSINESS_TABLES = (
    "products", "customers", "customer_prices", "orders", "order_items",
    "payments", "adjustments", "audit_logs", "purchase_orders",
    "purchase_order_items", "purchase_return_orders", "purchase_return_items",
)

# 全虚构数据；不含任何真实客户/商品/订单。列名在两种夹具里都成立。
SEED_SQL = """
INSERT INTO customers(id,name,opening_balance_cents) VALUES (1,'虚构客户甲',12345),(2,'虚构客户乙',0);
INSERT INTO products(id,name,unit,default_price_cents,safety_stock_3dp) VALUES
    (1,'虚构闸阀','个',1000,5),(2,'虚构水枪','把',2000,0);
INSERT INTO orders(id,order_no,customer_id,order_date,total_amount_cents,status,order_type) VALUES
    (1,'MD202610150001',1,'2026-10-15',10000,'saved','sale');
INSERT INTO order_items(id,order_id,product_id,product_name,unit,quantity,unit_price_cents,subtotal_cents,unit_cost_micro,cost_total_micro,cost_source) VALUES
    (1,1,1,'虚构闸阀','个','1.000',10000,10000,6000000,6000000,NULL);
INSERT INTO purchase_orders(id,customer_id,customer_name,order_no,business_date,total_amount_cents,status,request_key,payload_hash) VALUES
    (1,1,'虚构客户甲','NH202610150001','2026-10-15',5000,'saved','pk1','ph1');
INSERT INTO purchase_order_items(id,purchase_order_id,product_id,product_name,unit,quantity_3dp,unit_cost_cents,subtotal_cents) VALUES
    (1,1,1,'虚构闸阀','个',2000,2500,5000);
INSERT INTO purchase_return_orders(id,customer_id,customer_name,source_order_id,order_no,business_date,total_amount_cents,status,request_key,creation_payload_hash,version,posted_once) VALUES
    (1,1,'虚构客户甲',1,'NH202610150002','2026-10-15',2500,'saved','pk2','ph2',0,1);
INSERT INTO purchase_return_items(id,purchase_return_id,source_item_id,product_id,product_name,unit,quantity_3dp,unit_price_cents,subtotal_cents,unit_cost_micro,cost_total_micro,posting_seq) VALUES
    (1,1,1,1,'虚构闸阀','个',1000,2500,2500,2500000,2500000,1);
INSERT INTO inventory_postings(posting_seq,product_id,source_type,source_id,quantity_delta_3dp,cost_delta_micro,business_date) VALUES
    (1,1,'purchase','1',2000,5000000,'2026-10-15'),
    (2,1,'sale','1',-1000,-3000000,'2026-10-15');
INSERT INTO inventory_initializations(product_id,quantity_3dp,cost_total_micro,business_date,request_key,payload_hash) VALUES
    (1,1000,3000000,'2026-10-01','pk3','ph3');
INSERT INTO product_inventory_state(product_id,enabled,quantity_3dp,cost_total_micro,avg_cost_micro,last_posting_seq) VALUES
    (1,1,2000,5000000,2500000,2);
INSERT INTO payments(id,customer_id,amount_cents,payment_date) VALUES (1,1,3000,'2026-10-15');
INSERT INTO adjustments(id,customer_id,amount_cents,reason) VALUES (1,1,-200,'虚构调整');
INSERT INTO audit_logs(id,action,target_type,target_id,summary) VALUES (1,'test','order','1','虚构审计');
INSERT INTO customer_prices(customer_id,product_id,price_cents) VALUES (1,1,900);
"""


def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _table_names(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _notnull(conn, table, column):
    return [row[3] for row in conn.execute(f"PRAGMA table_info({table})") if row[1] == column]


def _build_from_fixture(fixture: Path, version: int) -> Path:
    """按夹具建一个「旧版本形态」的库并盖旧版本号。

    版本号必须退回旧值：v14/v15 的迁移对「schema 版本 ≥ 当前版本」的库直接跳过
    （与 `migrate_purchase_return_decoupling` 同规矩），否则测试是空转的。
    """
    db_file = db_module.db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(fixture.read_text(encoding="utf-8"))
        conn.executescript(SEED_SQL)
        conn.execute("INSERT INTO schema_version(version, notes) VALUES (?, ?)", (version, "synthetic fixture"))
        conn.commit()
    finally:
        conn.close()
    return db_file


def counts():
    with get_db() as conn:
        names = _table_names(conn)
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in BUSINESS_TABLES
            if table in names
        }


def key_fingerprint():
    """只取迁移后仍存在的业务列，因此迁移前后可直接比较。"""
    with get_db() as conn:
        return {
            "products": [tuple(r) for r in conn.execute("SELECT id,name,unit FROM products ORDER BY id")],
            "customers": [tuple(r) for r in conn.execute("SELECT id,name,opening_balance_cents FROM customers ORDER BY id")],
            "customer_prices": [tuple(r) for r in conn.execute("SELECT customer_id,product_id,price_cents FROM customer_prices ORDER BY customer_id,product_id")],
            "orders": [tuple(r) for r in conn.execute("SELECT id,order_no,total_amount_cents,status FROM orders ORDER BY id")],
            "order_items": [tuple(r) for r in conn.execute("SELECT id,product_name,quantity,unit_price_cents,subtotal_cents,source_item_id FROM order_items ORDER BY id")],
            "purchase_orders": [tuple(r) for r in conn.execute("SELECT id,order_no,total_amount_cents FROM purchase_orders ORDER BY id")],
            "purchase_order_items": [tuple(r) for r in conn.execute("SELECT id,product_name,quantity_3dp,subtotal_cents FROM purchase_order_items ORDER BY id")],
            "purchase_return_orders": [tuple(r) for r in conn.execute("SELECT id,order_no,total_amount_cents,source_order_id FROM purchase_return_orders ORDER BY id")],
            "purchase_return_items": [tuple(r) for r in conn.execute("SELECT id,product_name,quantity_3dp,subtotal_cents,source_item_id FROM purchase_return_items ORDER BY id")],
            "payments": [tuple(r) for r in conn.execute("SELECT id,amount_cents,payment_date FROM payments ORDER BY id")],
            "adjustments": [tuple(r) for r in conn.execute("SELECT id,amount_cents,reason FROM adjustments ORDER BY id")],
            "audit_logs": [tuple(r) for r in conn.execute("SELECT id,action,target_id,summary FROM audit_logs ORDER BY id")],
        }


def backup_zips():
    return sorted(config_module.project_path("backups").glob("*.zip"))


def _assert_inventory_system_gone(conn):
    names = _table_names(conn)
    for table in DROPPED_TABLES:
        assert table not in names, f"库存表仍在：{table}"
    for table, columns in DROPPED_COLUMNS.items():
        present = _columns(conn, table)
        for column in columns:
            assert column not in present, f"列仍在：{table}.{column}"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 15
    assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=15").fetchone()[0] == 1


def test_v14_database_drops_inventory_tables_and_columns():
    _build_from_fixture(V14_FIXTURE, 14)
    with get_db() as conn:
        assert set(DROPPED_TABLES) <= _table_names(conn)
        assert "safety_stock_3dp" in _columns(conn, "products")
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings").fetchone()[0] == 2
    before_counts, before_keys = counts(), key_fingerprint()
    assert backup_zips() == []

    init_db()

    with get_db() as conn:
        _assert_inventory_system_gone(conn)
    assert counts() == before_counts
    assert key_fingerprint() == before_keys
    assert "purchase_return_orders" in before_keys  # 拿货/退拿货链路没被误伤（决策 ①）


def test_migration_backs_up_the_database_before_dropping_anything():
    _build_from_fixture(V14_FIXTURE, 14)
    assert backup_zips() == []

    init_db()

    zips = [path for path in backup_zips() if "pre_drop_inventory" in path.name]
    assert len(zips) == 1, [path.name for path in backup_zips()]
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zips[0]) as archive:
            assert "erp.db" in archive.namelist()
            archive.extract("erp.db", tmp)
        old = sqlite3.connect(Path(tmp) / "erp.db")
        try:
            # 备份必须是「删之前」的库：库存三表与九列都还在，版本还是 14。
            assert set(DROPPED_TABLES) <= _table_names(old)
            assert "safety_stock_3dp" in _columns(old, "products")
            assert "default_price_cents" in _columns(old, "products")
            assert "posted_once" in _columns(old, "purchase_return_orders")
            assert old.execute("SELECT COUNT(*) FROM inventory_postings").fetchone()[0] == 2
            assert old.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 14
        finally:
            old.close()


def test_migration_is_idempotent_and_does_not_back_up_again():
    _build_from_fixture(V14_FIXTURE, 14)
    init_db()
    first = (counts(), key_fingerprint(), len(backup_zips()))

    init_db()  # 已是 v15 形态：无可删内容 → 直接返回，不再产生备份

    assert (counts(), key_fingerprint(), len(backup_zips())) == first


def test_v13_form_database_upgrades_through_the_whole_chain():
    """v13 解耦真的会触发的库 → v13 → v14 → v15 整条链。

    这条路径在仓库原有夹具里从未被走过（`legacy_schema_v2.sql` 没有拿货/退拿货表）。
    """
    _build_from_fixture(V13_FIXTURE, 12)
    with get_db() as conn:
        assert "spec" in _columns(conn, "products")
        assert _notnull(conn, "purchase_return_orders", "source_order_id") == [1]
        assert _notnull(conn, "purchase_return_items", "source_item_id") == [1]
    before_counts, before_keys = counts(), key_fingerprint()

    init_db()

    with get_db() as conn:
        _assert_inventory_system_gone(conn)
        for table in SPEC_TABLES:
            assert "spec" not in _columns(conn, table), table
        # v13 解耦确实动手了：source 列被放开成可空。
        assert _notnull(conn, "purchase_return_orders", "source_order_id") == [0]
        assert _notnull(conn, "purchase_return_items", "source_item_id") == [0]
        # v13 的重建 rename 过这两张表 → SQLite 把表名写成带引号形态；
        # v14 的表名正则必须兼容它，否则 init_db 会在这一步抛错中止。
        stored = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='purchase_return_items'"
        ).fetchone()[0]
        assert stored.startswith('CREATE TABLE "purchase_return_items"')
    assert counts() == before_counts
    assert key_fingerprint() == before_keys


def test_fresh_database_is_v15_and_matches_migrated_columns(monkeypatch):
    _build_from_fixture(V14_FIXTURE, 14)
    init_db()
    with get_db() as conn:
        migrated = {table: _columns(conn, table) for table in DROPPED_COLUMNS}

    fresh = db_module.db_path().parent / "fresh_erp.db"
    monkeypatch.setattr(db_module, "db_path", lambda: fresh)
    init_db()

    conn = sqlite3.connect(fresh)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in DROPPED_TABLES:
            assert table not in names, table
        for table in DROPPED_COLUMNS:
            assert _columns(conn, table) == migrated[table], table
    finally:
        conn.close()


def test_column_strip_helper_handles_both_real_ddl_shapes():
    """剥离器必须同时处理「一列一行」与 `ALTER TABLE ADD COLUMN` 追加到收尾行的列。

    后者是 v3.x 老库的真实形态（实测：`, safety_stock_3dp INTEGER NOT NULL DEFAULT 0 CHECK(...))`
    被追加在收尾行上），按行删会定位不到、直接抛错中止迁移。
    """
    line_form = (
        'CREATE TABLE "products" (\n'
        "    id INTEGER PRIMARY KEY,\n"
        "    name TEXT NOT NULL,\n"
        "    default_price_cents INTEGER NOT NULL CHECK(default_price_cents >= 0),\n"
        "    safety_stock_3dp INTEGER NOT NULL DEFAULT 0 CHECK(safety_stock_3dp >= 0)\n"
        ")"
    )
    stripped = db_module._strip_column_lines_from_table_ddl(
        line_form, ("default_price_cents", "safety_stock_3dp")
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(stripped)  # 结果必须仍是合法建表语句
    assert _columns(conn, "products") == ["id", "name"]

    source = sqlite3.connect(":memory:")
    source.execute(
        "CREATE TABLE products (\n"
        "    id INTEGER PRIMARY KEY,\n"
        "    name TEXT NOT NULL,\n"
        "    default_price_cents INTEGER NOT NULL CHECK(default_price_cents >= 0)\n"
        ")"
    )
    source.execute(
        "ALTER TABLE products ADD COLUMN safety_stock_3dp INTEGER NOT NULL DEFAULT 0"
        " CHECK(safety_stock_3dp >= 0)"
    )
    appended = source.execute("SELECT sql FROM sqlite_master WHERE name='products'").fetchone()[0]
    assert "\n, safety_stock_3dp" in appended, appended  # 夹具确实是「追加到收尾行」的形态
    stripped_appended = db_module._strip_column_lines_from_table_ddl(
        appended, ("default_price_cents", "safety_stock_3dp")
    )
    check = sqlite3.connect(":memory:")
    check.execute(stripped_appended)
    assert _columns(check, "products") == ["id", "name"]

    # 定位不到的列必须报错中止，而不是静默留下该列。
    with pytest.raises(RuntimeError, match="定位列"):
        db_module._strip_column_lines_from_table_ddl(line_form, ("no_such_column",))
