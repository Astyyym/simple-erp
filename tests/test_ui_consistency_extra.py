"""Batch E：一致性小项的页面级断言（2026-10-08 补全）。

覆盖计划里标注「E 专项测试未补」的条目：
- E5 拿货详情返回统一为「返回单据管理」→ /orders/
- E8 顶栏状态点接 /health（不再是纯静态）
- E9 四个列表金额带 ¥
- E10 桌面 PDF 预览页适配深色（ui_theme 变量 + dark 分支）
- E11 单据/客户列表补 <thead>
- E12 侧栏版本行按 is_desktop 区分
- E13 表格字号接入 --erp-scale
"""
from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
from erp.services.inventory import create_purchase_order


def _client():
    init_db()
    return create_app().test_client()


# --------------------------- E5 ---------------------------

def test_purchase_detail_returns_to_document_management():
    client = _client()
    cid = create_customer("E5客户")
    pid = create_product("E5商品", "个", 1000)
    # 用草稿拿货单：无需先启用库存；详情页返回按钮与状态无关。
    purchase = create_purchase_order(
        "NH202610010001", "2026-10-01",
        [{"product_id": pid, "quantity": "1", "unit_cost_yuan": "10"}],
        status="draft", request_key="e5-purchase", customer_id=cid,
    )
    html = client.get(f"/purchases/{purchase['order_id']}").get_data(as_text=True)
    assert 'class="btn btn-secondary page-return" href="/orders/"' in html
    assert "返回单据管理" in html


# --------------------------- E8 ---------------------------

def test_topbar_status_point_uses_health_endpoint():
    html = _client().get("/").get_data(as_text=True)
    assert "id=\"localStatus\"" in html
    assert "fetch('/health'" in html
    assert "本地服务异常" in html


# --------------------------- E9 ---------------------------

