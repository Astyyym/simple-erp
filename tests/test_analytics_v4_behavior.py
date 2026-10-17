"""C4 analytics: real isolated SQLite and real rendering, not prototype data."""
from contextlib import contextmanager
from flask import template_rendered
from erp import create_app
from erp.db import init_db, get_db
from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
from erp.services.analytics import summarize_analytics


@contextmanager
def rendered(app):
    contexts = []
    def record(sender, template, context, **extra):
        contexts.append(context)
    template_rendered.connect(record, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(record, app)


def legacy_sale(customer, product, no, day, quantity="1", price="10"):
    return create_order_from_typed_rows(customer, no, [{"product_id": product, "product_name": "历史商品", "unit": "个", "quantity": quantity, "unit_price_yuan": price}], status="saved", order_date=day)


def test_explicit_empty_dates_route_keeps_open_scope_and_prior_month_sales():
    init_db()
    customer = create_customer("虚构往来")
    product = create_product("历史商品", "个")
    legacy_sale(customer, product, "MD202509010001", "2025-09-01")
    app = create_app()
    with rendered(app) as contexts:
        response = app.test_client().get("/analytics/?start_date=&end_date=&customer_id=" + str(customer) + "&product_name=历史商品")
    assert response.status_code == 200
    result = contexts[-1]["result"]
    assert result["summary"]["net_sales_amount_cents"] == 1000
    assert result["filters"]["start_date"] == result["filters"]["end_date"] == ""
    html = response.get_data(as_text=True)
    assert 'name="start_date" value=""' in html
    assert 'name="end_date" value=""' in html


def test_single_open_date_heatmap_uses_observed_boundary_not_one_day():
    init_db()
    customer = create_customer("单边日期对象")
    product = create_product("历史商品", "个")
    legacy_sale(customer, product, "MD202509010001", "2025-09-01")
    legacy_sale(customer, product, "MD202509030001", "2025-09-03")
    for query in ({"start_date": "2025-08-31"}, {"end_date": "2025-09-04"}):
        result = summarize_analytics(customer_id=customer, **query)
        assert result["summary"]["net_sales_amount_cents"] == 2000
        days = [cell["date"] for cell in result["heatmap"]]
        assert "2025-09-01" in days and "2025-09-03" in days
        assert days[0] == query.get("start_date", "2025-09-01")
        assert days[-1] == query.get("end_date", "2025-09-03")
    empty = summarize_analytics(start_date="2026-10-01", customer_id=customer)
    assert empty["heatmap"] == []


import pytest


@pytest.mark.parametrize("bad", [{"start_date": "bad-date"}, {"start_date": "2026-10-03", "end_date": "2026-10-01"}, {"customer_id": "true"}, {"customer_id": "0"}, {"customer_id": "-1"}, {"customer_id": "99999"}, {"metric": "fake"}])
def test_invalid_route_scope_retains_inputs_without_fallback_results(bad):
    init_db()
    customer = create_customer("错误筛选对象")
    product = create_product("历史商品", "个")
    legacy_sale(customer, product, "MD202610010001", "2026-10-01")
    query = {"start_date": "", "end_date": "", "customer_id": str(customer), "product_name": "历史商品", **bad}
    app = create_app()
    with rendered(app) as contexts:
        response = app.test_client().get("/analytics/", query_string=query)
    assert response.status_code == 400
    assert contexts[-1]["result"] is None
    assert all(str(contexts[-1]["filters"][key]) == value for key, value in query.items())
    html = response.get_data(as_text=True)
    assert "回退到本月" not in html
    assert 'id="analysisMetrics"' not in html


def test_removed_spec_parameter_is_ignored_not_rejected():
    """决策变更（v14）：型号筛选整条删除 —— 传 spec 不再报错，也不再改变结果。

    旧用例把 {"spec": "fake"} 列为非法筛选（应 400）；该维度删除后 spec 被路由忽略。
    """
    init_db()
    customer = create_customer("忽略型号对象")
    product = create_product("历史商品", "个")
    legacy_sale(customer, product, "MD202610010001", "2026-10-01")
    app = create_app()
    with rendered(app) as contexts:
        response = app.test_client().get("/analytics/", query_string={
            "start_date": "", "end_date": "", "customer_id": str(customer),
            "product_name": "历史商品", "spec": "fake"})
    assert response.status_code == 200
    assert contexts[-1]["result"] is not None
    assert contexts[-1]["result"]["summary"]["net_sales_amount_cents"] == 1000


def test_unfilled_model_sentinel_and_model_dimension_are_removed():
    """决策变更（v14）：型号筛选整条删除 —— `__unfilled__`「未填写型号」哨兵随之消失。

    旧用例钉「未填写型号是独立于『全部型号』的可选值，且只筛出无型号商品」。
    该维度删除：spec 参数被忽略、页面不再有未填写型号选项；商品身份=单一名称，
    「历史商品」与「历史商品Q1」是两条独立名称。
    """
    init_db()
    customer = create_customer("型号与对象留存")
    blank = create_product("历史商品", "个")
    model = create_product("历史商品Q1", "个")
    legacy_sale(customer, blank, "MD202509010001", "2025-09-01")
    create_order_from_typed_rows(customer, "MD202509010002", [{"product_id": model, "product_name": "历史商品Q1", "unit": "个", "quantity": "1", "unit_price_yuan": "20"}], status="saved", order_date="2025-09-01")
    app = create_app()
    with rendered(app) as contexts:
        response = app.test_client().get("/analytics/", query_string={"start_date": "", "end_date": "", "customer_id": customer, "product_name": "历史商品", "spec": "__unfilled__"})
    assert response.status_code == 200
    result = contexts[-1]["result"]
    # 名称精确匹配「历史商品」→ 只命中它自身；型号商品是另一条独立身份。
    assert [row["product_id"] for row in result["product_rows"]] == [blank]
    assert result["summary"]["net_sales_amount_cents"] == 1000
    assert str(contexts[-1]["filters"]["customer_id"]) == str(customer)
    html = response.get_data(as_text=True)
    # 客户字段 2026-10-17 改联想输入：选中态由 hidden `customer_id` + 可见框里的名称回显承担（不再有 select 的 selected）。
    assert f'id="customerFilter" name="customer_id" value="{customer}"' in html
    assert 'value="型号与对象留存"' in html
    # `__unfilled__` 哨兵已删除：页面不再提供型号选择控件，也没有「未填写型号 / 全部型号」选项。
    assert 'id="specFilter"' not in html
    assert "未填写型号" not in html and "全部型号" not in html
    # 不按商品筛选时覆盖两条独立名称商品，合计 3000。
    assert summarize_analytics(customer_id=customer)["summary"]["net_sales_amount_cents"] == 3000


def sale_for_product(customer, product, no, day, quantity="1", price="10"):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product,)).fetchone()
    # 决策变更（v14）：商品身份=单一名称，明细 payload 不再有 spec；row 已无 spec 键。
    return create_order_from_typed_rows(customer, no, [{"product_id": product, "product_name": row["name"], "unit": row["unit"], "quantity": quantity, "unit_price_yuan": price}], status="saved", order_date=day)


