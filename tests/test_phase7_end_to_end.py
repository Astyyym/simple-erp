from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
    create_return_order_from_source,
)
from erp.services.analytics import summarize_analytics
from erp.services.inventory import create_purchase_order, initialize_product
from erp.services.reconciliation import create_reconciliation_snapshot, get_reconciliation_snapshot, update_reconciliation_selection


def test_phase7_complete_inventory_to_reconciliation_path_keeps_contract_values():
    init_db()
    product_id = create_product("Phase7灭火器", "4kg", "个", 2000, safety_stock="2")
    customer_id = create_customer("Phase7验收客户")
    initialize_product(product_id, "10", "10", "2026-10-01", "Phase 7 期初", "phase7-e2e-init")

    first_purchase = create_purchase_order(
        "NH202610010701",
        "2026-10-01",
        [{"product_id": product_id, "quantity": "10", "unit_cost_yuan": "14"}],
        request_key="phase7-e2e-purchase-1",
        customer_id=customer_id,
    )
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010701",
        [{"product_id": product_id, "product_name": "Phase7灭火器", "spec": "4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "5"}],
        status="saved",
        order_date="2026-10-01",
    )
    second_purchase = create_purchase_order(
        "NH202610010702",
        "2026-10-01",
        [{"product_id": product_id, "quantity": "5", "unit_cost_yuan": "20"}],
        request_key="phase7-e2e-purchase-2",
        customer_id=customer_id,
    )
    with get_db() as conn:
        sale_item_id = conn.execute("SELECT id FROM order_items WHERE order_id=?", (sale_id,)).fetchone()["id"]
    return_id = create_return_order_from_source(
        customer_id,
        "MD202610020701",
        sale_id,
        [{"source_item_id": sale_item_id, "quantity": "2"}],
        status="saved",
        order_date="2026-10-02",
    )

    with get_db() as conn:
        state = conn.execute(
            "SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        sale_item = conn.execute(
            "SELECT subtotal_cents, cost_total_micro, unit_cost_micro FROM order_items WHERE order_id=?",
            (sale_id,),
        ).fetchone()
        return_item = conn.execute(
            "SELECT subtotal_cents, cost_total_micro FROM order_items WHERE order_id=?",
            (return_id,),
        ).fetchone()

    assert (state["quantity_3dp"], state["cost_total_micro"]) == (22000, 304_000_000)
    assert (sale_item["subtotal_cents"], sale_item["cost_total_micro"], sale_item["unit_cost_micro"]) == (10000, 60_000_000, 12_000_000)
    assert (return_item["subtotal_cents"], return_item["cost_total_micro"]) == (4000, 24_000_000)
    assert first_purchase["order_id"] != second_purchase["order_id"]

    analytics = summarize_analytics(
        start_date="2026-10-01",
        end_date="2026-10-02",
        customer_id=customer_id,
        product_name="Phase7灭火器",
        spec="4kg",
        metric="amount",
    )
    assert analytics["summary"]["net_sales_amount_cents"] == 6000
    assert analytics["summary"]["net_cost_cents"] == 3600
    assert analytics["summary"]["gross_profit_cents"] == 2400

    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    snapshot = get_reconciliation_snapshot(snapshot_id)
    expected_scope = [f"order:{sale_id}", f"purchase:{first_purchase['order_id']}",
                      f"purchase:{second_purchase['order_id']}", f"order:{return_id}"]
    assert snapshot["scope_version"] == 2
    assert snapshot["scope_ids"] == snapshot["selected_ids"] == expected_scope
    assert snapshot["selected_count"] == snapshot["scope_count"] == len(expected_scope)
    purchase_total = first_purchase["total_amount_cents"] + second_purchase["total_amount_cents"]
    assert snapshot["type_totals_cents"] == {
        "sale": 10000, "customer_return": -4000,
        "purchase": -purchase_total, "purchase_return": 0,
    }
    assert snapshot["selected_total_cents"] == 6000 - purchase_total
    # Selection can exclude buying transactions; it must never change sales analysis.
    update_reconciliation_selection(snapshot_id, mode="all", excluded_ids=expected_scope[1:3])
    selected = get_reconciliation_snapshot(snapshot_id)
    assert selected["selected_ids"] == [f"order:{sale_id}", f"order:{return_id}"]
    assert selected["selected_total_cents"] == 6000

    client = create_app().test_client()
    assert client.get(f"/products/?q=Phase7灭火器").status_code == 200
    assert client.get(f"/purchases/{first_purchase['order_id']}").status_code == 200
    assert client.get(f"/orders/{sale_id}").status_code == 200
    analytics_page = client.get(
        "/analytics/",
        query_string={"start_date": "2026-10-01", "end_date": "2026-10-02", "customer_id": customer_id, "product_name": "Phase7灭火器", "spec": "4kg", "metric": "amount"},
    )
    reconciliation_page = client.get(
        "/analytics/reconciliation",
        query_string={"customer_id": customer_id, "start_date": "2026-10-01", "end_date": "2026-10-02"},
    )
    assert analytics_page.status_code == 200
    assert "Phase7灭火器" in analytics_page.get_data(as_text=True)
    assert reconciliation_page.status_code == 200
    assert f"已选 {len(expected_scope)} / 符合条件 {len(expected_scope)} 笔" in reconciliation_page.get_data(as_text=True)