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
    # 2026-11-18：查询行改 grid + 写死行号（flex 版在 1280/1360 下会把客户标签与输入挤成两行，
    # 并把「高级筛选」甩到第三行）。见 base.html 的 ① 查询三要素 注释。
    assert ".filter-tier-query{display:grid;grid-template-columns:auto minmax(0,1fr) auto" in html
    assert ".filter-field-composite{grid-column:1;grid-row:1" in html


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


def test_filter_query_row_has_explicit_grid_tiers_that_cannot_be_squeezed():
    """查询行必须「显式分档」，不能靠 flex 自动收缩决定谁被挤走。

    2026-11-18 用户第三次反馈「不同窗口宽度下单据管理页的筛选区域还是没有做好适配」。
    真实浏览器实测（1440/1280/1180/1024/960 逐像素量，见 base.html 的 ① 查询三要素注释）：
      · 1280 下「客户名称」被压到 241px，标签与输入框断成两行（字段高 68 而非 40）；
      · 「高级筛选」因 margin-left:auto 争不到空间，被甩到第三行独自右对齐；
      · 起止日期随容器拉伸（1440 每格 143px，1180 反而 224px —— 窄窗更宽）。
    修法是把查询行改成 grid + 写死 grid-row/column，并且日期格设上限。
    本测试锁住机制本身，防止退回「靠 flex-basis 碰运气」。
    """
    init_db()
    html = create_app().test_client().get("/orders/").get_data(as_text=True)

    # ① grid 三列 + 三个子项各自钉死行号：位置不再受 DOM 顺序或剩余宽度影响。
    assert ".filter-tier-query{display:grid;grid-template-columns:auto minmax(0,1fr) auto" in html
    assert ".filter-field-composite{grid-column:1;grid-row:1" in html
    assert ".filter-field-plain{grid-column:2;grid-row:1" in html
    assert ".filter-tier-query>.filter-advanced-toggle{grid-column:3;grid-row:1" in html

    # ② 分档写全：中档让时间块独占首行（它内容宽 596px，与按钮并排会被压到 487px
    #    而把日期折回第二行），第二行客户撑满 + 按钮贴右；≤720 再落成单列。
    #
    #    阈值 1413 是算出来的，不是拍的：三列并排需 596（时间）+20+260（客户下限）+20+94（按钮）
    #    = 990 内容宽，而内容宽 = 视口 − 424（1440→1016、1414→990 两点实测拟合），故 990+424=1414。
    #    取 1413 作断点：1413 时内容宽 989，客户只剩 259px，低于 260 下限会横向溢出（实测），
    #    所以 1413 必须归中档。用户 2026-11-18 定「1024 是最窄窗口」，落在这档内。
    assert "@media(max-width:1413px){" in html
    assert ".filter-field-composite{grid-column:1/-1;grid-row:1}" in html
    assert ".filter-field-plain{grid-column:1;grid-row:2}" in html
    assert ".filter-tier-query>.filter-advanced-toggle{grid-column:2;grid-row:2" in html
    assert "@media(max-width:720px){" in html
    assert ".filter-tier-query>.filter-advanced-toggle{grid-column:1;grid-row:3" in html
    # 反例：1399 这个旧断点必须消失 —— 它按「时间块 + 按钮同行」的下限去卡，
    # 却让 1100 那一档（桌面壳最小窗口）白多出一行按钮，卡片从 203px 涨到 257px。
    assert "@media(max-width:1399px){" not in html
    assert "@media(max-width:797px){" not in html

    # ③ 日期格有上限，不再随容器拉成任意宽度；也不许退回撑满（1fr）。
    assert ".filter-field-date .date-range{display:grid;grid-template-columns:repeat(2,minmax(0,143px))" in html
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" not in html.split(".filter-field-date .date-range{")[1].split("}")[0]
    # 日期区不再 flex-grow（原来随容器拉伸正是「窄窗反而更宽」的原因）。
    assert ".filter-field-composite .filter-inline>.dates{margin:0;flex:0 1 auto;min-width:0}" in html

    # ④ 客户字段在网格里带 260px 下限（标签 52 + 输入 180 + 间距），
    #    低于这个宽度就只剩「标签一行、输入一行」，所以要由布局兜住而不是事后补救。
    assert ".filter-field-plain{grid-column:2;grid-row:1;min-width:260px}" in html