def test_quantity_rankings_keep_one_all_product_chart_and_classify_real_sales():
    """数量榜只按数量排序、全品类同一张榜；不再按单位分组。"""

    from erp.services.accounting import create_return_order_from_source
    init_db()
    customer = create_customer("完整排行对象")
    ids = []
    for index in range(13):
        product = create_product(f"排行计件{index:02}", "个")

        sale_for_product(customer, product, f"MD20250901{index+1:04}", "2025-09-01", quantity=str(index+1))
        ids.append(product)
    meter = create_product("排行计量", "米")
    sale_for_product(customer, meter, "MD202509010101", "2025-09-01", quantity="100")
    free = create_product("零价实际销售", "个")
    sale_for_product(customer, free, "MD202509010102", "2025-09-01", price="0")
    unsold = create_product("期末前未销售", "个")
    too_new = create_product("期末后建档", "个")
    inactive = create_product("停用未销售", "个")
    returned = create_product("全部退回", "个")

    source = sale_for_product(customer, returned, "MD202509010103", "2025-09-01", quantity="2")
    with get_db() as conn:
        source_item = conn.execute("SELECT id FROM order_items WHERE order_id=?", (source,)).fetchone()[0]
        conn.execute("UPDATE products SET created_at='2025-01-01 00:00:00'")
        conn.execute("UPDATE products SET created_at='2026-01-01 00:00:00' WHERE id=?", (too_new,))
        conn.execute("UPDATE products SET is_active=0 WHERE id=?", (inactive,))
    create_return_order_from_source(customer, "MD202509020001", source, [{"source_item_id": source_item, "quantity": "2"}], status="saved", order_date="2025-09-02")
    result = summarize_analytics(start_date="2025-09-01", end_date="2025-09-02", customer_id=customer)

    # 只有一张榜，全部商品都在里面（含「米」单位），单位不再是分组键
    groups = result["ranking_groups"]["hot_sales"]
    assert len(groups) == 1 and groups[0]["unit"] is None
    ranked = groups[0]["rows"]
    assert meter in [row["product_id"] for row in ranked], "计量单位商品也必须进入同一张榜"
    # 全品类按净销售数量降序：100 米 → 13 个 → … → 1 个
    quantities = [row["net_sales_quantity_3dp"] for row in ranked]
    assert quantities == sorted(quantities, reverse=True)
    assert ranked[0]["product_id"] == meter
    assert [row["product_id"] for row in ranked if row["product_id"] in ids] == list(reversed(ids))
    assert [row["product_id"] for row in result["rankings"]["unsold"]] == [unsold]
    assert [row["product_id"] for row in result["rankings"]["net_returns"]] == [returned]
    assert free not in [row["product_id"] for row in result["rankings"]["unsold"]]
    assert result["summary"]["quantity_comparable"] is False
    assert result["filters"]["metric"] == "amount"
    assert all(cell["metric_value"] == cell["flow_net_cents"] for cell in result["heatmap"])


