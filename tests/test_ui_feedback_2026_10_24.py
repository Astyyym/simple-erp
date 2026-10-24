"""2026-10-24 用户七条界面反馈的回归契约。

本文件锁住「这一轮为什么这样改」，每条用例的 docstring 都写明用户原话与判据，
避免日后被当成「多余样式」清理掉。逐条对应：

1. 工作台指标卡补齐「拿货单 / 退拿货单」（与手机端今日概览四类同口径）。
2. 单据列表的类型标签改成手机端形态（淡底 + 类型色文字），金额与标签同色，
   列表不再深浅交替。
3. 数据分析页的日期输入放在「按日到日」右侧同一行（CSS 契约，见 test_ui_layout_reflow）。
4. 商品管理「按拼音排序」右侧加「价格」+ 正倒序箭头。
5. 客户管理同样加「欠款」+ 箭头；「期初」列改为「欠款」，正数绿 / 负数红。
6. 设置页备份行改成「另存 / 恢复 / 删除」三个独立按钮；「打印信息提示」归到「打印」。
7. App 商品详情页吸顶 + 搜索/排序（见 test_mobile_product_detail_sort.py）。
"""
from __future__ import annotations

import re

from erp import create_app
from erp.db import get_db, init_db
from erp.services import accounting
from erp.services.purchases import create_purchase_order
from erp.utils.backup import create_backup, list_backups


# ---------------------------------------------------------------- 1. 工作台四类

def test_workbench_metric_cards_cover_all_four_document_types():
    """用户：「第一张截图，今日净业务额 销售单 退货单 拿货单 退拿货单」。

    四类**不相加**（这是长期口径，见需求文档）：净业务额仍是「销售 − 退货」，
    拿货/退拿货只报笔数。所以这里断言的是「四张笔数卡都在」，不是「多了一个合计」。
    """
    init_db()
    today = accounting.date.today().isoformat()
    customer_id = accounting.create_customer("四类指标客户")
    product_id = accounting.create_product("四类指标商品", "个")
    accounting.create_order_from_typed_rows(
        customer_id, "MD202610240011",
        [{"product_id": product_id, "product_name": "四类指标商品", "unit": "个",
          "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date=today,
    )
    create_purchase_order(
        "NH202610240011", today,
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "5"}],
        customer_id=customer_id, request_key="metric-cards-purchase",
    )

    html = create_app().test_client().get("/").get_data(as_text=True)
    for hook in ("今日净业务额", "销售单", "退货单", "拿货单", "退拿货单"):
        assert hook in html, hook
    # 笔数钩子：拿货 1 笔、退拿货 0 笔（口径 = business_date + status='saved'）。
    assert 'id="purchasesCount">1 笔' in html
    assert 'id="purchaseReturnsCount">0 笔' in html


def test_workbench_purchase_counts_follow_the_same_status_rule_as_mobile():
    """拿货/退拿货没有「已打印」态：草稿与已作废都不计入（与手机端同一套过滤）。"""
    init_db()
    today = accounting.date.today().isoformat()
    customer_id = accounting.create_customer("拿货口径客户")
    product_id = accounting.create_product("拿货口径商品", "个")
    create_purchase_order(
        "NH202610240021", today,
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "5"}],
        customer_id=customer_id, request_key="metric-rule-draft", status="draft",
    )
    html = create_app().test_client().get("/").get_data(as_text=True)
    assert 'id="purchasesCount">0 笔' in html


# ---------------------------------------------------------------- 2. 单据列表配色

