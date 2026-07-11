import uuid

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import add_adjustment, add_payment, create_customer, create_order_from_typed_rows, customer_balance_cents


def test_accounts_bulk_balance_query_matches_individual_balance_calculation():
    init_db()
    suffix = uuid.uuid4().hex[:8]
    customer_id = create_customer(f"批量余额客户{suffix}", opening_balance_cents=10000)
    create_order_from_typed_rows(
        customer_id,
        f"BAL-{suffix}",
        [{"product_name": f"余额商品{suffix}", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="saved",
        order_date="2026-07-11",
    )
    add_payment(customer_id, 1500)
    add_adjustment(customer_id, -500, "测试核减")

    expected = customer_balance_cents(customer_id)
    html = create_app().test_client().get("/accounts/").get_data(as_text=True)

    assert f"批量余额客户{suffix}" in html
    assert f"{expected / 100:.2f}" in html


def test_order_entry_pages_have_one_click_clear_before_add_row():
    client = create_app().test_client()

    for path in ("/orders/new", "/orders/return/new"):
        html = client.get(path).get_data(as_text=True)
        clear_pos = html.index("一键清空")
        add_pos = html.index("新增一行")
        assert clear_pos < add_pos
        assert 'onclick="clearAllRows()"' in html
        assert "function clearAllRows()" in html
        assert "while (tbody.rows.length > 1)" in html
