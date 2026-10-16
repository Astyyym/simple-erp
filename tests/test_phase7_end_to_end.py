from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
    create_return_order_from_source,
)
from erp.services.analytics import summarize_analytics
from erp.services.purchases import create_purchase_order
from erp.services.reconciliation import create_reconciliation_snapshot, get_reconciliation_snapshot, update_reconciliation_selection


def test_phase7_complete_purchase_to_reconciliation_path_keeps_contract_values():
    """Phase7 端到端契约：拿货/销售/退货 → 分析口径 → 对账快照。

    决策变更（2026-10-16 ②/③）：库存与成本段（库存状态、明细成本快照、毛利润/净成本）
    随库存系统整体删除；保留仍然成立的三段——单据金额与单号、分析汇总口径、
    对账快照的范围/金额契约，外加各页面冒烟。
    """
    init_db()
    product_id = create_product("Phase7灭火器4kg", "个")
    customer_id = create_customer("Phase7验收客户")


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
        [{"product_id": product_id, "product_name": "Phase7灭火器4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "5"}],
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
        sale_item = conn.execute(
            "SELECT subtotal_cents FROM order_items WHERE order_id=?",
            (sale_id,),
        ).fetchone()
        return_item = conn.execute(
            "SELECT subtotal_cents FROM order_items WHERE order_id=?",
            (return_id,),
        ).fetchone()
        purchase_item = conn.execute(
            "SELECT unit_cost_cents, subtotal_cents FROM purchase_order_items WHERE purchase_order_id=?",
            (first_purchase["order_id"],),
        ).fetchone()

    # 销售 5 × 20 元 = 10000 分；退货按原单价比例回销 2/5 → 4000 分。
    assert sale_item["subtotal_cents"] == 10000
    assert return_item["subtotal_cents"] == 4000
    # 拿货单价是业务数据（决策 ①）：库存系统删除不影响它。
    assert (purchase_item["unit_cost_cents"], purchase_item["subtotal_cents"]) == (1400, 14000)
    assert first_purchase["order_id"] != second_purchase["order_id"]

    # 决策变更（v14）：商品身份合并为单一名称（Phase7灭火器4kg），型号不再是独立筛选参数。
    analytics = summarize_analytics(
        start_date="2026-10-01",
        end_date="2026-10-02",
        customer_id=customer_id,
        product_name="Phase7灭火器4kg",
        metric="amount",
    )
    # 汇总卡口径（决策 ②）：净销售额 = 销售 10000 − 退货 4000；成本/毛利润卡已下线。
    assert analytics["summary"]["sales_amount_cents"] == 10000
    assert analytics["summary"]["returns_amount_cents"] == 4000
    assert analytics["summary"]["net_sales_amount_cents"] == 6000
    assert "net_cost_cents" not in analytics["summary"]
    assert "gross_profit_cents" not in analytics["summary"]

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
        query_string={"start_date": "2026-10-01", "end_date": "2026-10-02", "customer_id": customer_id, "product_name": "Phase7灭火器4kg", "metric": "amount"},
    )
    reconciliation_page = client.get(
        "/accounts/reconciliation",
        query_string={"customer_id": customer_id, "start_date": "2026-10-01", "end_date": "2026-10-02"},
    )
    assert analytics_page.status_code == 200
    assert "Phase7灭火器" in analytics_page.get_data(as_text=True)
    assert reconciliation_page.status_code == 200
    assert f"已选 {len(expected_scope)} / 符合条件 {len(expected_scope)} 笔" in reconciliation_page.get_data(as_text=True)