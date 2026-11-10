import pytest

from erp import create_app
from erp.db import init_db


@pytest.mark.parametrize("mode", ["year", "month", "range"])
def test_order_filter_uses_one_stable_action_area_in_every_date_mode(mode):
    init_db()
    response = create_app().test_client().get("/orders/", query_string={"date_mode": mode})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    form = html.split('id="ordersFilterForm"', 1)[1].split("</form>", 1)[0]
    assert 'class="filter-card"' in form
    assert 'class="filter-actions"' in form
    fields, actions = form.split('class="filter-actions"', 1)
    assert 'name="date_mode"' in fields
    assert 'data-mode="year"' in fields and 'data-mode="month"' in fields and 'data-mode="range"' in fields
    # 2026-11-09 用户要求：动作条与数据分析页对齐（查询 / 重置），并撤掉「新建订单」与
    # 四个「导出 *Excel」按钮，动作条只剩「查询 · 重置 · 删除选中 · 导出汇总表」。
    expected = ["查询", "重置", "导出汇总表"]
    assert all(label in actions for label in expected)
    assert "清除筛选条件" not in actions
    assert "新建订单" not in actions
    assert "/orders/export/sales.xlsx" not in actions
    assert 'id="exportSummaryBtn"' in actions
    assert 'id="exportSummaryBtn"' not in fields
    assert 'filter-actions-extra' in actions
    assert 'gap:' in html


def test_filter_date_mode_switch_only_changes_fields_and_keeps_export_contract():
    init_db()
    html = create_app().test_client().get("/orders/", query_string={"date_mode": "range"}).get_data(as_text=True)
    assert "panel.hidden = !active" in html
    assert "el.disabled = !active" in html
    assert "summaryParams.delete('order_type')" in html
    # 四个 *Excel 按钮 2026-11-09 撤掉后，页面 JS 不再拼这些 href（后端接口本身保留，
    # 由 tests/test_excel_export.py 直接打端点验证）。
    assert "/orders/export/sales.xlsx" not in html


def test_date_mode_choices_are_separate_buttons_with_spacing():
    init_db()
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    mode_controls = html.split('aria-label="时间模式"', 1)[0].rsplit("<div", 1)[-1]
    assert 'class="filter-mode-options"' in mode_controls
    assert '.filter-mode-options{' in html
    assert 'gap:8px' in html
    assert 'class="btn-group" role="group" aria-label="时间模式"' not in html
    for mode in ("Year", "Month", "Range"):
        assert f'id="dateMode{mode}"' in html


def test_order_filter_type_block_keeps_bulk_buttons_on_the_type_row():
    """订单类型块：标签 + 四类复选框 + 清空同行左对齐；不勾选=全部，故不再有「全选」。"""
    init_db()
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    form = html.split('id="ordersFilterForm"', 1)[1].split("</form>", 1)[0]

    assert 'class="filter-field-type"' in form
    # 从 filter-type-row 到提示文案之间 = 类型行本体（内含四类与批量按钮）。
    type_row = form.split('class="filter-type-row"', 1)[1].split('filter-type-hint', 1)[0]
    assert 'class="filter-type-options"' in type_row, "四类复选框与标签同行"
    assert 'class="filter-type-bulk"' in type_row, "清空与四类同行"
    assert 'data-type-all' not in form, "不勾选=全部，全选按钮冗余，应已移除"
    assert 'data-status-all' not in form
    assert ".filter-type-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px}" in html
    # 批量按钮不再被推到行尾
    assert ".filter-type-bulk{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end}" not in html
    # 三段式布局：查询三要素 + 条件多选 + 动作条；时间模式与日期输入同属组合控件
    assert 'class="filter-tier-query"' in form
    assert 'class="filter-tier-conditions"' in form
    assert 'class="filter-field-composite"' in form
    assert ".filter-tier-query{display:flex;flex-wrap:wrap" in html
    assert ".filter-field-composite{flex:1.2 1 320px" in html


def test_order_filter_actions_are_one_left_aligned_row_without_dividers():
    """操作条（2026-11-10 用户要求）：一条左对齐的行，组间不要竖线，导出不要贴右。

    顺序固定为 查询 · 重置 · 删除选中 · 导出汇总表。
    """
    init_db()
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    form = html.split('id="ordersFilterForm"', 1)[1].split("</form>", 1)[0]
    actions = form.split('class="filter-actions"', 1)[1]

    order = ["查询", "重置", "删除选中", "导出汇总表"]
    positions = [actions.index(label) for label in order]
    assert positions == sorted(positions), "动作顺序必须固定为 查询 · 重置 · 删除选中 · 导出汇总表"

    # 竖线分隔与右推都已取消（两条规则都不许再带 border-left / margin-left:auto）。
    assert ".filter-actions-records{border-left" not in html
    assert ".filter-actions-extra{margin-left:auto" not in html
    assert ".filter-actions-extra{border-left" not in html
    assert ".filter-actions{" in html
    assert "border-top:1px solid var(--erp-border)" in html
