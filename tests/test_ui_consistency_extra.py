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
from erp.services.purchases import create_purchase_order


def _client():
    init_db()
    return create_app().test_client()


# --------------------------- E5 ---------------------------

def test_purchase_detail_returns_to_document_management():
    client = _client()
    cid = create_customer("E5客户")
    pid = create_product("E5商品", "个")
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
    create_product("E9商品", "个")
    create_order_from_typed_rows(
        cid, "MD202610010001",
        [{"product_name": "E9商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-01",
    )
    assert "¥" in client.get("/orders/").get_data(as_text=True)
    assert "¥" in client.get("/customers/").get_data(as_text=True)
    # 决策变更（2026-10-15）：商品列表不再有价格列（售价不是档案字段）→ 该页已无 ¥ 金额，
    # 这条断言随之删除；商品侧的金额（移动平均成本 / 库存金额）另由库存相关用例覆盖。
    # 回收站金额列同样带 ¥：软删商品/客户让列表非空，再软删一张单据让单据金额列非空。
    from erp.db import get_db
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP WHERE name='E9商品'")
        conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP WHERE name='E9客户'")
        conn.execute("UPDATE orders SET deleted_at=CURRENT_TIMESTAMP WHERE deleted_at IS NULL")
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
        # 决策变更（2026-10-15）：create_product 签名收窄为 (name, unit, pinyin_initials="", ...)
        # —— 默认价下线，第 3 个位置参数改成拼音首字母；「型号」早已并进名称。
        name = f"E4商品{i:03d}S{i:03d}"
        _cp(name, "个", build(name))
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


def test_return_rows_no_longer_paint_a_striped_or_tinted_background():
    """2026-10-24 用户要求：**列表不要用深浅颜色交替显示**。

    历史背景（F2，2026-10-14）：退货单整行曾用 `.table-danger` 铺淡红底，深色模式下
    还被 Bootstrap 的条纹 inset 阴影盖成浅粉底 + 浅粉字，当时用两条「置空
    `--bs-table-bg-type` / `--bs-table-accent-bg`」的规则修过。

    现在方向改由「类型标签 + 金额颜色」表达（与手机端单据列表同一形态）：
    - 行底一律是卡片白，斑马纹全局关闭（`--bs-table-accent-bg:transparent`）；
    - `.return-order-row` 只作为语义钩子保留，**不再有任何背景色规则**。
    所以这里断言的是「旧的整行着色与深色覆盖规则都必须消失」，
    而不是继续守着那两条补丁 —— 补丁的对象已经不存在了。
    """
    from pathlib import Path

    base = (Path(__file__).resolve().parents[1] / "app" / "erp" / "templates" / "base.html").read_text(encoding="utf-8")
    # 断言要带 `{`：注释里解释「为什么删掉整行淡红底」时也会出现这个选择器，
    # 只查裸类名会被注释满足，变成假通过（本项目已经踩过一次同类坑）。
    assert "table-danger.return-order-row>*{" not in base, "整行淡红底已按用户要求删除"
    assert "table-danger.return-order-row:hover>*{" not in base
    # 斑马纹必须被显式关掉：只删模板里的 table-striped 类不够，
    # 回收站/拿货明细等页仍在用该类，全局关掉才不会再出现深浅交替。
    assert ".table-striped>tbody>tr:nth-of-type(odd)>*{--bs-table-accent-bg:transparent}" in base
    # 方向必须靠这两组钩子上色（手机端同款语义）。
    for hook in (
        ".order-type-tag--sale",
        ".order-type-tag--return",
        ".order-type-tag--purchase",
        ".order-type-tag--purchase_return",
        ".order-amount--sale",
        ".order-amount--return",
        ".order-amount--purchase",
        ".order-amount--purchase_return",
    ):
        assert hook in base, hook
