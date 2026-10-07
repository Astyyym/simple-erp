from html.parser import HTMLParser

from erp import create_app
from erp.db import get_db
from erp.services.accounting import create_product
from erp.services.inventory import initialize_product


class RenderedHTML(HTMLParser):
    """Read visible table cells and form controls from real route responses."""

    def __init__(self, html):
        super().__init__()
        self.rows, self.controls, self.links = [], [], []
        self.row, self.cell = None, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row = []
        elif tag in ("th", "td") and self.row is not None:
            self.cell = []
        elif tag in ("input", "select", "textarea", "output"):
            self.controls.append((tag, attrs))
        elif tag == "a":
            self.links.append(attrs)

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def test_product_edit_has_prominent_computed_cost_and_top_return_without_editable_average():
    app = create_app()
    product_id = create_product("只读计量商品", "M1", "米", 1000, safety_stock="1.250")
    initialize_product(product_id, "12.345", "11.333333", "2026-10-01", "测试期初", "readonly-edit-01")
    response = app.test_client().get(f"/products/{product_id}/edit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parsed = RenderedHTML(html)
    summary_start = html.index('aria-label="当前库存与计算成本"')
    assert summary_start < html.index('name="name"')
    assert 'id="currentAverageCost"' in html
    assert "由库存流水计算，只读" in html
    assert html.index('class="page-header"') < summary_start
    returns = [link for link in parsed.links if link.get("href") == "/products/" and "btn" in link.get("class", "").split()]
    assert len(returns) == 1
    assert "page-return" in returns[0]["class"]
    controls = {attrs.get("name"): (tag, attrs) for tag, attrs in parsed.controls if attrs.get("name")}
    assert not ({"avg_cost", "avg_cost_micro", "current_avg_cost_micro", "cost_total_micro", "average_cost"} & controls.keys())
    assert controls["safety_stock"][1]["value"] == "1.25"
    # Cost inputs remain source inputs in the original opening/revision forms.
    assert controls["unit_cost"][0] == "input"
    assert f'action="/products/{product_id}/image"' in html
    assert f'action="/products/{product_id}/inventory/revise"' in html
    assert 'name="expected_version"' in html
    assert "12.345" in html


def test_analytics_has_separate_current_average_cost_with_zero_and_missing_distinct():
    app = create_app()
    zero_id = create_product("分析零成本商品", "Z1", "个", 1000)
    missing_id = create_product("分析未启用商品", "U1", "个", 1000)
    empty_id = create_product("分析零库存商品", "E1", "米", 1000)
    initialize_product(zero_id, "5", "0", "2026-10-01", "测试", "readonly-zero-cost")
    initialize_product(empty_id, "0", "", "2026-10-01", "测试", "readonly-empty-cost", confirm_zero=True)
    response = app.test_client().get("/analytics/", query_string={"start_date": "2026-10-01", "end_date": "2026-10-02"})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parsed = RenderedHTML(html)
    header = next(row for row in parsed.rows if "商品" in row)
    assert header == ["商品", "型号", "净销售数量", "净销售额", "历史净成本", "毛利润", "毛利率", "当前库存", "当前移动平均成本", "当前库存金额", "启用状态", "库存状态"]
    rows = {row[0]: dict(zip(header, row)) for row in parsed.rows if row and row[0] in {"分析零成本商品", "分析未启用商品", "分析零库存商品"}}
    assert rows["分析零成本商品"]["当前移动平均成本"] == "0.00"
    assert rows["分析零成本商品"]["当前库存"] == "5 个"
    assert rows["分析零成本商品"]["库存状态"] == "正常"
    assert rows["分析未启用商品"]["当前移动平均成本"] == "—"
    assert rows["分析未启用商品"]["当前库存"] == "—"
    assert rows["分析未启用商品"]["库存状态"] == "未启用"
    assert rows["分析零库存商品"]["当前移动平均成本"] == "无库存"
    assert rows["分析零库存商品"]["当前库存"] == "0 米"
    assert rows["分析零库存商品"]["库存状态"] == "缺货"
    assert 'id="layeredAnalysis"' in html
    assert 'id="heatTab"' in html and 'id="rankTab"' in html and 'id="healthTab"' in html
    assert rows["分析未启用商品"]["启用状态"] == "未启用"
    assert rows["分析零库存商品"]["启用状态"] == "已启用"
    assert 'id="heatmapTitle"' in html
    assert 'id="profitTitle"' in html


def test_product_cost_cells_and_readonly_summary_display_two_decimals_without_changing_storage():
    import re

    app = create_app()
    product_id = create_product("两位成本商品", "C1", "个", 1000)
    zero_id = create_product("商品零成本", "Z1", "个", 1000)
    missing_id = create_product("商品未启用", "U1", "个", 1000)
    initialize_product(product_id, "15", "11.333333", "2026-10-01", "测试", "readonly-precision-cost")
    initialize_product(zero_id, "1", "0", "2026-10-01", "测试", "readonly-product-zero")
    with get_db() as conn:
        before = tuple(conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone())
    html = app.test_client().get("/products/").get_data(as_text=True)
    parsed = RenderedHTML(html)
    header = next(row for row in parsed.rows if "名称" in row)
    rows = {row[1]: dict(zip(header, row)) for row in parsed.rows if len(row) > 1 and row[1] in {"两位成本商品", "商品零成本", "商品未启用"}}
    assert rows["两位成本商品"]["移动平均成本"] == "11.33"
    assert rows["两位成本商品"]["库存金额"] == "170.00"
    assert rows["商品零成本"]["移动平均成本"] == "0.00"
    assert rows["商品零成本"]["库存金额"] == "0.00"
    assert rows["商品未启用"]["移动平均成本"] == "—"
    assert rows["商品未启用"]["库存金额"] == "—"
    assert rows["商品未启用"]["当前库存"] == "—"
    edit_html = app.test_client().get(f"/products/{product_id}/edit").get_data(as_text=True)
    assert re.search(r'<dd id="currentAverageCost"[^>]*>11\.33</dd>', edit_html)
    assert "11.333333" not in edit_html
    with get_db() as conn:
        after = tuple(conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone())
    assert before == after


def test_display_routes_leave_inventory_postings_accounting_and_analytics_queries_unchanged():
    from erp.services.accounting import create_customer, create_order_from_typed_rows
    from erp.services.analytics import summarize_analytics

    app = create_app()
    product_id = create_product("只读显示回归", "R1", "个", 2000)
    initialize_product(product_id, "12.345", "3.333333", "2026-10-01", "测试", "readonly-query-regression")
    customer_id = create_customer("只读显示往来")
    create_order_from_typed_rows(customer_id, "MD202610010902", [{"product_id": product_id, "product_name": "只读显示回归", "spec": "R1", "unit": "个", "unit_price_yuan": "20", "quantity": "1.125"}], status="saved", order_date="2026-10-01")
    tables = ("products", "customers", "product_inventory_state", "inventory_initializations", "inventory_postings", "purchase_orders", "purchase_order_items", "orders", "order_items", "payments", "adjustments", "customer_prices")

    def snapshot():
        with get_db() as conn:
            return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()] for table in tables}

    before = snapshot()
    query = {"start_date": "2026-10-01", "end_date": "2026-10-02", "product_name": "只读显示回归", "customer_id": customer_id}
    summary_before = summarize_analytics(**query)
    client = app.test_client()
    assert client.get("/products/", query_string={"q": "只读显示"}).status_code == 200
    assert client.get(f"/products/{product_id}/edit").status_code == 200
    assert client.get("/analytics/", query_string=query).status_code == 200
    assert snapshot() == before
    assert summarize_analytics(**query) == summary_before