def test_order_list_type_tag_and_amount_share_the_mobile_color_scheme():
    """用户：「单据列表的『类型』标签，以手机端为准；『金额』的字体颜色也是如此。」

    手机端是**淡底 + 类型色文字**的胶囊（mobile.css `.tag.t-*` / 原生 TypeBadge），
    金额与标签同色。判据：类型单元格带 `order-type-tag--<type>`，
    金额单元格带 `order-amount--<type>`，且两者用的是同一套 CSS 变量。
    """
    init_db()
    customer_id = accounting.create_customer("配色客户")
    product_id = accounting.create_product("配色商品", "个")
    rows = [{"product_id": product_id, "product_name": "配色商品", "unit": "个",
             "unit_price_yuan": "100", "quantity": "1"}]
    accounting.create_order_from_typed_rows(
        customer_id, "MD202610240031", rows, status="saved", order_date="2026-10-24",
    )
    accounting.create_order_from_typed_rows(
        customer_id, "MD202610240032", rows, status="saved", order_date="2026-10-24",
        order_type="return",
    )

    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    assert 'class="order-type-tag order-type-tag--sale"' in html
    assert 'class="order-type-tag order-type-tag--return"' in html
    assert 'class="order-amount order-amount--sale"' in html
    assert 'class="order-amount order-amount--return"' in html
    # 旧实现（Bootstrap 实心色块）必须彻底消失：它的观感与手机端不同源。
    assert 'class="badge bg-primary"' not in html
    assert "badge bg-danger\">退货单" not in html


def test_order_list_has_no_zebra_striping():
    """用户：「然后列表不要用深浅颜色交替显示」。

    判据分两层：模板不再用 `table-striped`；全局样式把该类的条纹变量置空
    （回收站等页仍在用这个类，只删模板不够）。
    """
    init_db()
    customer_id = accounting.create_customer("斑马纹客户")
    product_id = accounting.create_product("斑马纹商品", "个")
    rows = [{"product_id": product_id, "product_name": "斑马纹商品", "unit": "个",
             "unit_price_yuan": "100", "quantity": "1"}]
    accounting.create_order_from_typed_rows(
        customer_id, "MD202610240033", rows, status="saved", order_date="2026-10-24",
    )
    accounting.create_order_from_typed_rows(
        customer_id, "MD202610240034", rows, status="saved", order_date="2026-10-24",
        order_type="return",
    )
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    table = html.split('id="ordersResults"', 1)[1].split("</table>", 1)[0]
    assert "MD202610240033" in table and "MD202610240034" in table, "夹具单据要真的在表里"
    assert "table-striped" not in table
    # 退货单不再整行铺淡红（那是第三种底色，同样属于「深浅交替」）。
    assert "table-danger" not in table
    assert "return-order-row" in table, "语义钩子保留（排查/脚本按它取行）"


# ---------------------------------------------------------------- 4/5. 排序控件

def test_product_sort_offers_price_and_a_direction_arrow():
    """用户：「『按拼音排序』右侧加『价格』『正序/倒序（的上下箭头）』同手机端」。"""
    init_db()
    html = create_app().test_client().get("/products/?sort=price&order=desc").get_data(as_text=True)
    bar = html.split('class="list-view-options"', 1)[1].split("</div>", 1)[0]
    assert bar.index("按拼音排序") < bar.index("价格"), "「价格」必须在「按拼音排序」右侧"
    assert 'aria-label="当前倒序，点击改成正序"' in bar
    assert "order=asc" in bar, "箭头点一下要换成反方向"


def test_customer_sort_offers_debt_and_a_direction_arrow():
    """用户：「客户管理页面也是如此，『按拼音排序』右侧加『欠款』『正序/倒序』」。"""
    init_db()
    html = create_app().test_client().get("/customers/?sort=debt").get_data(as_text=True)
    bar = html.split('class="list-view-options"', 1)[1].split("</div>", 1)[0]
    assert bar.index("按拼音排序") < bar.index("欠款")
    assert "list-sort-dir" in bar


def test_customer_debt_sort_really_orders_by_balance():
    """排序必须真的改顺序（不能只改按钮样式）：多 → 少为欠款正序。"""
    init_db()
    rich = accounting.create_customer("欠得多客户")
    poor = accounting.create_customer("欠得少客户")
    product_id = accounting.create_product("欠款排序商品", "个")
    accounting.create_order_from_typed_rows(
        rich, "MD202610240041",
        [{"product_id": product_id, "product_name": "欠款排序商品", "unit": "个",
          "unit_price_yuan": "900", "quantity": "1"}],
        status="saved", order_date="2026-10-24",
    )
    accounting.create_order_from_typed_rows(
        poor, "MD202610240042",
        [{"product_id": product_id, "product_name": "欠款排序商品", "unit": "个",
          "unit_price_yuan": "100", "quantity": "1"}],
        status="saved", order_date="2026-10-24",
    )
    client = create_app().test_client()
    body = client.get("/customers/?sort=debt").get_data(as_text=True)
    body = body.split('id="customersResults"', 1)[1]
    assert body.index("欠得多客户") < body.index("欠得少客户")
    reversed_body = client.get("/customers/?sort=debt&order=desc").get_data(as_text=True)
    reversed_body = reversed_body.split('id="customersResults"', 1)[1]
    assert reversed_body.index("欠得少客户") < reversed_body.index("欠得多客户")


