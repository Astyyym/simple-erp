"""1-B 自动备份：启动/退出触发点 + 防抖 + 失败不抛。

在 pytest 隔离数据根内运行。
"""
from __future__ import annotations

from pathlib import Path

from erp.db import init_db
from erp.services.accounting import create_customer
from erp.utils import backup as backup_module
from erp.utils.backup import auto_backup_if_due, backup_dir, list_backups


def _auto_backups():
    return [entry for entry in list_backups() if entry["reason"].startswith("auto")]


def test_auto_backup_creates_when_none(monkeypatch):
    init_db()
    monkeypatch.setattr(backup_module, "AUTO_BACKUP_INTERVAL_MINUTES", 10)
    created = auto_backup_if_due("auto_start")
    assert created is not None and created.is_file()
    entries = _auto_backups()
    assert len(entries) == 1
    assert entries[0]["reason"] == "auto_start"


def test_auto_backup_debounced_within_interval(monkeypatch):
    init_db()
    monkeypatch.setattr(backup_module, "AUTO_BACKUP_INTERVAL_MINUTES", 10)
    first = auto_backup_if_due("auto_start")
    assert first is not None
    # 紧接着再触发：未越过 10 分钟防抖窗口 → 跳过，不新增文件。
    second = auto_backup_if_due("auto_exit")
    assert second is None
    assert len(_auto_backups()) == 1


def test_auto_backup_runs_again_when_interval_zero(monkeypatch):
    init_db()
    monkeypatch.setattr(backup_module, "AUTO_BACKUP_INTERVAL_MINUTES", 0)
    first = auto_backup_if_due("auto_start")
    second = auto_backup_if_due("auto_exit")
    assert first is not None and second is not None
    assert len(_auto_backups()) == 2


def test_auto_backup_swallows_failure(monkeypatch):
    init_db()

    def boom(reason="manual"):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup_module, "create_backup", boom)
    # 不应抛异常，返回 None。
    assert auto_backup_if_due("auto_start") is None


def test_auto_backup_hooks_present_in_both_entrypoints():
    """触发点必须覆盖源码模式与桌面壳（不能只挂 app.py）。"""
    root = Path(__file__).resolve().parents[1]
    app_src = (root / "app" / "app.py").read_text(encoding="utf-8")
    desktop_src = (root / "desktop_app.py").read_text(encoding="utf-8")
    assert "auto_backup_if_due" in app_src
    assert "auto_backup_if_due" in desktop_src
    # 启动 + 退出各一次
    assert app_src.count("auto_backup_if_due") >= 2
    assert desktop_src.count("auto_backup_if_due") >= 2
    # 绝不能放进 create_app 工厂（会污染测试、乱造备份）
    erp_init = (root / "app" / "erp" / "__init__.py").read_text(encoding="utf-8")
    assert "auto_backup_if_due" not in erp_init