def test_formal_v4_layout_renders_every_month_inside_the_applied_scope():
    init_db()
    customer = create_customer("跨年完整范围对象")
    product = create_product("历史商品", "个")
    legacy_sale(customer, product, "MD202401010001", "2024-01-01")
    legacy_sale(customer, product, "MD202610010001", "2026-10-01")
    app = create_app()
    query = {"start_date": "2024-01-01", "end_date": "2026-10-01", "customer_id": customer, "product_name": "历史商品", "sort": "amount", "tab": "rank"}
    with rendered(app) as contexts:
        response = app.test_client().get("/analytics/", query_string=query)
    assert response.status_code == 200
    result, view = contexts[-1]["result"], contexts[-1]["view"]
    assert result["filters"]["metric"] == "amount"
    assert result["summary"]["net_sales_amount_cents"] == 2000
    # 热力图不再有独立年份选择：范围内每个自然月都渲染成日历
    assert [month["month"] for month in view["calendars"]] == sorted({cell["date"][:7] for cell in result["heatmap"]})
    assert view["calendars"][0]["month"] == "2024-01" and view["calendars"][-1]["month"] == "2026-10"
    assert view["heatmap"] == result["heatmap"]
    assert result["heatmap"][-1]["date"] == "2026-10-01"
    assert view["tab"] == "rank" and view["sort"] == "amount"
    html = response.get_data(as_text=True)
    ids = ["analyticsFilters", "analysisMetrics", "layeredAnalysis", "productAnalysis"]
    assert [html.index(f'id="{element}"') for element in ids] == sorted(html.index(f'id="{element}"') for element in ids)
    assert all(f'id="{element}"' in html for element in ("heatTab", "rankTab"))
    # 决策 ②/③：成本诊断页签、成本覆盖说明与库存告急锚点整块下线，页面只剩热力图与产品排行。
    assert 'id="healthTab"' not in html and 'id="inventoryAlert"' not in html
    assert "成本诊断" not in html and "毛利润" not in html and "净成本" not in html
    # 数据分析页不再提供"进入往来对账"入口（改由账款管理的单据类型筛选承担）
    assert 'id="reconEntry"' not in html and 'id="openRecon"' not in html


def _database_snapshot():
    """只读快照：证明分析页 GET 不写库（原先借用的库存测试助手已随库存系统删除）。"""
    with get_db() as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY 1')] for table in tables}


