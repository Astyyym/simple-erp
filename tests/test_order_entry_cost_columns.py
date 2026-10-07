"""Only read-only internal costs may enter the order entry view."""
import re

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import create_purchase_order, initialize_product


def test_order_entry_exposes_readonly_cost_column_not_submitted_cost_input():
    client = create_app().test_client()
    for path in ("/orders/new", "/orders/return/new"):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'class="readonly-total unit-cost-display"' in html
        assert "移动平均成本" in html if path == "/orders/new" else "退货回冲成本" in html
        assert not re.search(r'<input\b[^>]*name="(?:unit_cost|cost_total|avg_cost)', html)
        assert "未选择商品" in html


def test_reedit_initial_cost_uses_historical_snapshot_after_purchase_changes_average():
    init_db()
    product_id = create_product("成本列商品", "4kg", "个", 2500)
    customer_id = create_customer("成本列往来对象")
    initialize_product(product_id, "10", "10", "2026-10-01", "期初", "cost-col-init")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010901",
        [{"product_id": product_id, "product_name": "成本列商品", "spec": "4kg", "unit": "个", "quantity": "2", "unit_price_yuan": "25"}],
        order_date="2026-10-01",
        status="saved",
    )
    create_purchase_order(
        "NH202610010901", "2026-10-01",
        [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "20"}],
        request_key="cost-col-purchase",
        customer_id=customer_id,
    )
    html = create_app().test_client().get(f"/orders/{sale_id}/edit").get_data(as_text=True)
    assert 'data-unit-cost="10.000000"' in html
    assert "销售时成本" in html
    assert 'class="readonly-total unit-cost-display"' in html
    assert 'data-unit-cost="12.000000"' not in html