def test_customer_list_shows_debt_column_instead_of_opening_balance():
    """用户：「删除『期初』的字段，改为『欠款』……我欠别人的，用红色，别人欠我的，用绿色。」

    口径 = **当前欠款**（与账款管理页同一套加减法），不是期初。
    正数（客户欠我）= `.is-owed` → success；负数（我欠客户）= `.is-overpaid` → danger。
    """
    init_db()
    owed = accounting.create_customer("欠我钱的客户")
    overpaid = accounting.create_customer("我欠他钱的客户")
    product_id = accounting.create_product("欠款列商品", "个")
    accounting.create_order_from_typed_rows(
        owed, "MD202610240051",
        [{"product_id": product_id, "product_name": "欠款列商品", "unit": "个",
          "unit_price_yuan": "500", "quantity": "1"}],
        status="saved", order_date="2026-10-24",
    )
    # 期初为负 = 我欠客户（多付/预付）：列表必须显示成负数并标红。
    with get_db() as conn:
        conn.execute("UPDATE customers SET opening_balance_cents=-30000 WHERE id=?", (overpaid,))

    html = create_app().test_client().get("/customers/").get_data(as_text=True)
    assert "<th>欠款</th>" in html
    assert "<th>期初</th>" not in html
    body = html.split('id="customersResults"', 1)[1]

    owed_row = next(line for line in body.split("<tr>") if "欠我钱的客户" in line)
    assert 'class="customer-balance is-owed"' in owed_row
    assert "¥500.00" in owed_row

    overpaid_row = next(line for line in body.split("<tr>") if "我欠他钱的客户" in line)
    assert 'class="customer-balance is-overpaid"' in overpaid_row
    assert "¥-300.00" in overpaid_row or "-¥300.00" in overpaid_row


def test_customer_debt_matches_the_accounts_page_total():
    """客户列表的欠款必须与账款管理页同源（期初 + 单据 + 调整 − 收款 − 拿货 + 退拿货）。

    两处不同源的话，同一个客户在两张页面上是两个数 —— 那比不显示更糟。
    """
    init_db()
    customer_id = accounting.create_customer("跨页对账客户", opening_balance_cents=10000)
    product_id = accounting.create_product("跨页对账商品", "个")
    accounting.create_order_from_typed_rows(
        customer_id, "MD202610240061",
        [{"product_id": product_id, "product_name": "跨页对账商品", "unit": "个",
          "unit_price_yuan": "200", "quantity": "1"}],
        status="saved", order_date="2026-10-24",
    )
    accounting.add_payment(customer_id, 5000, payment_date="2026-10-24")
    accounting.add_adjustment(customer_id, 2000, "跨页对账调整")

    expected = accounting.get_customer_account_ledger(customer_id)["current_balance_cents"]
    html = create_app().test_client().get("/customers/").get_data(as_text=True)
    row = next(line for line in html.split("<tr>") if "跨页对账客户" in line)
    # 100 + 200 + 20 − 50 = 270 元
    assert expected == 27000
    assert "¥270.00" in row


# ---------------------------------------------------------------- 6. 设置页

def test_backup_rows_expose_three_separate_buttons():
    """用户：「改为『另存』『恢复』『删除』，三个单独的按钮」。

    判据：每行三个按钮各自独立（不是一颗「另存 / 恢复」），且删除有自己的表单端点。
    """
    init_db()
    create_backup("pytest")
    html = create_app().test_client().get("/settings/").get_data(as_text=True)
    section = html.split('id="backupSection"', 1)[1].split("</section>", 1)[0]

    assert 'data-backup-toggle="copy"' in section
    assert 'data-backup-toggle="restore"' in section
    assert "/settings/backup/delete" in section
    assert "另存 / 恢复" not in section, "合并按钮已按用户要求拆成三个"
    # 三个按钮都必须在**摘要行**上（不需要先展开才能看到）。
    summary = section.split('class="backup-summary"', 1)[1].split("</div>", 1)[0]
    for hook in ('data-backup-toggle="copy"', 'data-backup-toggle="restore"', "/settings/backup/delete"):
        assert hook in summary, hook


