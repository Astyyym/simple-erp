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
    return create_order_from_typed_rows(customer, no, [{"product_id": product, "product_name": "历史商品", "spec": "", "unit": "个", "quantity": quantity, "unit_price_yuan": price}], status="saved", order_date=day)


def test_explicit_empty_dates_route_keeps_open_scope_and_prior_month_sales():
    init_db()
    customer = create_customer("虚构往来")
    product = create_product("历史商品", "", "个", 1000)
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
    product = create_product("历史商品", "", "个", 1000)
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


@pytest.mark.parametrize("bad", [{"start_date": "bad-date"}, {"start_date": "2026-10-03", "end_date": "2026-10-01"}, {"customer_id": "true"}, {"customer_id": "0"}, {"customer_id": "-1"}, {"customer_id": "99999"}, {"metric": "fake"}, {"spec": "fake"}])
def test_invalid_route_scope_retains_inputs_without_fallback_results(bad):
    init_db()
    customer = create_customer("错误筛选对象")
    product = create_product("历史商品", "", "个", 1000)
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


def test_historical_cost_micro_is_aggregated_before_rounding_in_every_projection():
    init_db()
    customer = create_customer("微元历史对象")
    product = create_product("历史商品", "", "个", 100)
    first = legacy_sale(customer, product, "MD202509010001", "2025-09-01", price="1")
    second = legacy_sale(customer, product, "MD202509010002", "2025-09-01", price="1")
    with get_db() as conn:
        conn.execute("UPDATE order_items SET cost_total_micro=5000 WHERE order_id IN (?,?)", (first, second))
    result = summarize_analytics(customer_id=customer)
    row = result["product_rows"][0]
    assert row["net_cost_cents"] == 1
    assert row["net_cost_micro"] == 10000
    assert row["gross_profit_cents"] == 199
    assert result["summary"]["net_cost_cents"] == 1
    assert result["summary"]["gross_profit_cents"] == 199
    assert result["heatmap"][0]["net_cost_micro"] == 10000
    assert result["heatmap"][0]["net_cost_cents"] == 1
    assert result["heatmap"][0]["has_transactions"] is True


def test_partial_historical_cost_coverage_is_explicit_not_zero_or_full_profit():
    init_db()
    customer = create_customer("部分成本对象")
    product = create_product("历史商品", "", "个", 100)
    known = legacy_sale(customer, product, "MD202509010001", "2025-09-01", price="1")
    legacy_sale(customer, product, "MD202509030001", "2025-09-03", price="2")
    with get_db() as conn:
        conn.execute("UPDATE order_items SET cost_total_micro=5000 WHERE order_id=?", (known,))
    result = summarize_analytics(start_date="2025-09-01", end_date="2025-09-03", customer_id=customer)
    summary = result["summary"]
    assert summary["cost_complete"] is False
    assert summary["gross_profit_cents"] is None and summary["gross_margin_rate"] is None
    assert summary["known_line_count"] == 1 and summary["unknown_line_count"] == 1
    assert summary["known_net_sales_cents"] == 100 and summary["unknown_net_sales_cents"] == 200
    assert summary["known_gross_profit_cents"] == 100
    from decimal import Decimal
    assert summary["known_gross_margin_rate"] == Decimal("0.995")
    assert result["product_rows"][0]["sales_line_count"] == 2
    assert result["heatmap"][2]["cost_complete"] is False
    assert result["heatmap"][2]["unknown_net_sales_cents"] == 200
    assert result["heatmap"][1]["has_transactions"] is False


def test_unfilled_model_is_distinct_from_all_models_and_retains_selected_party():
    init_db()
    customer = create_customer("型号与对象留存")
    blank = create_product("历史商品", "", "个", 1000)
    model = create_product("历史商品", "Q1", "个", 1000)
    legacy_sale(customer, blank, "MD202509010001", "2025-09-01")
    create_order_from_typed_rows(customer, "MD202509010002", [{"product_id": model, "product_name": "历史商品", "spec": "Q1", "unit": "个", "quantity": "1", "unit_price_yuan": "20"}], status="saved", order_date="2025-09-01")
    app = create_app()
    with rendered(app) as contexts:
        response = app.test_client().get("/analytics/", query_string={"start_date": "", "end_date": "", "customer_id": customer, "product_name": "历史商品", "spec": "__unfilled__"})
    assert response.status_code == 200
    result = contexts[-1]["result"]
    assert [row["product_id"] for row in result["product_rows"]] == [blank]
    assert result["summary"]["net_sales_amount_cents"] == 1000
    assert str(contexts[-1]["filters"]["customer_id"]) == str(customer)
    html = response.get_data(as_text=True)
    assert f'value="{customer}" selected' in html
    assert 'value="__unfilled__" selected' in html
    assert summarize_analytics(customer_id=customer, product_name="历史商品")["summary"]["net_sales_amount_cents"] == 3000


