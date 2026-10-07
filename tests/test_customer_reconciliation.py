from pathlib import Path

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import initialize_product
from erp.services.reconciliation import (
    create_reconciliation_snapshot,
    get_reconciliation_snapshot,
    update_reconciliation_selection,
    toggle_reconciliation_selection,
    validate_reconciliation_snapshot,
)
from erp.utils import pdf
from pdf_test_utils import assert_a4_portrait_pdf


def _seed_reconciliation_data():
    init_db()
    product = create_product("对账灭火器", "4kg", "个", 3000)
    initialize_product(product, "20", "10", "2026-10-01", "期初", "reconciliation-init")
    customer_id = create_customer("对账客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010201",
        [{"product_id": product, "product_name": "对账灭火器", "spec": "4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
        notes="客户可见备注",
    )
    with get_db() as conn:
        sale_item_id = conn.execute("SELECT id FROM order_items WHERE order_id=?", (sale_id,)).fetchone()["id"]
    from erp.services.accounting import create_return_order_from_source

    return_id = create_return_order_from_source(
        customer_id,
        "MD202610020202",
        sale_id,
        [{"source_item_id": sale_item_id, "quantity": "1"}],
        status="saved",
        order_date="2026-10-02",
        notes="退货备注",
    )
    other_customer = create_customer("其他客户")
    other_order_id = create_order_from_typed_rows(
        other_customer,
        "MD202610020203",
        [{"product_id": product, "product_name": "对账灭火器", "spec": "4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_date="2026-10-02",
    )
    return customer_id, sale_id, return_id, other_order_id


def test_reconciliation_default_selects_full_customer_scope_and_exclusion_is_exact():
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()

    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    snapshot = get_reconciliation_snapshot(snapshot_id)
    assert snapshot["scope_ids"] == [f"order:{sale_id}", f"order:{return_id}"]
    assert snapshot["selected_ids"] == [f"order:{sale_id}", f"order:{return_id}"]
    assert snapshot["selected_total_cents"] == 2000

    update_reconciliation_selection(snapshot_id, mode="all", excluded_ids=[f"order:{return_id}"])
    selected = get_reconciliation_snapshot(snapshot_id)
    assert selected["selected_ids"] == [f"order:{sale_id}"]
    assert selected["selected_total_cents"] == 4000
    assert selected["selected_count"] == 1


def test_reconciliation_rejects_scope_change_and_cross_customer_selection():
    customer_id, sale_id, _, other_order_id = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    with get_db() as conn:
        conn.execute("UPDATE orders SET total_amount_cents=9999 WHERE id=?", (sale_id,))
    error = validate_reconciliation_snapshot(snapshot_id)
    assert error == "查询结果已变化，请重新查询后打印"

    with __import__("pytest").raises(ValueError, match="只能选择当前客户的单据"):
        update_reconciliation_selection(snapshot_id, mode="explicit", selected_ids=[f"order:{sale_id}", f"order:{other_order_id}"])


def test_reconciliation_page_and_pdf_are_business_only_and_do_not_write_payments(tmp_path, monkeypatch):
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    app = create_app()
    client = app.test_client()
    page = client.get(
        "/analytics/reconciliation",
        query_string={"customer_id": customer_id, "start_date": "2026-10-01", "end_date": "2026-10-02"},
    )
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "往来对账" in html
    assert "默认选中" in html
    assert str(sale_id) in html and str(return_id) in html

    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    update_reconciliation_selection(snapshot_id, mode="all", excluded_ids=[f"order:{return_id}"])
    captured = {}

    class CapturingHtml:
        def __init__(self, string, base_url=None):
            captured["html"] = string

        def write_pdf(self, output_path):
            Path(output_path).write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(pdf, "HTML", CapturingHtml)
    with app.app_context():
        generated = pdf.generate_reconciliation_pdf(snapshot_id)
    rendered = captured["html"]
    assert generated.read_bytes().startswith(b"%PDF")
    assert "交易对账净额" in rendered
    assert "对账灭火器" in rendered
    assert "成本" not in rendered
    assert "毛利润" not in rendered
    assert "库存" not in rendered
    assert "货源" not in rendered
    assert "MD202610020202" not in rendered
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM payments").fetchone()["count"] == 0


def test_reconciliation_pdf_endpoint_returns_a4_portrait_pdf():
    customer_id, _, _, _ = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    response = create_app().test_client().get(f"/analytics/reconciliation/pdf/{snapshot_id}")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert_a4_portrait_pdf(response.data)


def test_reconciliation_type_filter_and_empty_selection_are_explicit():
    from erp.utils.pdf import generate_reconciliation_pdf

    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    sale_snapshot = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "sale")
    sale_result = get_reconciliation_snapshot(sale_snapshot)
    assert sale_result["scope_ids"] == [f"order:{sale_id}"]

    empty_snapshot = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    update_reconciliation_selection(empty_snapshot, mode="explicit", selected_ids=[])
    with __import__("pytest").raises(ValueError, match="未选择任何单据"):
        generate_reconciliation_pdf(empty_snapshot)


def test_reconciliation_single_toggle_persists_across_snapshot_page_reads():
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    toggle_reconciliation_selection(snapshot_id, f"order:{return_id}", False)
    assert get_reconciliation_snapshot(snapshot_id)["selected_ids"] == [f"order:{sale_id}"]
    toggle_reconciliation_selection(snapshot_id, f"order:{return_id}", True)
    assert get_reconciliation_snapshot(snapshot_id)["selected_ids"] == [f"order:{sale_id}", f"order:{return_id}"]


def test_reconciliation_order_detail_endpoint_is_limited_to_snapshot_scope():
    customer_id, sale_id, _, other_order_id = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    client = create_app().test_client()
    detail = client.get(f"/analytics/reconciliation/{snapshot_id}/order/{sale_id}")
    assert detail.status_code == 200
    assert detail.get_json()["items"][0]["product_name"] == "对账灭火器"
    rejected = client.get(f"/analytics/reconciliation/{snapshot_id}/order/{other_order_id}")
    assert rejected.status_code == 404


def test_reconciliation_keeps_selection_across_result_pages():
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    with get_db() as conn:
        product_id = conn.execute("SELECT product_id FROM order_items WHERE order_id=?", (sale_id,)).fetchone()["product_id"]
    for index in range(49):
        create_order_from_typed_rows(
            customer_id,
            f"MD20261002{index + 3000:04d}",
            [{"product_id": product_id, "product_name": "对账灭火器", "spec": "4kg", "unit": "个", "unit_price_yuan": "1", "quantity": "0.1"}],
            status="saved",
            order_date="2026-10-02",
        )
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    page_two = create_app().test_client().get(f"/analytics/reconciliation?snapshot_id={snapshot_id}&page=2")
    assert page_two.status_code == 200
    assert "第 2 / 2 页" in page_two.get_data(as_text=True)
    snapshot = get_reconciliation_snapshot(snapshot_id)
    second_page_id = snapshot["scope_ids"][-1]
    toggle_reconciliation_selection(snapshot_id, second_page_id, False)
    updated = get_reconciliation_snapshot(snapshot_id)
    assert updated["selected_count"] == len(snapshot["scope_ids"]) - 1
    assert second_page_id not in updated["selected_ids"]
