"""清空所有数据（Batch C）：清空口径、保留边界、单号高位与安全交互。

契约（用户 2026-10-11 确认）：
- 清空：四类单据与明细、客户/商品资料与客户价、库存状态/流水/期初、收付款与调整、
  对账快照、审计日志、单号序列表、`product_images/`、`temp_pdf/`、`logs/`。
- 保留：`backups/`、`legacy_archive/`、数据目录位置记忆、`data/erp.db` 文件本身。
- 回出厂：公司名/店名、打印信息、界面主题/缩放/字重、打印偏移与缩放。
- 保留单号流水高位：清空后同一天新单从高位续排，避免与已打出的纸质单撞号。
- 安全：必须输入确认词「清空」；执行前自动 `create_backup("pre_purge")`；失败不动数据。
"""

from pathlib import Path

import pytest

from erp import create_app
from erp.config import load_config, save_config
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp.utils.backup import backup_dir, list_backups
from erp.utils.legacy_archive import archive_dir
from erp.utils.reset import CLEARED_TABLES, purge_all_data

BUSINESS_TABLES = (
    "orders", "order_items", "purchase_orders", "purchase_order_items",
    "purchase_return_orders", "purchase_return_items",
    "customers", "customer_prices", "products", "product_inventory_state",
    "inventory_postings", "inventory_initializations",
    "payments", "adjustments", "reconciliation_snapshots", "audit_logs",
)


def _counts() -> dict[str, int]:
    with get_db() as conn:
        return {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in BUSINESS_TABLES
        }


def _seed_business_data():
    init_db()
    customer_id = create_customer("清空客户", opening_balance_cents=12345)
    create_order_from_typed_rows(
        customer_id,
        "MD202610110007",
        [{"product_name": "清空闸阀", "unit": "只", "unit_price_yuan": "10", "quantity": "2"}],
        status="saved",
        order_date="2026-10-11",
    )
    return customer_id


def test_purge_clears_every_business_table():
    _seed_business_data()
    before = _counts()
    assert sum(before.values()) > 0, "种子数据必须先真的写进库"

    result = purge_all_data()

    after = _counts()
    assert all(count == 0 for count in after.values()), after
    assert result["cleared_counts"]["orders"] >= 1
    assert result["cleared_counts"]["customers"] >= 1
    # 清表不删库：文件仍在，且完整性检查通过。
    from erp.db import db_path, integrity_check
    assert db_path().exists()
    assert integrity_check() == "ok"


def test_purge_keeps_backups_and_legacy_archive_and_data_root_memory(tmp_path):
    init_db()
    from erp.utils.backup import create_backup
    from erp.utils.legacy_archive import save_archive

    create_backup("manual")
    archive_dir().mkdir(parents=True, exist_ok=True)
    (archive_dir() / "old_debts.csv").write_text("客户,金额\n甲,100\n", encoding="utf-8")
    (archive_dir() / "archive_index.json").write_text(
        '[{"name": "old_debts.csv", "saved_at": "2026-10-01T00:00:00"}]', encoding="utf-8"
    )
    backups_before = {item["name"] for item in list_backups()}
    assert backups_before

    import erp.config as config_module
    location_before = config_module.location_file().read_text(encoding="utf-8")
    data_root_before = str(config_module.data_root())

    purge_all_data()

    assert {item["name"] for item in list_backups()} == backups_before
    assert (archive_dir() / "old_debts.csv").exists()
    assert (archive_dir() / "archive_index.json").exists()
    assert config_module.location_file().read_text(encoding="utf-8") == location_before
    assert str(config_module.data_root()) == data_root_before


def test_purge_clears_images_temp_pdf_and_logs_but_not_backups_dir():
    init_db()
    import erp.config as config_module

    for dirname in ("product_images", "temp_pdf", "logs"):
        directory = config_module.project_path(dirname)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "leftover.txt").write_text("stale", encoding="utf-8")
    backups = backup_dir()
    backups.mkdir(parents=True, exist_ok=True)
    (backups / "keep_me.zip").write_bytes(b"PK\x03\x04keep")

    purge_all_data()

    for dirname in ("product_images", "temp_pdf", "logs"):
        directory = config_module.project_path(dirname)
        assert directory.exists(), f"{dirname} 目录本身应保留"
        assert list(directory.iterdir()) == [], f"{dirname} 内容应清空"
    assert (backups / "keep_me.zip").exists()


def test_purge_resets_config_to_factory_but_keeps_data_root_key_out_of_config():
    init_db()
    save_config({
        "shop_name": "被改过的店名",
        "ui_theme": "dark",
        "ui_scale": "150",
        "ui_font_weight": "strong",
        "print_offset_x_mm": 4,
        "print_offset_y_mm": -3,
        "print_scale": 0.98,
        "print_order_phone": "000-1111",
        "print_maker_name": "制单人甲",
    })
    assert load_config()["shop_name"] == "被改过的店名"

    purge_all_data()

    config = load_config()
    assert config["shop_name"] == "简单ERP"
    assert config["app_name"] == "简单ERP"
    assert config["ui_theme"] == "light"
    assert config["ui_scale"] == "100"
    assert config["ui_font_weight"] == "standard"
    assert config["print_offset_x_mm"] == 0
    assert config["print_offset_y_mm"] == 0
    assert float(config["print_scale"]) == 1.0
    assert config["print_order_phone"] == ""
    assert config["print_maker_name"] == ""
    # 数据根仍可解析（路径记忆未被清）。
    assert config["data_root"]


