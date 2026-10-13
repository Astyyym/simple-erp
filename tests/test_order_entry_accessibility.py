import re
from pathlib import Path

from erp import create_app
from erp.db import init_db

ROOT = Path(__file__).resolve().parents[1]
PICKER_JS = ROOT / "app" / "erp" / "static" / "order-product-picker.js"
COMMON_JS = ROOT / "app" / "erp" / "static" / "order-entry-common.js"


def _tag_with_attribute(markup: str, tag: str, attribute: str, value: str) -> str:
    pattern = rf"<{tag}\b(?=[^>]*\b{re.escape(attribute)}=[\"']{re.escape(value)}[\"'])[^>]*>"
    match = re.search(pattern, markup)
    assert match is not None, f"missing <{tag}> with {attribute}={value!r}"
    return match.group(0)


def test_order_entry_exposes_accessible_customer_combobox_and_product_picker():
    init_db()
    response = create_app().test_client().get("/orders/new")
    assert response.status_code == 200
    page = response.get_data(as_text=True)

    customer_input = _tag_with_attribute(page, "input", "id", "customer_name")
    assert 'role="combobox"' in customer_input
    assert 'aria-autocomplete="list"' in customer_input
    assert 'aria-controls="customer_suggestions"' in customer_input
    assert 'aria-expanded="false"' in customer_input
    customer_list = _tag_with_attribute(page, "div", "id", "customer_suggestions")
    assert 'role="listbox"' in customer_list

    # 决策变更（v14）：商品身份由「名称 + 型号」合并为单一名称，型号框与型号 datalist
    # 已删除，商品控件只剩一个可手输的名称框（datalist 建议 + 命中绑定/未命中自动建档）。
    assert 'class="form-control form-control-sm picker-name-input"' in page
    assert 'name="product_name"' in page and 'class="product-name"' in page
    assert 'id="productCatalog"' in page
    # 决策变更（Phase 3 S2）：商品名建议由原生 datalist 改为自有 popover
    #（datalist 在 WebView2 不渲染弹层，且不支持拼音）。
    assert "<datalist" not in page
    assert 'list="productNameOptions"' not in page
    assert 'class="suggest-list d-none product-suggestions"' in page
    assert "/orders/api/products?q=" in page
    assert "order-product-picker.js" in page
    assert "mountHybrid" in page

    # 决策变更（Phase 3 S1）：键盘交互与 ARIA 由 order-entry-common.js 统一提供。
    common_js = COMMON_JS.read_text(encoding="utf-8")
    assert "order-entry-common.js" in page
    assert "ArrowDown" in common_js and "ArrowUp" in common_js and "Escape" in common_js
    assert "aria-activedescendant" in common_js
    assert 'aria-autocomplete' in common_js and "listbox" in common_js
    # 决策变更（v14）：型号框 / 型号建议源 / name="spec" 均已删除。
    assert "picker-spec-input" not in page
    assert 'class="product-spec"' not in page
    assert 'name="spec"' not in page
    assert 'id="productSpecOptions"' not in page


def test_order_entry_outside_click_can_reset_combobox_expanded_state():
    init_db()
    page = create_app().test_client().get("/orders/new").get_data(as_text=True)
    common_js = COMMON_JS.read_text(encoding="utf-8")

    # 决策变更（Phase 3 S1）：控制器定义已统一到共享文件；页内保留点外关闭的调用点。
    assert "order-entry-common.js" in page
    assert "input._closeSuggestions = close" in common_js
    assert "input._closeSuggestions?.()" in page


def test_order_entry_closing_suggestions_invalidates_pending_fetches():
    init_db()
    page = create_app().test_client().get("/orders/new").get_data(as_text=True)
    common_js = COMMON_JS.read_text(encoding="utf-8")

    # 决策变更（Phase 3 S1）：close() 已随控制器统一到共享文件。
    assert "order-entry-common.js" in page
    close_match = re.search(r"const close = \(\) => \{(?P<body>.*?)\n\s*\};", common_js, re.DOTALL)
    assert close_match is not None
    assert "requestSequence += 1;" in close_match.group("body")


def test_order_autocomplete_renders_untrusted_values_as_text_not_html():
    init_db()
    page = create_app().test_client().get("/orders/new").get_data(as_text=True)
    common_js = COMMON_JS.read_text(encoding="utf-8")

    # 决策变更（Phase 3 S1）：showSuggestions 的选项构造已移到共享文件。
    assert "option.appendChild(render(item));" in common_js
    assert "option.innerHTML = render(item)" not in common_js
    assert "nameNode.textContent = c.name;" in page
    assert "detailsNode.textContent = c.phone || '';" in page
    # 商品名由单框 + 自有联想呈现：目录 JSON 只喂数据，选项文本用 textContent，不拼接 HTML
    picker = PICKER_JS.read_text(encoding="utf-8")
    assert "o.textContent = text;" in picker
    assert "innerHTML" not in picker


def test_customer_product_and_order_suggestions_render_names_as_text():
    """三张列表页联想改自有 popover（Phase 3 S6）后，仍以 textContent 渲染名称。

    决策变更（Phase 3 S6）：原生 datalist 已移除，选项由共享控制器的 `render` 回调
    用 `document.createElement` + `textContent` 构造，绝不把库内名称当 HTML 解析。
    """
    init_db()
    client = create_app().test_client()
    customer_page = client.get("/customers/").get_data(as_text=True)
    product_page = client.get("/products/").get_data(as_text=True)
    order_page = client.get("/orders/").get_data(as_text=True)

    for page in (customer_page, product_page, order_page):
        assert "bindAutocomplete" in page
        assert "textContent" in page
        assert "innerHTML = name" not in page
        assert "innerHTML = row" not in page
