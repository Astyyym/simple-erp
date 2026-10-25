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
            [{"product_name": "回收站卡片器材R1", "unit": "个",
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
    """回收站：单据卡独占整行（主体），商品、客户两张卡两栏并排（附属），窄屏回落单列。

    2026-10-16 重做：旧版把商品/客户塞在右窄栏，把「单据」那张主表的列压到省略号截断
    （单号/金额/删除时间全看不见）。改成单据独占整行后列宽才够用。
    """
    page = _html("/recycle/")
    assert 'class="recycle-layout"' in page
    assert ".recycle-layout{display:grid;gap:16px;align-items:start}" in page
    assert ".recycle-side{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))" in page
    assert "@media(max-width:1100px){.recycle-side{grid-template-columns:minmax(0,1fr)}}" in page
    # 三张卡片：单据（整行）+ 商品、客户（并排）。
    assert page.count('class="recycle-card"') == 3
    # 计数徽章三处；空计数会额外带 is-zero（褪成中性色，不拿「有内容」的蓝徽章提示空）。
    assert page.count('class="recycle-count') == 3
    for title in ("单据", "商品", "客户"):
        assert f"<h3>{title} <span" in page, title
    # 每个卡片的表格都在自己的滚动区里，不再把整页撑宽。
    assert page.count('class="recycle-scroll"') == 3
    # 空状态不再留空白表体。
    assert page.count('class="recycle-empty"') == 3
    # 单据卡带四类类型筛选（与单据管理页同一套语义）。
    assert 'class="recycle-layer"' in page
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
    # 批次 2（2026-10-16）：gap 18 → 16，并新增左侧常驻目录 + 右侧内容骨架。
    # 保护点仍是「只有宽屏才两列」「宽卡片与操作条跨整行」。
    assert ".settings-form{display:flex;flex-direction:column;gap:16px;max-width:920px}" in page
    assert "@media(min-width:1440px)" in page
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in page
    # 字段/说明很多的卡片保持整行，避免压窄后换行难读。
    assert 'class="settings-card settings-card-wide"' in page
    assert ".settings-form>.settings-card.settings-card-wide{grid-column:1/-1}" in page
    # 操作按钮条已搬到左栏（2026-10-16 用户决策）；宽屏网格里不再有跨行操作条。
    assert ".settings-form>.settings-actions{grid-column:1/-1}" not in page
    # 批次 2：左侧目录 + 右侧内容的分区骨架，以及 4 个分组的锚点。
    assert 'class="settings-layout"' in page
    assert 'class="settings-toc"' in page
    assert ".settings-layout{display:grid;grid-template-columns:200px minmax(0,1fr)" in page
    for anchor in ("groupBasic", "groupPrint", "groupData", "groupPurgeLegacy"):
        assert f'id="{anchor}"' in page, f"缺少设置分组锚点 {anchor}"
    # 保存设置（2026-10-16 决策，2026-10-17 按用户截图反馈重做）：左栏里与「设置分区」目录
    # **并列的独立圆角卡片**，中间留空隙。
    # 实测病根（2026-10-17）：上一版把保存卡嵌在 nav.settings-toc 内部，而 toc 自带内边距
    # 与玻璃背景，视觉上被读成「同一张大卡片里又嵌了一层描边面板」；用户明确指出
    # 「你为什么还是把它们做到了一块儿」。故断言从「在目录内」改为「与目录平级 + 有间距」，
    # 并钉住 sticky 挂在容器上（挂在 toc 上时其包含块仅两卡高度，滚动即整列滚出视口）。
    assert "position:sticky;bottom:-1px" not in page
    assert 'class="settings-side"' in page
    assert ".settings-side{position:sticky;top:0;display:flex;flex-direction:column;gap:14px" in page
    assert 'class="settings-save-card"' in page
    assert ".settings-save-card{" in page and ".settings-save-card .btn{width:100%}" in page
    # 不再有「嵌在父卡内部」的伪元素分隔线（那正是被读成同一张卡的原因）。
    assert ".settings-save-card:before" not in page
    side = page[page.index('class="settings-side"'):page.index('class="settings-main"')]
    assert 'class="settings-toc"' in side and 'class="settings-save-card"' in side
    # 两者是兄弟节点：保存卡必须在 nav.settings-toc 之外。
    assert side.index('</nav>') < side.index('class="settings-save-card"')
    assert 'id="settingsSaveBtn"' in side and 'form="settingsMainForm"' in side
    # 右侧分区标题必须比卡片标题更醒目（实测病根：分区 15px < 卡片 h3 18px 层级倒挂）。
    # 2026-10-17：分区标题改版（序号徽标 + 24px 主色标题 + sticky），断言跟着样式走：
    # 这里只守「设置页有分区标题样式」这件事本身，不钉死具体字号。
    assert ".settings-group-head{" in page
    assert ".settings-group-head h2{" in page
    # 2026-10-18：序号徽标已按用户要求删除，改为圆角矩形卡片（四角圆 + 内阴影左色条）。
    assert ".settings-group-index" not in page
    assert "border-radius:12px" in page
    assert "box-shadow:inset 5px 0 0 0 var(--erp-primary)" in page
    # 「点了立即生效、不用先保存」的卡片有独立标注。
    assert "settings-instant" in page


def test_settings_device_table_adapts_instead_of_overhanging_the_card():
    """已配对设备表在窄窗口下不能伸出卡片右边界（2026-10-24 用户截图反馈）。

    病根：7 列全 nowrap，表格固有宽度约 1027px；卡片内宽小于它时 table 既不换行
    也不收缩，直接顶穿卡片（换用真实形态数据后实测：1440 视口下卡片内宽 795px、
    表格 1027px，溢出 37px）。
    `.settings-table-shell` 原来只有 `min-width:0`，它管的是「网格子项不被内容撑开」，
    管不到「表格自身比容器宽」。

    修法两层，缺一不可：
    ① 容器 `overflow-x:auto` —— 兜底，任何情况下都不画出卡片；
    ② `container-type:inline-size` + `@container` —— 按**容器**宽度（不是视口宽度）
       把整表切成「每条设备一张小卡」。视口媒体查询做不到：左栏 200px 让容器
       比视口窄 300px 以上。

    ⚠️ 断点 860 是量出来的：真实数据（型号 `sdk_gphone64_x86_64`、IP
    `192.168.10.21`、带「已同步」徽标）下自然宽度 1027px；徽标独占一行 +
    设备名可断词后最小内容宽度 782px。断点必须高于 782，否则 782–860 这一档
    会先冒出横向滚动条而不是切形态。**别拿演示库的 `127.0.0.1` 去量** ——
    那样会得到 720px 的假结论，断点定低就会在 1440 窗口下直接溢出。
    """
    page = _html("/settings/")
    assert ".settings-table-shell{min-width:0;max-width:100%;overflow-x:auto" in page
    assert "container-type:inline-size" in page
    assert "@container (max-width:860px){" in page
    # 阈值必须按容器算：表格宽度契约也要同时钉住，否则 width:auto 会把固有宽度带回来。
    assert "table.settings-plain-table{width:100%;max-width:100%}" in page

    # 徽标独占一行：inline 时这一列 = 日期 + 徽标 ≈ 291px，整表 1027px 装不下。
    assert ".mobile-sync-badge{\n    display:block;width:fit-content" in page
    # 设备名要能断词（型号名是不可断的长 token），否则最小内容宽度仍卡在 892px。
    assert "td:first-child{overflow-wrap:anywhere}" in page or "code{overflow-wrap:anywhere}" in page

    # 堆叠形态靠 td 的 data-label 画标签，缺一个就会出现「没有名字的值」。
    assert 'class="table align-middle settings-plain-table settings-stack-table"' in page
    for label in ("设备", "接入方式", "设备 ID", "配对时间", "最后访问", "来源 IP"):
        assert f'data-label="{label}"' in page, label
    # 自动刷新重绘的行也必须带 data-label（只改 Jinja 模板会漏掉这段 JS 拼串，
    # 配对一台手机后表格就变成没有标签的一串值）。
    assert '+ \'<td data-label="设备">\'' in page
    assert '+ \'<td data-label="来源 IP">' in page
    # 操作列没有 data-label：堆叠时靠 `:not([data-label])` 占满整行靠右。
    assert "td:not([data-label])" in page
    # 极窄容器：徽标自己也要允许换行，否则 500px 视口（容器 138px < 徽标 140px）仍溢出。
    assert "table.settings-stack-table .mobile-sync-badge{" in page
    # 徽标在 DOM 里是 td 的第三个子项；td 是 2 列 grid（::before 标签 | 值），
    # 不显式钉列的话它会掉进第二行的标签列（68px），被挤成三行小圆饼（打包实跑抓到）。
    assert "grid-column:2/-1;justify-self:start" in page
    assert "white-space:normal;max-width:100%" in page


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
    # 2026-10-17 用户截图反馈：金额汇总容器**不能再是 3 列网格**。
    # 实测病根：该段结构早已变成「.section-heading 标题 + .analytics-amount-grid 卡片网格」
    # 两个子元素，而 .analytics-summary 上残留的 3 列网格把两者各塞进一列 —— 标题只占 1/3 宽，
    # 六张卡被压进 391px（每张 90px），金额 ¥24012.03 实需 110px、溢出 52px、退货金额折两行。
    assert ".erp-analytics .analytics-summary{margin-bottom:18px}" in css
    assert ".erp-analytics .analytics-summary{display:grid" not in css
    # 净值卡跨两列：草图上第二行两个宽框分别压在四张卡的 1-2 与 3-4 之下。
    assert ".erp-analytics .analytics-card-net{grid-column:span 2" in css
    # 窄档必须把 span 收掉，否则 1 列网格里 span 2 会撑出隐式列、产生横向溢出。
    assert ".erp-analytics .analytics-card-net{grid-column:span 1}" in css
    # 筛选区：客户+商品并排在时间控件**右侧**同一行（2026-10-17 第三轮用户截图要求；
    # 此前是 column 的两行布局）。放不下时由 flex-wrap 折行，窄档规则见媒体查询。
    assert 'class="filter-row-counterparty"' in analytics
    assert ".erp-analytics .filter-tier-query{display:flex;flex-direction:row;flex-wrap:wrap" in css
    # 时间块内部排布（2026-10-24 用户第三张截图要求：**日期输入框放在「按日到日」右侧同一行**）。
    # 此前是 grid 两行（第一行「时间范围 + 模式按钮」，第二行起止日期占整行），
    # 用户圈出日期框要求与模式按钮同行 → 改成单行 flex，放不下才换行。
    assert ".erp-analytics .filter-field-composite .filter-inline{display:flex" in css
    # 日期块不再独占整行（旧规则 `.dates{grid-column:1 / -1}` 必须消失，否则两行结构又回来）。
    assert ".erp-analytics .filter-field-composite .filter-inline>.dates{grid-column:1 / -1" not in css
    assert ".erp-analytics .filter-field-composite .filter-inline>.dates{margin:0;flex:0 0 auto}" in css