def test_purge_keeps_md_high_water_so_same_day_numbers_do_not_collide():
    _seed_business_data()  # MD202610110007
    from erp.routes.orders import next_order_no

    purge_all_data()

    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        # 同一天必须让过已打出的 0007，从 0008 续排。
        assert next_order_no(conn, "2026-10-11") == "MD202610110008"
        # 其他日期不受影响，从 0001 开始。
        assert next_order_no(conn, "2026-10-12") == "MD202610120001"
        # 高位已落表，重启后仍然有效。
        row = conn.execute(
            "SELECT last_sequence FROM order_number_sequences WHERE business_date='2026-10-11'"
        ).fetchone()
        assert row is not None and row["last_sequence"] == 7


def test_purge_keeps_nh_high_water_for_same_day_purchase_numbers():
    init_db()
    from erp.services.accounting import create_product
    from erp.services.inventory import create_purchase_order, initialize_product
    from erp.utils.order_numbering import next_nh_order_no

    customer_id = create_customer("清空采购方")
    product_id = create_product("清空采购商品", "C", "个", 1000)
    initialize_product(product_id, "0", "0", "2026-10-11", "清空期初", "purge-init", confirm_zero=True)
    create_purchase_order(
        "NH202610110003", "2026-10-11",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "5"}],
        customer_id=customer_id, request_key="purge-purchase",
    )

    purge_all_data()

    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0
        assert next_nh_order_no(conn, "2026-10-11") == "NH202610110004"


def test_purge_route_requires_confirm_word_and_leaves_data_untouched():
    _seed_business_data()
    before = _counts()
    client = create_app().test_client()

    response = client.post(
        "/settings/purge-all-data", data={"confirm_text": "no"}, follow_redirects=True
    )
    assert response.status_code == 200
    assert "清空已取消" in response.get_data(as_text=True)
    assert _counts() == before


def test_purge_route_backs_up_first_then_clears_and_traces_log():
    _seed_business_data()
    client = create_app().test_client()

    response = client.post(
        "/settings/purge-all-data", data={"confirm_text": "清空"}, follow_redirects=True
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "已清空所有业务数据" in page
    assert "请完全退出程序后重新打开" in page

    # 安全网：清空前一定留下了 pre_purge 备份，且可被恢复流程识别。
    reasons = {item["reason"] for item in list_backups()}
    assert "pre_purge" in reasons
    assert all(count == 0 for count in _counts().values())

    # 审计表已被清空，留痕必须落在 logs/ 文本里。
    import erp.config as config_module
    trace = config_module.project_path("logs", "purge.log")
    assert trace.exists() and "purge_all_data" in trace.read_text(encoding="utf-8")


def test_settings_page_exposes_purge_card_between_backup_and_legacy_archive():
    init_db()
    html = create_app().test_client().get("/settings/").get_data(as_text=True)

    assert 'id="purgeSection"' in html
    assert "清空所有数据" in html
    assert 'action="/settings/purge-all-data"' in html
    assert 'name="confirm_text"' in html
    assert "输入 清空" in html
    # 卡片位置：备份与恢复之后、旧账留底存档之前。
    assert html.index('id="backupSection"') < html.index('id="purgeSection"') < html.index('id="legacyArchiveSection"')
    # 明确告知保留边界，避免用户以为备份也被清掉。
    assert "backups/" in html and "legacy_archive/" in html


def test_purge_truncates_open_log_handlers_without_nul_holes():
    """logs/ 被清空后，仍打开的日志句柄必须从 0 续写，不能留下 NUL 空洞。

    只删文件不够：`RotatingFileHandler` 持有打开的文件对象，外部截断后它仍从旧偏移写，
    文件头部会变成 NUL 空洞。清空必须走句柄的 `truncate(0)` + `seek(0)`。
    """
    import logging

    import erp.config as config_module

    init_db()
    logs_dir = config_module.project_path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    target = logs_dir / "app.log"
    if target.exists():
        target.unlink()

    handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=1_000_000, backupCount=1, encoding="utf-8"
    )
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        for index in range(30):
            handler.emit(logging.LogRecord(
                "probe", logging.INFO, __file__, 0, "填充日志行 %d %s", (index, "x" * 60), None,
            ))
        handler.flush()
        assert target.stat().st_size > 1000

        purge_all_data()

        for index in range(3):
            handler.emit(logging.LogRecord(
                "probe", logging.INFO, __file__, 0, "清空后的新日志 %d", (index,), None,
            ))
        handler.flush()
        raw = target.read_bytes()
        assert b"\x00" not in raw, "日志被清空后不应出现 NUL 空洞"
        assert "清空后的新日志".encode("utf-8") in raw
        # 清空后文件必须从 0 字节开始就是新内容（没有旧内容残留、没有前导空洞）。
        assert raw.startswith("清空后的新日志".encode("utf-8")), raw[:40]
        assert len(raw) < 500, "清空后不应残留旧日志"
    finally:
        root.removeHandler(handler)
        handler.close()


def test_purge_module_clears_the_documented_tables_only():
    """清空清单必须与需求口径一致，且不含保留对象。"""
    assert "orders" in CLEARED_TABLES and "order_items" in CLEARED_TABLES
    assert "customers" in CLEARED_TABLES and "products" in CLEARED_TABLES
    assert "audit_logs" in CLEARED_TABLES
    # 保留对象不是表，但确认没有被误列进清空清单。
    for kept in ("backups", "legacy_archive", "schema_version"):
        assert kept not in CLEARED_TABLES
