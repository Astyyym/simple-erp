"""Batch A 备份与恢复回归（2026-10-08 旧数据迁移承接计划）。

全部在 pytest 隔离数据根内运行，绝不触碰正式 data/erp.db。
"""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from erp import config as config_module
from erp.config import data_root, load_config, save_config
from erp.db import CURRENT_SCHEMA_VERSION, get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp.utils.backup import (
    RestoreError,
    backup_dir,
    copy_backup_to,
    create_backup,
    inspect_backup,
    list_backups,
    prune_old_backups,
    read_backup_schema_version,
    restore_backup,
)


def _customer_names() -> list[str]:
    with get_db() as conn:
        return [row["name"] for row in conn.execute("SELECT name FROM customers ORDER BY name")]


def _make_order(name: str, order_no: str) -> int:
    customer_id = create_customer(name)
    create_order_from_typed_rows(
        customer_id,
        order_no,
        [{"product_name": "灭火器", "unit": "个", "unit_price_yuan": "100", "quantity": "1"}],
        status="saved",
    )
    return customer_id


def test_backup_zip_covers_data_folders_and_skips_transient_dirs():
    init_db()
    root = data_root()
    # 有内容的数据目录：商品图片 + 旧账存档。
    (root / "product_images").mkdir(parents=True, exist_ok=True)
    (root / "product_images" / "p1.png").write_bytes(b"png-bytes")
    (root / "legacy_archive").mkdir(parents=True, exist_ok=True)
    (root / "legacy_archive" / "old_debts.xlsx").write_bytes(b"legacy-bytes")
    # 不该进备份的目录：日志、临时 PDF、imports。
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "app.log").write_text("noise", encoding="utf-8")
    (root / "temp_pdf").mkdir(parents=True, exist_ok=True)
    (root / "temp_pdf" / "preview.pdf").write_bytes(b"%PDF")
    (root / "imports").mkdir(parents=True, exist_ok=True)
    (root / "imports" / "upload.xlsx").write_bytes(b"x")

    backup_path = create_backup("pytest")
    assert backup_path.exists()
    with zipfile.ZipFile(backup_path) as zf:
        names = set(zf.namelist())

    assert {"erp.db", "config.json", "backup_info.txt", "integrity_check.txt"}.issubset(names)
    assert "product_images/p1.png" in names
    assert "legacy_archive/old_debts.xlsx" in names
    assert not any(name.startswith("logs/") for name in names)
    assert not any(name.startswith("temp_pdf/") for name in names)
    assert not any(name.startswith("imports/") for name in names)


def test_backup_info_is_multiline_not_literal_backslash_n():
    init_db()
    backup_path = create_backup("pytest")
    with zipfile.ZipFile(backup_path) as zf:
        text = zf.read("backup_info.txt").decode("utf-8")
    assert "\\n" not in text
    lines = [line for line in text.splitlines() if line.strip()]
    assert any(line.startswith("reason=") for line in lines)
    assert any(line.startswith("created_at=") for line in lines)
    assert any(line.startswith("integrity=") for line in lines)


def test_backup_work_dir_is_cleaned_up_after_zip():
    init_db()
    backup_path = create_backup("pytest")
    leftover = backup_path.with_suffix("")  # backup_<ts>_pytest
    assert not leftover.exists()


def test_restore_round_trip_brings_data_back_to_backup_point():
    init_db()
    _make_order("迁移客户甲", "MD202610090001")
    backup_path = create_backup("pytest")

    # 备份之后新增的数据，恢复后应当消失。
    _make_order("备份后客户乙", "MD202610090002")
    assert "备份后客户乙" in _customer_names()

    result = restore_backup(backup_path)
    assert result["integrity"] == "ok"
    names = _customer_names()
    assert "迁移客户甲" in names
    assert "备份后客户乙" not in names
    # 恢复前自动备份存在。
    assert Path(result["pre_restore_backup"]).exists()
    assert "pre_restore" in Path(result["pre_restore_backup"]).name


def test_restore_leaves_config_untouched_unless_requested():
    init_db()
    save_config({"shop_name": "备份时的店名"})
    backup_path = create_backup("pytest")

    save_config({"shop_name": "恢复前的店名"})

    # 默认：只恢复数据库与数据文件，设置保持现状。
    result = restore_backup(backup_path)
    assert result["config_restored"] is False
    assert load_config()["shop_name"] == "恢复前的店名"

    # 显式勾选：设置一并倒回备份时的值。
    result2 = restore_backup(backup_path, include_config=True)
    assert result2["config_restored"] is True
    assert load_config()["shop_name"] == "备份时的店名"


