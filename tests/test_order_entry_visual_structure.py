"""Shared sales/return entry surfaces and sidebar grouping contracts."""
from html.parser import HTMLParser

from erp import create_app


class OrderCardContents(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.ids = set()
        self.names = set()
        self.text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "section" and attributes.get("id") == "orderDetailsCard":
            self.depth = 1
        elif self.depth and tag == "section":
            self.depth += 1
        if self.depth:
            self.ids.add(attributes.get("id"))
            self.names.add(attributes.get("name"))

    def handle_endtag(self, tag):
        if self.depth and tag == "section":
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)


def test_all_pages_share_one_sidebar_width():
    """侧栏宽度全局一致：4 个开单页不再单独收窄。

    历史：开单页曾为给右侧 PDF 预览腾宽度被收窄到 200px，导致点开单入口与点
    其他入口时侧栏宽度不一致。现统一回默认 224px，且不得再出现页面级覆盖。
    """
    client = create_app().test_client()
    paths = ("/orders/new", "/orders/return/new", "/purchases/new", "/purchases/return/new",
             "/orders/", "/accounts/", "/analytics/", "/recycle/", "/products/", "/customers/")
    for path in paths:
        html = client.get(path).get_data(as_text=True)
        # 默认侧栏宽度声明（flex + width 都用 224px）
        assert "flex:0 0 calc(224px * var(--erp-scale));width:calc(224px * var(--erp-scale))" in html, path
        # 不得存在任何按页面收窄侧栏的覆盖规则
        assert ".order-entry-page .sidebar{" not in html, path


def test_sales_and_return_details_share_card_excluding_success_actions():
    client = create_app().test_client()
    for path, title in (("/orders/new", "订单信息"), ("/orders/return/new", "退货信息")):
        response = client.get(path)
        assert response.status_code == 200
        parser = OrderCardContents()
        parser.feed(response.get_data(as_text=True))
        assert "orderTable" in parser.ids
        assert "notes" in parser.names
        assert title in "".join(parser.text)
        assert "saveSuccessPanel" not in parser.ids
        assert "todayHistoryRows" not in parser.ids


def test_sidebar_has_named_groups_without_changing_routes():
    html = create_app().test_client().get("/").get_data(as_text=True)
    for label in ("工作台", "基础资料", "回收站", "系统设置"):
        assert f'class="nav-group" aria-label="{label}"' in html
    assert html.count('class="nav-divider"') == 3
    assert 'href="/">工作台</a>' in html
    assert 'href="/settings/">系统设置</a>' in html


def test_light_sidebar_uses_soft_scrollbar_tokens_and_vertical_scrollport():
    html = create_app().test_client().get("/").get_data(as_text=True)

    assert '--erp-sidebar-glass:#f1f3f6' in html
    assert '--erp-sidebar-scroll-thumb:rgba(71,84,103,.28)' in html
    assert 'overflow-x:hidden;overflow-y:auto' in html
    assert '.sidebar::-webkit-scrollbar-thumb{min-height:34px;background:var(--erp-sidebar-scroll-thumb);border:2px solid transparent;border-radius:999px;background-clip:padding-box}' in html


def test_workbench_layout_fills_the_content_area_without_a_width_cap():
    """工作台主区域铺满内容区；1360px 上限会让 2560 窗口左右各空约 480px。"""
    html = create_app().test_client().get("/").get_data(as_text=True)

    assert ".dashboard-layout{width:100%;margin:0 auto}" in html
    assert "max-width:1360px" not in html


def test_order_entry_product_picker_shows_name_and_spec_side_by_side():
    """四类开单共用一处 .product-picker 样式：名称与型号必须并排，不能上下堆叠。

    flex 必须挂在内层 .picker-row 上，不能挂在 <td> 上：td 变成 flex 容器后
    会丢掉 table-cell 身份，高度塌成内容高并贴顶对齐（vertical-align 失效）。
    """
    base = create_app().test_client().get("/orders/new").get_data(as_text=True)
    assert ".product-picker{padding:4px}" in base
    assert ".product-picker .picker-row{display:flex;flex-direction:row" in base
    assert ".product-picker{display:flex" not in base
    assert ".product-picker{display:flex;flex-direction:column" not in base
    assert ".product-picker .picker-name{flex:1 1 58%" in base
    assert ".product-picker .picker-spec{flex:1 1 42%" in base
    for path, table in (("/orders/new", "orderTable"), ("/orders/return/new", "orderTable"),
                        ("/purchases/new", "purchaseTable"), ("/purchases/return/new", "returnTable")):
        page = create_app().test_client().get(path).get_data(as_text=True)
        assert 'id="%s"' % table in page, path
        assert 'min-width:340px">产品名称</th>' in page, path
        # 单元格里必须有内层 flex 容器，否则 td 会自己变成 flex。
        assert '<td class="product-picker"><div class="picker-row">' in page, path
