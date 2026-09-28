import re

from erp import create_app
from erp.db import init_db


def _tag_with_attribute(markup: str, tag: str, attribute: str, value: str) -> str:
    pattern = rf"<{tag}\b(?=[^>]*\b{re.escape(attribute)}=[\"']{re.escape(value)}[\"'])[^>]*>"
    match = re.search(pattern, markup)
    assert match is not None, f"missing <{tag}> with {attribute}={value!r}"
    return match.group(0)


def test_order_entry_autocomplete_exposes_keyboard_accessible_comboboxes():
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

    product_match = re.search(
        r'<input\b(?=[^>]*\bclass=["\'][^"\']*\bproduct-name\b[^"\']*["\'])[^>]*>',
        page,
    )
    assert product_match is not None, "missing product-name input"
    product_input = product_match.group(0)
    assert 'role="combobox"' in product_input
    assert 'aria-autocomplete="list"' in product_input
    assert 'aria-controls="product_suggestions_1"' in product_input
    product_list = _tag_with_attribute(page, "div", "id", "product_suggestions_1")
    assert 'role="listbox"' in product_list

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
    assert "nameNode.textContent = p.name;" in page
    assert "detailsNode.textContent = details;" in page


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
