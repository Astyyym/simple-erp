"""1-E 系统健康卡：设置页展示数据库完整性 / 最近备份 / 数据根 / 版本。"""
from __future__ import annotations

from erp import create_app
from erp.db import init_db
from erp.utils.backup import create_backup, list_backups


def test_health_card_renders_and_values_match():
    init_db()
    client = create_app().test_client()
    page = client.get("/settings/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "系统健康" in html
    assert "数据库完整性" in html
    assert "正常" in html
    assert "程序版本" in html


def test_health_snapshot_counts_backups():
    init_db()
    app = create_app()
    with app.test_request_context("/settings/"):
        from erp.routes.settings import system_health_snapshot

        before = system_health_snapshot()
        assert before["db_ok"] is True
        assert before["backup_count"] == len(list_backups())
        assert before["version"].startswith("v") or before["version"]

    create_backup("manual")
    with app.test_request_context("/settings/"):
        from erp.routes.settings import system_health_snapshot

        after = system_health_snapshot()
        assert after["backup_count"] == len(list_backups())
        assert after["last_backup_display"]