def sale_for_product(customer, product, no, day, quantity="1", price="10"):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product,)).fetchone()
    return create_order_from_typed_rows(customer, no, [{"product_id": product, "product_name": row["name"], "spec": row["spec"], "unit": row["unit"], "quantity": quantity, "unit_price_yuan": price}], status="saved", order_date=day)


def test_quantity_rankings_keep_one_all_product_chart_and_classify_real_sales():
    """数量榜只按数量排序、全品类同一张榜；不再按单位分组。"""
    from erp.services.inventory import initialize_product
    from erp.services.accounting import create_return_order_from_source
    init_db()
    customer = create_customer("完整排行对象")
    ids = []
    for index in range(13):
        product = create_product(f"排行计件{index:02}", "", "个", 1000)
        initialize_product(product, "100", "0", "2025-01-01", "虚构", f"rank-{index}")
        sale_for_product(customer, product, f"MD20250901{index+1:04}", "2025-09-01", quantity=str(index+1))
        ids.append(product)
    meter = create_product("排行计量", "", "米", 1000)
    sale_for_product(customer, meter, "MD202509010101", "2025-09-01", quantity="100")
    free = create_product("零价实际销售", "", "个", 0)
    sale_for_product(customer, free, "MD202509010102", "2025-09-01", price="0")
    unsold = create_product("期末前未销售", "", "个", 0)
    too_new = create_product("期末后建档", "", "个", 0)
    inactive = create_product("停用未销售", "", "个", 0)
    returned = create_product("全部退回", "", "个", 1000)
    initialize_product(returned, "10", "1", "2025-01-01", "虚构", "rank-return")
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
    from urllib.parse import parse_qs, urlsplit
    from test_readonly_cost_display import RenderedHTML
    init_db()
    customer = create_customer("跨年完整范围对象")
    product = create_product("历史商品", "", "个", 1000)
    legacy_sale(customer, product, "MD202401010001", "2024-01-01")
    legacy_sale(customer, product, "MD202610010001", "2026-10-01")
    app = create_app()
    query = {"start_date": "2024-01-01", "end_date": "2026-10-01", "customer_id": customer, "product_name": "历史商品", "sort": "profit", "tab": "health"}
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
    assert view["tab"] == "health" and view["sort"] == "profit"
    html = response.get_data(as_text=True)
    ids = ["analyticsFilters", "analysisMetrics", "layeredAnalysis", "productAnalysis"]
    assert [html.index(f'id="{element}"') for element in ids] == sorted(html.index(f'id="{element}"') for element in ids)
    assert all(f'id="{element}"' in html for element in ("heatTab", "rankTab", "healthTab", "inventoryAlert"))
    assert "已知部分净成本" in html and "未覆盖净销售额" in html
    assert "历史净成本" in html and "当前库存金额" in html
    # 数据分析页不再提供"进入往来对账"入口（改由账款管理的单据类型筛选承担）
    assert 'id="reconEntry"' not in html and 'id="openRecon"' not in html


def test_ranking_size_selector_replaces_pagination_and_keeps_aggregate_scope():
    from test_product_inventory_status_display import business_snapshot
    init_db()
    customer=create_customer("排行分页对象")
    ids=[]
    for index in range(13):
        pid=create_product("分页商品",str(index),"个",1000)
        sale_for_product(customer,pid,f"MD20250901{index+1:04}","2025-09-01",quantity=str(index+1))
        ids.append(pid)
    app=create_app()
    client=app.test_client()
    before=business_snapshot()
    query={"start_date":"", "end_date":"", "customer_id":customer, "product_name":"分页商品", "tab":"rank", "sort":"profit", "rank_key":"hot_sales"}
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
        assert context['view']['sort']=='profit' and context['view']['tab']=='rank'
    # 非法的每组条数被拒绝
    assert client.get('/analytics/',query_string={**query,'rank_size':'7'}).status_code==400
    assert business_snapshot()==before


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
    assert all(value in html for value in ('bad-date','99999','未知商品','错误型号'))
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
