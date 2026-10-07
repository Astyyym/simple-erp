"""Rendered UI contracts; all runtime data uses conftest's isolated database."""

from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from flask import render_template

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import create_purchase_order
from erp.services.reconciliation import create_reconciliation_snapshot


class Elements(HTMLParser):
    """Small DOM reader using only the standard library."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parent": self.stack[-1] if self.stack else None}
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def by_class(self, name):
        return [node for node in self.nodes if name in node["attrs"].get("class", "").split()]

    def by_id(self, name):
        return next(node for node in self.nodes if node["attrs"].get("id") == name)


def _css_rule(html, selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", html)
    assert match, f"missing shared sizing rule: {selector}"
    return re.sub(r"\s+", "", match.group(1))


@pytest.fixture
def pages():
    init_db()
    customer_id = create_customer("页面动作测试客户")
    order_id = create_order_from_typed_rows(
        customer_id, "MD202610020901",
        [{"product_name": "页面测试商品", "unit": "个", "quantity": "1", "unit_price_yuan": "20"}],
        status="saved", order_date="2026-10-02",
    )
    product_id = create_product("页面草稿商品", "测试", "个", 2000)
    purchase = create_purchase_order(
        "NH202610020901", "2026-10-02",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        status="draft", request_key="page-actions-purchase",
        customer_id=customer_id,
    )
    return create_app().test_client(), customer_id, order_id, purchase["order_id"]


@pytest.mark.parametrize("page, return_href", [
    ("order", "/orders/"),
    ("purchase", "/purchases/new"),
    ("customer", "/customers/"),
    ("prices", "/customers/"),
    ("reconciliation", "/analytics/"),
])
def test_secondary_return_is_last_title_action(pages, page, return_href):
    client, customer_id, order_id, purchase_id = pages
    paths = {
        "order": f"/orders/{order_id}", "purchase": f"/purchases/{purchase_id}",
        "customer": f"/customers/{customer_id}/edit", "prices": f"/customers/{customer_id}/prices",
        "reconciliation": "/analytics/reconciliation",
    }
    if page == "prices":
        # This retained template currently has no registered HTTP route.
        with client.application.test_request_context(paths[page]):
            html = render_template("customers/prices.html", customer={"name": "测试客户"}, products=[])
    else:
        response = client.get(paths[page])
        assert response.status_code == 200
        html = response.get_data(as_text=True)
    dom = Elements(html)
    returns = dom.by_class("page-return")
    assert len(returns) == 1, "secondary page must expose one shared return action"
    return_link = returns[0]
    assert return_link["tag"] == "a"
    assert return_link["attrs"]["href"] == return_href
    actions = return_link["parent"]
    assert "page-actions" in actions["attrs"].get("class", "").split()
    assert "page-header" in actions["parent"]["attrs"].get("class", "").split()
    children = [node for node in dom.nodes if node["parent"] is actions]
    assert children[-1] is return_link, "return must remain the rightmost title action"
    if page == "purchase":
        assert ">返回拿货</a>" in html


def test_shared_header_actions_keep_return_size_and_no_wrapping(pages):
    client, _, order_id, _ = pages
    html = client.get(f"/orders/{order_id}").get_data(as_text=True)
    actions = _css_rule(html, ".page-actions")
    assert "flex-wrap:wrap" in actions
    assert "margin-left:auto" in actions
    button = _css_rule(html, ".page-actions .btn")
    assert "white-space:nowrap" in button
    assert "flex-shrink:0" in button
    assert "min-width:96px" in button
    returning = _css_rule(html, ".page-actions .page-return")
    assert "order:99" in returning
    assert "margin-left:auto" in returning


@pytest.mark.parametrize("path", ["/", "/orders/", "/customers/", "/products/", "/accounts/", "/analytics/"])
def test_top_level_modules_do_not_gain_return_actions(pages, path):
    response = pages[0].get(path)
    assert response.status_code == 200
    assert not Elements(response.get_data(as_text=True)).by_class("page-return")


def test_order_detail_has_no_void_controls_and_keeps_header_actions(pages):
    """作废 UI 已内化为列表删除的自动冲回；回看页不再有原因输入框与作废按钮。"""
    client, _, order_id, _ = pages
    html = client.get(f"/orders/{order_id}").get_data(as_text=True)
    dom = Elements(html)
    assert not [node for node in dom.nodes if node["tag"] == "form" and node["attrs"].get("action") == f"/orders/{order_id}/void"]
    assert "作废原因" not in html
    assert "order-void-row" not in html
    assert "page-danger-action" not in html
    assert any(node["attrs"].get("href") == f"/orders/{order_id}/edit" for node in dom.nodes)
    assert any(node["attrs"].get("href") == f"/orders/{order_id}/pdf" for node in dom.nodes)
    # 标题行动作顺序与不换行排布保持既有契约。
    header = next(node for node in dom.nodes if node["tag"] == "header" and "page-header" in node["attrs"].get("class", "").split())
    actions = next(node for node in dom.nodes if node["parent"] is header and "page-actions" in node["attrs"].get("class", "").split())
    buttons = [node for node in dom.nodes if node["parent"] is actions and node["tag"] == "a"]
    assert [button["attrs"]["href"] for button in buttons] == [f"/orders/{order_id}/edit", f"/orders/{order_id}/pdf", "/orders/"]
    assert "flex-wrap:nowrap" in _css_rule(html, ".order-detail-header .page-actions")
    # 标题行动作仍保持既有排布契约。
    assert "white-space:nowrap" in _css_rule(html, ".page-actions .btn")
    assert "min-width:96px" in _css_rule(html, ".page-actions .btn")


@pytest.mark.parametrize("return_to, expected", [
    ("/orders/?page=2&customer_name=测试客户", "/orders/?page=2&customer_name=测试客户"),
    ("https://example.com/", "/"),
    ("//example.com/orders/", "/"),
])
def test_desktop_pdf_save_and_safe_return_share_sizing_and_rightmost_group(pages, return_to, expected):
    response = pages[0].get("/settings/print-preview.pdf?" + urlencode({"desktop_preview": "1", "return_to": return_to}))
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    dom = Elements(html)
    save = dom.by_id("savePdf")
    returning = dom.by_id("returnToErp")
    assert save["parent"] is returning["parent"], "save and return must share one action group"
    assert "page-actions" in save["parent"]["attrs"].get("class", "").split()
    assert "btn" in save["attrs"]["class"].split()
    assert "btn" in returning["attrs"]["class"].split()
    assert "page-return" in returning["attrs"]["class"].split()
    assert returning["attrs"]["href"] == expected
    children = [node for node in dom.nodes if node["parent"] is save["parent"]]
    assert children[-1] is returning
    rule = _css_rule(html, ".page-actions .btn")
    for declaration in ("width:112px", "min-height:40px", "white-space:nowrap", "flex-shrink:0"):
        assert declaration in rule
    assert "background:#0b6b6b" in _css_rule(html, ".pdf-save")
    assert "background:#475467" in _css_rule(html, ".page-return")
    assert "window.pywebview.api.save_pdf" in html


@pytest.mark.parametrize("desktop", [False, True])
def test_reconciliation_pdf_preserves_browser_mode_and_exact_desktop_source(pages, monkeypatch, desktop):
    client, customer_id, _, _ = pages
    if desktop:
        monkeypatch.setenv("ERP_DESKTOP", "1")
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-02", "2026-10-02", "all")
    source = "/analytics/reconciliation?" + urlencode({"snapshot_id": snapshot_id, "page": 1, "source": "中文 & + /"})
    response = client.get(source)
    assert response.status_code == 200
    dom = Elements(response.get_data(as_text=True))
    links = [node for node in dom.nodes if node["tag"] == "a" and node["attrs"].get("href", "").startswith(f"/analytics/reconciliation/pdf/{snapshot_id}")]
    assert len(links) == 1
    pdf_href = links[0]["attrs"]["href"]
    query = parse_qs(urlsplit(pdf_href).query)
    if desktop:
        assert query.get("desktop_preview") == ["1"]
        assert query.get("return_to") == [source], "preserve the exact encoded internal source, including snapshot and page"
        assert "target" not in links[0]["attrs"]
        preview = client.get(pdf_href)
        assert preview.status_code == 200 and preview.mimetype == "text/html"
        preview_dom = Elements(preview.get_data(as_text=True))
        returning = preview_dom.by_id("returnToErp")
        assert returning["attrs"]["href"] == source
        assert client.get(returning["attrs"]["href"]).status_code == 200
        frame = next(node for node in preview_dom.nodes if node["tag"] == "iframe")
        raw_pdf = client.get(frame["attrs"]["src"])
        assert raw_pdf.mimetype == "application/pdf" and raw_pdf.data.startswith(b"%PDF")
    else:
        assert not query
        pdf_response = client.get(pdf_href)
        assert pdf_response.status_code == 200 and pdf_response.mimetype == "application/pdf"
        assert pdf_response.data.startswith(b"%PDF")


@pytest.mark.parametrize('is_return', [False, True])
def test_purchase_detail_has_no_void_row_and_recycle_entry_is_always_available(is_return):
    """拿货/退拿货详情的作废行已删除；「移入回收站」对正式单据也直接可用（内部自动冲回）。"""
    from tests.test_purchase_printing import seed
    _, _, purchase_id, return_id, _ = seed()
    path = f'/purchases/return/{return_id}' if is_return else f'/purchases/{purchase_id}'
    html = create_app().test_client().get(path).get_data(as_text=True)
    dom = Elements(html)
    header = next(n for n in dom.nodes if n['tag'] == 'header' and 'page-header' in n['attrs'].get('class', '').split())
    assert 'order-detail-header' in header['attrs']['class'].split()
    actions = next(n for n in dom.nodes if n['parent'] is header and 'page-actions' in n['attrs'].get('class', '').split())
    links = [n for n in dom.nodes if n['parent'] is actions and n['tag'] == 'a']
    assert links[-2]['attrs']['href'] == path + '/pdf'
    assert 'page-return' in links[-1]['attrs']['class'].split()
    # 作废 UI 已整体移除（无表单、无原因输入框、无 order-void-row 容器）。
    assert not [n for n in dom.nodes if n['tag'] == 'form' and n['attrs'].get('action') == path + '/void']
    assert 'order-void-row' not in html
    assert 'page-danger-action' not in html
    assert '作废原因' not in html
    # 正式单据（seed 造出的是 saved）也能直接移入回收站。
    recycle = next(n for n in dom.nodes if n['tag'] == 'form' and n['attrs'].get('action') == path + '/delete')
    assert recycle['attrs']['method'] == 'post'
    assert 'confirm(' in recycle['attrs']['onsubmit']
    if is_return:
        assert any(n['attrs'].get('name') == 'version' and n['attrs'].get('value') == '0'
                   for n in dom.nodes if n['parent'] is recycle and n['tag'] == 'input')
