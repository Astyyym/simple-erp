import uuid

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product


def test_return_order_page_offers_both_save_and_save_print():
    """C1：退货开单页拆成「保存」与「保存并打印」两个按钮（2026-10-08 决策）。"""
    init_db()
    client = create_app().test_client()

    html = client.get("/orders/return/new").get_data(as_text=True)

    assert "保存并打印退货单" in html
    assert 'id="saveButton" name="save_action" value="save"' in html
    assert 'name="save_action" value="save_print"' in html
    assert 'id="continueOrderLink" href="/orders/return/new"' in html
    assert "fetch(orderForm.action" in html


def test_orders_mark_return_orders_red_and_account_ledger_preserves_return_sign_and_clear_filter():
    init_db()
    suffix = uuid.uuid4().hex[:8]
    customer_id = create_customer(f"红色退货客户{suffix}")
    create_order_from_typed_rows(customer_id, f"SALE-{suffix}", [{"product_name": f"销售商品{suffix}", "unit": "个", "unit_price_yuan": "100", "quantity": "1"}], status="saved", order_date="2026-07-10")
    create_order_from_typed_rows(customer_id, f"RETURN-{suffix}", [{"product_name": f"退货商品{suffix}", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}], status="saved", order_date="2026-07-11", order_type="return")
    client = create_app().test_client()

    orders_html = client.get(f"/orders/?customer=红色退货客户{suffix}").get_data(as_text=True)
    accounts_html = client.get(f"/accounts/?customer_id={customer_id}").get_data(as_text=True)

    assert 'href="/orders/"' in orders_html
    assert "清除筛选条件" in orders_html
    assert f'<tr class="table-danger return-order-row" data-order-type="return"' in orders_html
    assert f"RETURN-{suffix}" in orders_html
    assert f"SALE-{suffix}" in orders_html

    assert 'href="/accounts/"' in accounts_html
    assert "清除筛选" in accounts_html
    assert "type-return" in accounts_html
    assert "退货单" in accounts_html
    assert "−¥20.00" in accounts_html
    assert f"RETURN-{suffix}" in accounts_html
    assert f"SALE-{suffix}" in accounts_html


def test_products_page_has_similarity_suggestions_and_clear_info_button():
    init_db()
    create_product("沟槽闸阀", "DN100", "个", 12300, "gczf")
    client = create_app().test_client()

    html = client.get("/products/?q=沟").get_data(as_text=True)

    assert "沟槽闸阀" in html
    assert 'list="product_name_suggestions"' in html
    assert '<datalist id="product_name_suggestions">' in html
    assert "清除信息" in html
    assert 'href="/products/"' in html


def test_customers_page_has_similarity_suggestions_search_and_clear_info_button():
    init_db()
    suffix = uuid.uuid4().hex[:8]
    with get_db() as conn:
        conn.execute("INSERT INTO customers(name, phone, address) VALUES (?, ?, ?)", (f"杭州消防客户{suffix}", "138", "杭州"))
    client = create_app().test_client()

    html = client.get(f"/customers/?q={suffix[:4]}").get_data(as_text=True)

    assert f"杭州消防客户{suffix}" in html
    assert 'list="customer_name_suggestions"' in html
    assert '<datalist id="customer_name_suggestions">' in html
    assert "清除信息" in html
    assert 'href="/customers/"' in html
