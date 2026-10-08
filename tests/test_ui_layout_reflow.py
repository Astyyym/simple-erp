"""回收站卡片网格、设置页宽屏双列、筛选卡满行网格的结构契约。

这些是 2026-10-06 反馈的 UI 重排：回收站从 5 条整宽表格改成两列卡片；
设置页宽屏把下方设置项放到右侧（窄屏仍单列）；两张筛选卡统一为满行字段网格。

回收站的复选框在 `{% for %}` 循环里：**必须先造出已删除记录**再断言，
否则空列表会让断言在「结构本身没问题」的情况下假失败。
"""
from erp import create_app
from erp.db import get_db
from erp.services import accounting


def _html(path):
    return create_app().test_client().get(path).get_data(as_text=True)


def _seed_recycle_bin():
    """造已删除记录，让回收站各分区都有真实行。

    先 create_app() 触发建表：否则直接调服务会撞 no such table: customers。
    勾选框在 `{% for %}` 循环里，空列表渲染不出 name 属性 —— 所以必须真造数据。
    """
    app = create_app()
    client = app.test_client()
    with app.app_context():
        customer_id = accounting.create_customer("回收站卡片往来", opening_balance_cents=0)
        draft_id = accounting.create_order_from_typed_rows(
            customer_id, "MD202610070001",
            [{"product_name": "回收站卡片器材", "spec": "R1", "unit": "个",
              "quantity": "1", "unit_price_yuan": "10.00"}],
            status="draft", order_date="2026-10-07",
        )
        accounting.delete_order(draft_id, "回收站卡片测试")
        # 商品与客户：另造干净记录再走路由删除（有账务的客户不允许物理删除）。
        with get_db() as conn:
            product_id = conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0]
            clean_customer = accounting.create_customer("回收站卡片空客户", opening_balance_cents=0)
    client.post(f"/products/{product_id}/delete")
    client.post(f"/customers/{clean_customer}/delete")
    return customer_id, draft_id, product_id


def test_recycle_bins_are_two_column_cards_not_full_width_tables():
    """回收站：左列一张四类单据合并列表，右列商品、客户两张卡片，窄屏回落单列。"""
    page = _html("/recycle/")
    assert 'class="recycle-layout"' in page
    assert ".recycle-layout{display:grid;grid-template-columns:minmax(0,1.9fr) minmax(0,1fr)" in page
    assert "@media(max-width:1280px){.recycle-layout{grid-template-columns:minmax(0,1fr)}}" in page
    # 三张卡片：单据（左列）+ 商品、客户（右列）。
    assert page.count('class="recycle-card"') == 3
    assert page.count('class="recycle-count"') == 3
    for title in ("单据", "商品", "客户"):
        assert f"<h3>{title} <span" in page, title
    # 每个卡片的表格都在自己的滚动区里，不再把整页撑宽。
    assert page.count('class="recycle-scroll"') == 3
    # 空状态不再留空白表体。
    assert page.count('class="recycle-empty"') == 3
    # 左列单据卡带四类类型筛选（与单据管理页同一套语义）。
    assert 'class="recycle-type-row"' in page
    assert 'class="recycle-type-options"' in page
    for value in ("sale", "return", "purchase", "purchase_return"):
        assert f'id="recycleType{value}"' in page
    assert 'data-recycle-type-all="1"' in page and 'data-recycle-type-none="1"' in page


def test_recycle_bulk_forms_and_versioned_controls_survive_the_card_layout():
    """改版不能丢批量端点、勾选键与退拿货版本号（后端契约）。

    必须先造出已删除记录：勾选框在循环内，空回收站渲染不出 name 属性。
    """
    _seed_recycle_bin()
    page = _html("/recycle/")

    # 合并列表走统一批量端点；商品/客户按类型端点（同一套 data-recycle-submit 驱动）。
    assert 'id="recycleBulkForm"' in page and 'action="/recycle/bulk"' in page
    assert 'data-recycle-submit="restore"' in page and 'data-recycle-submit="purge"' in page
    for form_id in ("recycleBulkForm", "recycleProductForm", "recycleCustomerForm"):
        assert f'id="{form_id}"' in page, form_id
    # 商品/客户的默认 action 在标记里（purge）；restore 由同一段 JS 按 action 拼接，
    # 其真实行为由 tests/frontend/recycle_bulk.test.mjs 用真 JS 覆盖。
    for action in ("/recycle/bulk/product/purge", "/recycle/bulk/customer/purge"):
        assert action in page, action
    assert "/recycle/bulk/${checkboxName === 'product_ids' ? 'product' : 'customer'}/${action}" in page

    # 勾选框必须真的渲染出来（这次有已删除记录），键为 `类型:id`。
    assert 'name="recycle_ids"' in page
    assert 'name="customer_ids"' in page
    assert 'name="product_ids"' in page
    # 行内单条操作仍按类型分派到既有端点。
    assert "/recycle/order/" in page and "/purge" in page
    # 计数徽标反映真实数量，不是写死的。
    assert '<h3>单据 <span class="recycle-count" id="recycleDocumentCount">1</span>' in page
    # 三类都已有记录：不再有空白表体（空状态由第一个用例覆盖）。
    assert 'class="recycle-empty"' not in page
    # 退拿货的逐行版本号契约（data-recycle-version → version_<id>）由
    # test_purchase_returns.py 的 test_return_recycle_has_versioned_bulk_controls_and_single_active_entry 覆盖。


