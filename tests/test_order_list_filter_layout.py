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
    expected = ["筛选", "清除筛选条件", "导出汇总表", "导出销售单Excel", "导出退货单Excel"]
    assert all(label in actions for label in expected)
    assert actions.index('id="exportSummaryBtn"') < actions.index('id="exportSalesExcelBtn"') < actions.index('id="exportReturnsExcelBtn"')
    assert 'id="exportSummaryBtn"' not in fields
    assert 'id="exportSalesExcelBtn"' not in fields
    assert 'id="exportReturnsExcelBtn"' not in fields
    assert 'filter-actions-extra' in actions
    assert 'gap:' in html


def test_filter_date_mode_switch_only_changes_fields_and_keeps_export_contract():
    init_db()
    html = create_app().test_client().get("/orders/", query_string={"date_mode": "range"}).get_data(as_text=True)
    assert "panel.hidden = !active" in html
    assert "el.disabled = !active" in html
    assert "summaryParams.delete('order_type')" in html
    assert "'/orders/export/sales.xlsx'" in html
    assert "'/orders/export/returns.xlsx'" in html


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


def test_order_filter_actions_split_primary_and_export_groups():
    """操作条：主操作靠左、导出组贴右，按钮同高。"""
    init_db()
    html = create_app().test_client().get("/orders/").get_data(as_text=True)

    assert ".filter-actions-extra{margin-left:auto}" in html
    assert ".filter-actions{" in html
    assert "border-top:1px solid var(--erp-border)" in html
