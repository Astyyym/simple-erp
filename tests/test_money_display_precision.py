"""Display cents without rounding the cost used by inventory or order previews."""
import pytest

from erp import create_app
from erp.db import get_db
from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
from erp.services.inventory import initialize_product
from erp.utils.money import micro_to_yuan


@pytest.mark.parametrize("micro, expected", [
    (None, "—"), (0, "0.00"), (1, "0.00"), (5_000, "0.01"),
    (1_234_567, "1.23"), (1_995_000, "2.00"), (-1_005_000, "-1.01"),
])
def test_internal_micro_cost_formats_as_two_visible_decimal_places(micro, expected):
    assert micro_to_yuan(micro) == expected


def test_two_decimal_display_keeps_exact_cost_payload_and_historical_snapshot():
    app = create_app()
    product_id = create_product("小数成本验收商品", "S1", "个", 2000)
    customer_id = create_customer("小数成本验收往来")
    initialize_product(product_id, "15", "11.333333", "2026-10-01", "测试期初", "money-display-init")
    sale_id = create_order_from_typed_rows(
        customer_id, "MD202610010950",
        [{"product_id": product_id, "product_name": "小数成本验收商品", "spec": "S1", "unit": "个", "quantity": "3", "unit_price_yuan": "20"}],
        status="saved", order_date="2026-10-01",
    )
    with get_db() as conn:
        before = tuple(conn.execute("SELECT quantity_3dp,cost_total_micro,avg_cost_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone())
        cost_snapshot = tuple(conn.execute("SELECT unit_cost_micro,cost_total_micro FROM order_items WHERE order_id=?", (sale_id,)).fetchone())
    client = app.test_client()
    product = client.get("/orders/api/products", query_string={"q": "小数成本验收商品"}).get_json()[0]
    assert product["unit_cost"] == "11.333333"
    return_item = client.get(f"/orders/api/return_sources/{sale_id}").get_json()["items"][0]
    assert return_item["unit_cost"] == "11.333333"
    html = client.get(f"/orders/{sale_id}/edit").get_data(as_text=True)
    assert 'data-unit-cost="11.333333"' in html
    assert 'aria-label="只读成本">11.33</div>' in html
    with get_db() as conn:
        after = tuple(conn.execute("SELECT quantity_3dp,cost_total_micro,avg_cost_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone())
        after_snapshot = tuple(conn.execute("SELECT unit_cost_micro,cost_total_micro FROM order_items WHERE order_id=?", (sale_id,)).fetchone())
    assert after == before
    assert after_snapshot == cost_snapshot == (11_333_333, 33_999_999)