def test_backup_delete_endpoint_removes_only_that_file():
    """删除只动指定的那一份；非法文件名（路径穿越）一律拒绝。"""
    init_db()
    first = create_backup("pytest-a")
    create_backup("pytest-b")
    before = {entry["name"] for entry in list_backups()}
    assert first.name in before

    client = create_app().test_client()
    response = client.post(
        "/settings/backup/delete", data={"backup_name": first.name}, follow_redirects=True
    )
    assert response.status_code == 200
    assert "已删除备份" in response.get_data(as_text=True)
    after = {entry["name"] for entry in list_backups()}
    assert first.name not in after
    assert len(after) == len(before) - 1, "只该删掉指定的那一份"

    # 路径穿越必须被拒绝，且不删任何东西。
    rejected = client.post(
        "/settings/backup/delete", data={"backup_name": "../erp.db"}, follow_redirects=True
    )
    assert "找不到要删除的备份文件" in rejected.get_data(as_text=True)
    assert {entry["name"] for entry in list_backups()} == after


def test_backup_panel_visibility_must_toggle_hidden_and_data_open_together():
    """展开面板的两个开关必须一起动 —— 这是本轮实测踩到的坑，必须钉住。

    Bootstrap 自带 `[hidden]{display:none!important}`。只加 `data-open` 时面板
    量到 `height=0`（点了「恢复」屏幕上什么都没出现，也不报错）；
    反过来只删 `hidden` 又会得到一个没有内距/描边/背景的裸表单。
    """
    init_db()
    create_backup("pytest")
    html = create_app().test_client().get("/settings/").get_data(as_text=True)

    # 初始态：两个面板都带 hidden（服务端渲染即收起）。
    section = html.split('id="backupSection"', 1)[1].split("</section>", 1)[0]
    assert section.count('class="backup-panel"') == 2
    assert section.count('class="backup-panel" data-backup-panel="copy" hidden') == 1
    assert section.count('class="backup-panel" data-backup-panel="restore" hidden') == 1
    # JS 必须两个属性一起切换。
    assert "panel.removeAttribute('hidden')" in html
    assert "panel.setAttribute('hidden', '')" in html
    # 作者样式也要给展开态兜一层（防 Bootstrap 那条 !important 之外的环境差异）。
    assert ".backup-panel[hidden]{display:none!important}" in html


def test_print_info_prompt_card_sits_under_the_print_group():
    """用户：「设置页的『打印信息提示』需要放在『打印』这一项下」。

    判据是**文档顺序**：打印分组标题 → 打印信息提示卡片 → 数据与备份分组标题。
    它仍是「点了立即生效」的独立卡片（不在主表单里），所以放在 `</form>` 之后。
    """
    init_db()
    html = create_app().test_client().get("/settings/").get_data(as_text=True)
    print_group = html.index('id="groupPrint"')
    prompt = html.index('id="printInfoPromptSection"')
    data_group = html.index('id="groupData"')
    assert print_group < prompt < data_group, "打印信息提示必须在「打印」与「数据与备份」之间"
    # 仍在主表单之外（否则会跟着「保存设置」一起提交，语义变成「保存才生效」）。
    main_form_end = html.index('</form>', html.index('id="settingsMainForm"'))
    assert main_form_end < prompt


# ---------------------------------------------------------------- 结构健壮性

def test_customer_table_colgroup_still_matches_header_columns():
    """「期初 → 欠款」改了表头文字，列数不能跟着漂（历史缺陷：表头与 colgroup 不同步）。"""
    init_db()
    html = create_app().test_client().get("/customers/").get_data(as_text=True)
    colgroup = re.search(r"<colgroup>(.*?)</colgroup>", html, re.S).group(1)
    thead = re.search(r"<thead>(.*?)</thead>", html, re.S).group(1)
    assert colgroup.count("<col") == thead.count("<th")
