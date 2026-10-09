import re
from pathlib import Path

from erp import create_app
from erp.db import init_db

PICKER_JS = Path(__file__).resolve().parents[1] / "app" / "erp" / "static" / "order-product-picker.js"


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

    # G 段二：商品改为「名称 + 型号」可手输的混合控件（datalist 建议 + 命中绑定/未命中自动建档）。
    assert 'class="form-control form-control-sm picker-name-input"' in page
    assert 'class="form-control form-control-sm picker-spec-input"' in page
    assert 'name="product_name"' in page and 'class="product-name"' in page
    assert 'name="spec"' in page and 'class="product-spec"' in page
    assert 'id="productCatalog"' in page
    assert 'id="productNameOptions"' in page and 'id="productSpecOptions"' in page
    assert 'order-product-picker.js' in page
    assert "mountHybrid" in page

    assert "ArrowDown" in page
    assert "ArrowUp" in page
    assert "Escape" in page
    assert "aria-activedescendant" in page


def test_order_entry_outside_click_can_reset_combobox_expanded_state():
    init_db()
    page = create_app().test_client().get("/orders/new").get_data(as_text=True)

    assert "input._closeSuggestions = close" in page
    assert "input._closeSuggestions?.()" in page


def test_order_entry_closing_suggestions_invalidates_pending_fetches():
    init_db()
    page = create_app().test_client().get("/orders/new").get_data(as_text=True)

    close_match = re.search(r"const close = \(\) => \{(?P<body>.*?)\n  \};", page, re.DOTALL)
    assert close_match is not None
    assert "requestSequence += 1;" in close_match.group("body")


def test_order_autocomplete_renders_untrusted_values_as_text_not_html():
    init_db()
    page = create_app().test_client().get("/orders/new").get_data(as_text=True)

    assert "option.appendChild(render(item));" in page
    assert "option.innerHTML = render(item)" not in page
    assert "nameNode.textContent = c.name;" in page
    assert "detailsNode.textContent = c.phone || '';" in page
    # 商品名/型号改由两级原生下拉呈现：目录 JSON 只喂数据，选项文本用 textContent，不拼接 HTML
    picker = PICKER_JS.read_text(encoding="utf-8")
    assert "o.textContent = text;" in picker
    assert "innerHTML" not in picker


def test_customer_product_and_order_suggestions_do_not_parse_stored_names_as_html():
    init_db()
    client = create_app().test_client()
    customer_page = client.get("/customers/").get_data(as_text=True)
    product_page = client.get("/products/").get_data(as_text=True)
    order_page = client.get("/orders/").get_data(as_text=True)

    assert "option.value = name;" in customer_page
    assert "names.map(name => `<option" not in customer_page
    assert "option.value = name;" in product_page
    assert "names.map(name => `<option" not in product_page
    assert "option.value = row.name;" in order_page
    assert "rows.map(row => `<option" not in order_page