def test_list_amounts_show_yuan_symbol():
    client = _client()
    cid = create_customer("E9客户")
    create_product("E9商品", "个", 1234)
    create_order_from_typed_rows(
        cid, "MD202610010001",
        [{"product_name": "E9商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-01",
    )
    assert "¥" in client.get("/orders/").get_data(as_text=True)
    assert "¥" in client.get("/products/").get_data(as_text=True)
    assert "¥" in client.get("/customers/").get_data(as_text=True)
    # 回收站金额列同样带 ¥：先软删一条商品让列表非空。
    from erp.db import get_db
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP WHERE name='E9商品'")
        conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP WHERE name='E9客户'")
    recycle_html = client.get("/recycle/").get_data(as_text=True)
    assert "¥" in recycle_html


# --------------------------- E10 ---------------------------

def test_desktop_pdf_preview_page_supports_dark_theme():
    html = _client().get("/settings/print-preview.pdf?desktop_preview=1").get_data(as_text=True)
    assert 'data-ui-theme=' in html
    assert 'data-ui-theme="dark"' in html  # dark 分支规则存在
    assert "--pv-bg" in html
    # 不再硬编码浅色背景作为唯一底色。
    assert "background:var(--pv-bg)" in html


# --------------------------- E11 ---------------------------

def test_orders_and_customers_lists_wrap_header_in_thead():
    client = _client()
    assert "<thead>" in client.get("/orders/").get_data(as_text=True)
    assert "<thead>" in client.get("/customers/").get_data(as_text=True)


# --------------------------- E12 ---------------------------

def test_sidebar_version_row_switches_by_desktop_flag(monkeypatch):
    client = _client()
    browser = client.get("/").get_data(as_text=True)
    assert "浏览器本地访问" in browser
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    desktop = create_app().test_client().get("/").get_data(as_text=True)
    assert "本地桌面版" in desktop


# --------------------------- E13 ---------------------------

def test_table_font_size_scales_with_erp_scale():
    html = _client().get("/").get_data(as_text=True)
    assert ".table th{height:44px" in html
    assert "font-size:calc(12px * var(--erp-scale))" in html
    assert "font-size:calc(14px * var(--erp-scale))" in html


# --------------------------- E4 ---------------------------

def test_truncation_hints_render_on_product_and_customer_lists():
    """商品/客户列表到达 200 上限时给出「已截断，可用搜索缩小范围」提示。"""
    from erp.utils.pinyin import pinyin_initials as build
    from erp.services.accounting import create_product as _cp

    cid = None
    client = _client()
    for i in range(205):
        # 决策变更（v14）：create_product 签名收窄为 (name, unit, default_price_cents,
        # pinyin_initials="", ...)，原「型号」直接并进名称。
        name = f"E4商品{i:03d}S{i:03d}"
        _cp(name, "个", 1000, build(name))
    html = client.get("/products/").get_data(as_text=True)
    # Batch B（B-9）：截断提示强化，并明确「不要据此认为数据丢失」。
    assert "列表已截断" in html and "仅显示前 200 条" in html
    assert "缩小范围" in html and "不要据此认为数据丢失" in html


def test_return_source_candidates_are_capped_and_missing_cost_is_explained():
    """退货来源改为检索式候选后的实现契约（2026-10-13）。

    旧契约是"服务端渲染全量下拉 + 达到 100 张时提示"，现在是：候选按需走
    `/orders/api/return_sources`（上限 `RETURN_SOURCE_LIMIT`），被成本门槛挡掉的张数
    用页面上的 `#returnSourceMissingHint` 说明，而不是把选不了的单塞进候选。
    """
    from pathlib import Path

    from erp.routes.orders import RETURN_SOURCE_LIMIT

    assert RETURN_SOURCE_LIMIT == 30
    tpl = (Path(__file__).resolve().parents[1] / "app" / "erp" / "templates" / "orders" / "new.html").read_text(encoding="utf-8")
    assert "return_sources|length" not in tpl
    assert "{% for source in return_sources %}" not in tpl
    assert "returnSourceMissingHint" in tpl
    assert "/orders/api/return_sources?q=" in tpl


def test_purchase_source_picker_is_searchable_with_a_shared_limit():
    """F4（2026-10-14）：退拿货来源从「服务端渲染最近 200 张的原生下拉」改为检索式候选，
    与退货单同口径（上限 `PURCHASE_SOURCE_LIMIT`，点开列最近 N 张，更多靠输入过滤）。"""
    from pathlib import Path

    from erp.routes.purchases import PURCHASE_SOURCE_LIMIT

    assert PURCHASE_SOURCE_LIMIT == 30
    tpl = (Path(__file__).resolve().parents[1] / "app" / "erp" / "templates" / "purchases" / "return_new.html").read_text(encoding="utf-8")
    assert "purchase_sources|length" not in tpl
    assert "{% for source in purchase_sources %}" not in tpl
    assert "/purchases/api/purchase_sources?q=" in tpl
    assert "purchaseSourceClear" in tpl


def test_dark_theme_return_rows_neutralise_the_bootstrap_stripe_overlay():
    """F2（2026-10-14）：深色下退货单行曾被 Bootstrap 的条纹 inset 阴影盖成浅粉底 + 浅粉字。

    `.table-danger` 的 `--bs-table-striped-bg` 是浅色模式写死的 `#eccccf`，而 Bootstrap 用
    `box-shadow: inset 0 0 0 9999px var(--bs-table-bg-type, …)` 把它盖在 background 之上；
    只覆盖 background/color 不够，必须把这两个变量一起置空（深色与跟随系统各一条）。
    实测修复前 `getComputedStyle(td).boxShadow` 为 `rgb(236, 204, 207) … inset`，修复后为透明。
    """
    from pathlib import Path

    base = (Path(__file__).resolve().parents[1] / "app" / "erp" / "templates" / "base.html").read_text(encoding="utf-8")
    lines = base.splitlines()
    overlay_rules = [line for line in lines if "--bs-table-bg-type:transparent" in line]
    assert len(overlay_rules) == 2, "深色与跟随系统各需要一条覆盖"
    assert all("table-danger" in line and "return-order-row" in line for line in overlay_rules)
    hover_rules = [line for line in lines if "table-danger.return-order-row:hover>*" in line]
    assert len(hover_rules) == 3, "浅色 1 条 + 深色/跟随系统各 1 条，否则悬停又变浅底浅字"
    assert sum('html[data-ui-theme="dark"]' in line for line in hover_rules) == 1
    assert sum('html[data-ui-theme="system"]' in line for line in hover_rules) == 1