def test_recycle_action_cells_reserve_width_so_buttons_are_not_clipped():
    """操作列必须自己占住宽度，否则「确认删除」会被裁掉。

    `stable-table` 是 `table-layout:fixed` + `td{overflow:hidden}`。若把 `action-wrap`
    直接写在 `<td>` 上（本页曾有 3 处），该列只分到剩余宽度的均分零头：实测 1600px 宽下
    商品/客户卡片操作列仅 66.9px，「确认删除」被裁 76.1px，几乎点不到。
    正确写法是本项目通用的 `<td class="action-cell"><div class="action-wrap">`。
    """
    _seed_recycle_bin()
    page = _html("/recycle/")
    # 三张表（单据 / 商品 / 客户）都给出操作列宽度。
    assert ".recycle-scroll .action-cell{width:150px;min-width:150px}" in page
    assert page.count('class="action-cell"') >= 3
    # 反例：action-wrap 不能再直接当单元格用。
    assert '<td class="action-wrap"' not in page
    # 每个 action-cell 内层都包一层 action-wrap（沿用全局布局约定）。
    assert page.count('<td class="action-cell"><div class="action-wrap">') >= 3


def test_settings_uses_two_columns_only_on_wide_screens():
    """宽屏把下方设置项放到右侧；窄屏保持单列并可用。"""
    page = _html("/settings/")
    assert ".settings-form{display:flex;flex-direction:column;gap:18px;max-width:920px}" in page
    assert "@media(min-width:1440px)" in page
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in page
    # 字段/说明很多的卡片保持整行，避免压窄后换行难读。
    assert 'class="settings-card settings-card-wide"' in page
    assert ".settings-form>.settings-card.settings-card-wide{grid-column:1/-1}" in page
    # 操作按钮条也跨整行。
    assert ".settings-form>.settings-actions{grid-column:1/-1}" in page


def test_both_filter_cards_share_one_full_row_field_grid():
    """单据管理与数据分析的筛选卡：同一套 .filter-card 三段式结构。"""
    orders = _html("/orders/")
    assert 'class="filter-card"' in orders
    # 三段式：查询三要素（时间组合控件 + 客户）→ 条件多选 → 动作条。
    assert ".filter-tier-query{display:flex;flex-wrap:wrap" in orders
    assert ".filter-field-composite{flex:1.2 1 320px" in orders
    assert ".filter-tier-conditions{display:grid" in orders
    # 订单类型整行：标签 + 四类 + 清空同行左对齐（不勾选=全部，无全选）。
    assert ".filter-type-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px}" in orders
    assert ".filter-type-options,.filter-type-bulk{display:flex;flex-wrap:wrap;gap:8px}" in orders
    assert "justify-content:flex-end" not in orders.split('.filter-type-row')[1].split('}')[0]
    assert 'data-type-all' not in orders and 'data-status-all' not in orders

    analytics = _html("/analytics/")
    assert "analytics-v4.css" in analytics
    # 数据分析页复用同一套类名，不再维护第二套筛选卡样式。
    assert 'class="filter-card"' in analytics
    assert 'class="filter-tier-query"' in analytics
    assert 'class="filter-field-composite"' in analytics
    assert 'class="filter-field-type"' in analytics
    assert 'class="filter-type-row"' in analytics
    assert 'class="filter-type-bulk"' in analytics
    assert 'class="analytics-order-type"' not in analytics
    assert 'data-type-all' not in analytics
    from erp import create_app as _create
    app = _create()
    css = app.test_client().get("/static/analytics-v4.css").get_data(as_text=True)
    # 旧的 analytics-filters / filter-action 规则已迁入 base.html，不再重复定义。
    assert ".erp-analytics .analytics-filters" not in css
    assert ".erp-analytics .filter-action" not in css