def test_ranking_size_selector_replaces_pagination_and_keeps_aggregate_scope():
    init_db()
    customer=create_customer("排行分页对象")
    ids=[]
    for index in range(13):
        # 决策变更（v14）：商品身份=单一名称 —— 旧 (名称'分页商品', 型号=index) 合并为「分页商品0…12」。
        pid=create_product(f"分页商品{index}","个")
        sale_for_product(customer,pid,f"MD20250901{index+1:04}","2025-09-01",quantity=str(index+1))
        ids.append(pid)
    app=create_app()
    client=app.test_client()
    before=_database_snapshot()
    # 合并后名称唯一、无法再用基名一次选中整个系列，故改为只按客户筛选（13 个商品即全部）。
    query={"start_date":"", "end_date":"", "customer_id":customer, "product_name":"", "tab":"rank", "sort":"amount", "rank_key":"hot_sales"}
    with rendered(app) as contexts:
        response=client.get('/analytics/',query_string=query)
    assert response.status_code==200
    preview=contexts[-1]['view']['rank_groups']['hot_sales'][0]
    # 默认每组 10 项，总量仍是完整结果
    assert preview['total_count']==13 and len(preview['rows'])==10 and preview['shown_count']==10
    assert 'id="rankingSize"' in response.get_data(as_text=True)
    # 旧分页入口已移除
    assert '查看完整' not in response.get_data(as_text=True) and 'rank_page' not in response.get_data(as_text=True)
    for size, expected in (('20', 13), ('all', 13)):
        with rendered(app) as contexts:
            response=client.get('/analytics/',query_string={**query,'rank_size':size})
        assert response.status_code==200
        context=contexts[-1]
        group=context['view']['rank_groups']['hot_sales'][0]
        assert len(group['rows'])==expected and group['total_count']==13
        assert context['view']['rank_size']==size
        # 条形按组内最大值等比缩放，且带数值标签
        assert group['rows'][0]['bar_percent']==100.0
        assert group['rows'][0]['value_text'] and group['rows'][0]['label'].startswith('分页商品')
        assert context['result']['summary']['net_sales_amount_cents']==sum(range(1,14))*1000
        assert len(context['view']['product_rows'])==13
        assert context['filters']['start_date']==context['filters']['end_date']==''
        assert context['view']['sort']=='amount' and context['view']['tab']=='rank'
    # 非法的每组条数被拒绝
    assert client.get('/analytics/',query_string={**query,'rank_size':'7'}).status_code==400
    assert _database_snapshot()==before


def test_removed_heat_year_parameter_is_ignored_not_rejected():
    """热力图年份选择已删除：旧链接里的 heat_year 不再报错，也不改变渲染范围。"""
    app=create_app()
    response=app.test_client().get('/analytics/?start_date=&end_date=&heat_year=0000')
    assert response.status_code==200
    assert 'id="heatGrid"' in response.get_data(as_text=True) or 'empty-state' in response.get_data(as_text=True)
    assert 'id="heatYear"' not in response.get_data(as_text=True)


def test_invalid_filter_values_stay_visible_instead_of_appearing_as_all_party():
    app=create_app()
    html=app.test_client().get('/analytics/',query_string={'start_date':'bad-date','end_date':'','customer_id':'99999','product_name':'未知商品','spec':'错误型号'}).get_data(as_text=True)
    assert 'id="invalidScopeInputs"' in html
    assert all(value in html for value in ('bad-date','99999','未知商品'))
    # 决策变更（v14）：型号不再是筛选维度 —— 无效 spec 值不再被回显/参与校验。
    assert '错误型号' not in html
    assert 'id="analysisMetrics"' not in html


