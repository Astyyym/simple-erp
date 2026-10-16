"""界面说明文字开关（设置页「显示」→「界面说明文字」）。

行为契约：
- 默认开启：`<html data-ui-hints="on">`，说明段带 `data-ui-hint`，base.html 里有一条
  `html[data-ui-hints="off"] [data-ui-hint]{display:none!important}` 规则。
- 关掉后：`<html data-ui-hints="off">`，说明段被那句 CSS 隐藏。
- 危险操作警告、状态与数据文字**不带** `data-ui-hint`，无论开关都不能消失。
"""

from __future__ import annotations

from erp import create_app
from erp.config import load_config
from erp.db import init_db

CSS_RULE = 'html[data-ui-hints="off"] [data-ui-hint]{display:none!important}'


def _form(**extra) -> dict:
    data = {"shop_name": "提示开关测试店", "ui_theme": "light", "ui_scale": "100", "ui_font_weight": "standard"}
    data.update(extra)
    return data


def test_hints_are_on_by_default_and_css_rule_is_present():
    init_db()
    client = create_app().test_client()
    html = client.get("/settings/").get_data(as_text=True)
    assert 'data-ui-hints="on"' in html
    assert CSS_RULE in html
    assert "界面说明文字" in html
    assert 'name="ui_show_hints"' in html
    # 设置页各区块的说明段都带标记（改开关前先确认标记还在）。
    assert html.count("data-ui-hint") >= 10


def test_turning_hints_off_and_back_round_trips_through_settings_form():
    init_db()
    client = create_app().test_client()
    assert load_config().get("ui_show_hints", True) is True

    # 复选框未勾选 = 浏览器不提交该字段 → 关闭。
    saved = client.post("/settings/save", data=_form(), follow_redirects=True)
    assert "设置已保存" in saved.get_data(as_text=True)
    assert load_config().get("ui_show_hints") is False
    assert 'data-ui-hints="off"' in client.get("/settings/").get_data(as_text=True)

    back = client.post("/settings/save", data=_form(ui_show_hints="1"), follow_redirects=True)
    assert "设置已保存" in back.get_data(as_text=True)
    assert load_config().get("ui_show_hints") is True
    assert 'data-ui-hints="on"' in client.get("/settings/").get_data(as_text=True)

    # 开关自身状态也要在设置页上如实回显（勾选态）。
    assert 'name="ui_show_hints" value="1" checked' in client.get("/settings/").get_data(as_text=True)


def test_danger_warnings_and_data_text_never_carry_the_hint_marker():
    init_db()
    client = create_app().test_client()
    html = client.get("/settings/").get_data(as_text=True)

    for warning in ("此操作不可撤销", "请先确认上面的备份确实存在"):
        pos = html.find(warning)
        assert pos > 0, warning
        start = html.rfind("<p", 0, pos)
        open_tag = html[start:html.find(">", start) + 1]
        assert "data-ui-hint" not in open_tag, f"危险警告被误标记：{open_tag}"


def test_hint_marker_is_absent_from_data_cells_in_source():
    """导入原因、更新时间戳这类**数据**单元格不得带标记（它们不是说明文字）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "erp" / "templates"
    import_preview = (root / "import_preview.html").read_text(encoding="utf-8")
    assert 'data-ui-hint class="text-muted small"' not in import_preview

    products_edit = (root / "products" / "edit.html").read_text(encoding="utf-8")
    for line in products_edit.splitlines():
        if "row.updated_at" in line:
            assert "data-ui-hint" not in line, line