def test_restore_replaces_data_folders_from_backup():
    init_db()
    root = data_root()
    (root / "legacy_archive").mkdir(parents=True, exist_ok=True)
    (root / "legacy_archive" / "old_debts.xlsx").write_bytes(b"legacy-bytes")
    backup_path = create_backup("pytest")

    # 备份后旧账存档被改动，恢复后应回到备份内容。
    (root / "legacy_archive" / "old_debts.xlsx").write_bytes(b"changed")
    (root / "legacy_archive" / "extra.txt").write_text("extra", encoding="utf-8")

    result = restore_backup(backup_path)
    assert "legacy_archive" in result["restored_dirs"]
    assert (root / "legacy_archive" / "old_debts.xlsx").read_bytes() == b"legacy-bytes"
    assert not (root / "legacy_archive" / "extra.txt").exists()


def test_restore_rejects_missing_and_corrupt_backups():
    init_db()
    with pytest.raises(RestoreError):
        restore_backup(data_root() / "backups" / "does_not_exist.zip")

    backup_dir_path = data_root() / "backups"
    backup_dir_path.mkdir(parents=True, exist_ok=True)
    bogus = backup_dir_path / "backup_20260101_000000_manual.zip"
    bogus.write_bytes(b"not a zip")
    with pytest.raises(RestoreError):
        restore_backup(bogus)


def test_restore_rejects_zip_without_database():
    init_db()
    backup_dir_path = data_root() / "backups"
    backup_dir_path.mkdir(parents=True, exist_ok=True)
    bad = backup_dir_path / "backup_20260101_000000_manual.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("config.json", "{}")
    with pytest.raises(RestoreError, match="erp.db"):
        inspect_backup(bad)


def test_restore_rejects_zip_slip_paths():
    init_db()
    backup_dir_path = data_root() / "backups"
    backup_dir_path.mkdir(parents=True, exist_ok=True)
    bad = backup_dir_path / "backup_20260101_000000_manual.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escape.txt", "x")
        zf.writestr("erp.db", "x")
    with pytest.raises(RestoreError):
        inspect_backup(bad)


def test_list_backups_newest_first_and_parses_metadata():
    init_db()
    create_backup("manual")
    create_backup("pytest")
    entries = list_backups()
    assert len(entries) >= 2
    assert entries == sorted(entries, key=lambda item: item["created_at"], reverse=True)
    newest = entries[0]
    assert newest["name"].startswith("backup_")
    assert newest["size_bytes"] > 0
    assert newest["created_at_display"]


def test_prune_removes_backups_older_than_retention_window():
    init_db()
    backup_dir_path = data_root() / "backups"
    backup_dir_path.mkdir(parents=True, exist_ok=True)
    old_name = "backup_20200101_000000_manual.zip"
    old_path = backup_dir_path / old_name
    with zipfile.ZipFile(old_path, "w") as zf:
        zf.writestr("erp.db", "x")
    fresh = create_backup("pytest")
    # create_backup 自身就会清理过期备份，所以 2020 的那份应当已经消失。
    assert not old_path.exists()
    assert fresh.exists()

    # 再放一份过期备份，直接验证 prune 的返回值与保留窗口。
    stale = backup_dir_path / "backup_20200102_000000_manual.zip"
    with zipfile.ZipFile(stale, "w") as zf:
        zf.writestr("erp.db", "x")
    removed = prune_old_backups(reference=datetime(2026, 10, 9, 12, 0, 0))
    assert "backup_20200102_000000_manual.zip" in removed
    assert not stale.exists()
    assert fresh.exists()


def test_copy_backup_to_second_location(tmp_path):
    init_db()
    backup_path = create_backup("pytest")
    destination_dir = tmp_path / "offsite"
    copied = copy_backup_to(backup_path, destination_dir)
    assert copied.exists()
    assert copied.read_bytes() == backup_path.read_bytes()

    # 另存到原目录应被拒绝，避免“备份还是同一块盘”。
    with pytest.raises(ValueError):
        copy_backup_to(backup_path, backup_path.parent)


def test_settings_page_exposes_backup_controls():
    init_db()
    from erp import create_app

    client = create_app().test_client()
    html = client.get("/settings/").get_data(as_text=True)
    assert "备份与恢复" in html
    assert "/settings/backup/create" in html
    assert "立即备份" in html
    assert "另存到" in html