def test_analytics_filter_card_uses_aligned_grid_and_split_order_type_block():
    """筛选卡：与单据管理页共用 .filter-card 三段式；订单类型标签与清空同行、操作条独立分隔。"""
    from pathlib import Path

    init_db()
    html = create_app().test_client().get('/analytics/').get_data(as_text=True)
    base = (Path(__file__).resolve().parents[1] / "app" / "erp" / "templates" / "base.html").read_text(encoding="utf-8")
    css = (Path(__file__).resolve().parents[1] / "app" / "erp" / "static" / "analytics-v4.css").read_text(encoding="utf-8")

    assert 'class="filter-card"' in html
    assert 'class="row analytics-filters"' not in html
    # 共享样式在 base.html：查询三要素（时间组合控件）+ 条件多选 + 动作条。
    assert ".filter-tier-query{display:flex;flex-wrap:wrap" in base
    assert ".filter-card>*{min-width:0}" in base
    assert ".filter-field-composite{flex:1.2 1 320px" in base
    assert ".filter-actions{display:flex" in base
    # 嵌套日期行不再使用 Bootstrap 负外边距
    assert ".filter-card .row{margin-left:0;margin-right:0}" in base

    type_row = html.split('class="filter-type-row"', 1)[1].split('filter-type-hint', 1)[0]
    assert 'class="filter-type-options"' in type_row
    assert 'class="filter-type-bulk"' in type_row
    # 清空与四类同行左对齐；不勾选=全部，无全选按钮。
    assert ".filter-type-options,.filter-type-bulk{display:flex;flex-wrap:wrap;gap:8px}" in base
    assert ".filter-type-bulk{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end}" not in base
    assert 'data-type-all' not in html
    # chip 行样式在共享 CSS；默认无筛选时不渲染空行。
    assert ".filter-chips{display:flex" in base
    assert 'class="filter-chips"' not in html
    # 勾选单一类型（部分选择）后出现「已选条件」chip，且清空链接指向去掉该条件的 URL。
    filtered = create_app().test_client().get('/analytics/', query_string={'document_type': 'sale'}).get_data(as_text=True)
    assert 'class="filter-chips"' in filtered
    assert '类型：销售单' in filtered
    # analytics-v4.css 不再重复定义筛选卡（避免两套样式漂移）。
    assert ".erp-analytics .analytics-filters" not in css


def test_bar_growth_animation_is_wired_to_tab_switch_and_keeps_constant_speed():
    """条形统计图竖向生长：切页签 / 就地换图都播，速度恒定（时长=柱高/速度）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    css = (root / "app" / "erp" / "static" / "analytics-v4.css").read_text(encoding="utf-8")
    js = (root / "app" / "erp" / "static" / "analytics-controls.mjs").read_text(encoding="utf-8")

    # 竖向柱：从 0 长到 --bar-rise；柱顶数值同用 --bar-rise/--bar-dur 随柱上升。
    assert "@keyframes bargrow{from{height:0}to{height:var(--bar-rise,0)}}" in css
    assert "@keyframes barlabel{from{transform:translate(-50%,var(--bar-rise,0))}to{transform:translate(-50%,0)}}" in css
    assert ".is-growing .rank-bar-fill{animation:bargrow var(--bar-dur" in css
    assert ".is-growing .rank-bar-value{animation:barlabel var(--bar-dur" in css
    # reduced-motion 关闭生长。
    assert "@media(prefers-reduced-motion:reduce)" in css and ".erp-analytics .is-growing .rank-bar-fill" in css
    # 匀速口径：时长 = 柱高 / 恒定速度；触发点在 JS（就地刷新整块替换后按需重播）。
    assert "export const BAR_GROW_SPEED_PX_S" in js
    assert "heights.map(height => Math.max(0, Number(height) || 0) / speed)" in js
    assert "chart.classList.add('is-growing')" in js
    assert "prefersReducedMotion" in js
    # 触发点一：切页签到 产品排行（点已选中的页签不重播）；成本诊断页签已删除。
    assert "key === 'rank'" in js, "切到 产品排行 要生长"
    assert "wasSelected" in js, "点已选中的页签不应重播"
    # 触发点二：任何就地换图（换榜 / 换每组显示 / 点显示）都重播生长。
    assert "growActivePanel()" in js and "applyViewChange" in js
    assert "activeTabKey" in js, "就地刷新后按当前页签决定播哪块"
    # 后端与模板没有引入生长相关参数：柱高仍是模板内联的 bar_height_px。
    template = (root / "app" / "erp" / "templates" / "analytics" / "index.html").read_text(encoding="utf-8")
    assert 'style="height:{{row.bar_height_px}}px"' in template
    assert 'style="bottom:{{row.bar_height_px}}px"' in template