def test_settings_backup_create_and_restore_flow():
    init_db()
    from erp import create_app

    client = create_app().test_client()
    _make_order("界面客户甲", "MD202610090010")

    created = client.post("/settings/backup/create", follow_redirects=True)
    assert created.status_code == 200
    assert "已创建备份" in created.get_data(as_text=True)
    backups = list_backups()
    assert backups
    name = backups[0]["name"]

    # 备份后新增数据。
    _make_order("界面客户乙", "MD202610090011")
    assert "界面客户乙" in _customer_names()

    # 确认词不对 → 拒绝恢复。
    rejected = client.post(
        "/settings/backup/restore",
        data={"backup_name": name, "confirm_text": "no"},
        follow_redirects=True,
    )
    assert "恢复已取消" in rejected.get_data(as_text=True)
    assert "界面客户乙" in _customer_names()

    # 正确确认词 → 恢复。
    ok = client.post(
        "/settings/backup/restore",
        data={"backup_name": name, "confirm_text": "恢复"},
        follow_redirects=True,
    )
    assert "已从备份恢复" in ok.get_data(as_text=True)
    assert "界面客户乙" not in _customer_names()
    assert "界面客户甲" in _customer_names()


def test_master_data_lists_warn_when_no_backup_exists():
    init_db()
    from erp import create_app

    client = create_app().test_client()

    for path, page_name in (("/customers/", "客户管理"), ("/products/", "商品管理")):
        html = client.get(path).get_data(as_text=True)
        assert page_name in html
        # A-7：没有任何备份时必须显著警示（含迁移前提醒，两句话合并在同一条里）。
        assert "尚未创建任何备份" in html
        assert "/settings/#backupSection" in html
        assert "迁移旧数据" in html

    # 建一份备份后，整条告警消失：备份已存在时不再常驻占用版面。
    create_backup("pytest")
    html_after = client.get("/customers/").get_data(as_text=True)
    assert "尚未创建任何备份" not in html_after
    assert "迁移旧数据" not in html_after


def _make_legacy_backup_zip(version: int = 14) -> str:
    """造一份「旧版结构」备份包（真实 v14 结构，只含 erp.db），返回备份文件名。"""
    sql = (Path(__file__).parent / "fixtures" / "schema_v14_full.sql").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="legacy_backup_") as tmp:
        db_file = Path(tmp) / "erp.db"
        conn = sqlite3.connect(db_file)
        conn.executescript(sql)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version(version, notes) VALUES (?, 'synthetic legacy')", (version,))
        conn.commit()
        conn.close()
        name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_legacy_v{version}.zip"
        target = backup_dir() / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_file, arcname="erp.db")
    return name


def test_list_backups_reports_schema_version_and_flags_legacy():
    """恢复界面必须能分辨备份的库结构：当前版本不提示，旧版结构打标记。

    背景（schema v15）：恢复旧版备份后，程序下次启动会执行**不可逆**升级
    （物理删掉库存表与成本列），所以在恢复前就得让用户看见。
    """
    init_db()
    create_backup("current")
    current = next(e for e in list_backups() if e["reason"] == "current")
    assert current["schema_version"] == CURRENT_SCHEMA_VERSION
    assert current["is_legacy"] is False
    assert current["schema_version_display"] == f"v{CURRENT_SCHEMA_VERSION}"

    name = _make_legacy_backup_zip(14)
    legacy = next(e for e in list_backups() if e["name"] == name)
    assert read_backup_schema_version(legacy["path"]) == 14
    assert legacy["schema_version"] == 14
    assert legacy["schema_version_display"] == "v14"
    assert legacy["is_legacy"] is True


def test_settings_page_warns_before_restoring_a_legacy_backup():
    init_db()
    _make_legacy_backup_zip(14)
    from erp import create_app

    client = create_app().test_client()
    html = client.get("/settings/").get_data(as_text=True)
    assert "数据库版本" in html
    assert "v14" in html
    assert "旧版结构" in html
    assert "库存与成本数据将被永久删除" in html


def test_restoring_a_legacy_backup_warns_about_irreversible_upgrade():
    init_db()
    name = _make_legacy_backup_zip(14)
    from erp import create_app

    client = create_app().test_client()
    restored = client.post(
        "/settings/backup/restore",
        data={"backup_name": name, "confirm_text": "恢复"},
        follow_redirects=True,
    )
    text = restored.get_data(as_text=True)
    assert "已从备份恢复" in text
    assert "会自动升级到 v15" in text
    assert "无法回退" in text


def test_restoring_a_current_backup_has_no_upgrade_warning():
    """当前版本的备份不该被误报成旧版结构。"""
    init_db()
    create_backup("current")
    name = next(e for e in list_backups() if e["reason"] == "current")["name"]
    from erp import create_app

    client = create_app().test_client()
    restored = client.post(
        "/settings/backup/restore",
        data={"backup_name": name, "confirm_text": "恢复"},
        follow_redirects=True,
    )
    assert "已从备份恢复" in restored.get_data(as_text=True)
    assert "会自动升级到" not in restored.get_data(as_text=True)
