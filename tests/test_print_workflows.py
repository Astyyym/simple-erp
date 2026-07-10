from pathlib import Path
from uuid import uuid4

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp.utils.pdf import _account_summary_table_rows, generate_account_summary_pdf


def test_account_summary_pdf_uses_filtered_order_items_without_grouping():
    init_db()
    app = create_app()
    suffix = uuid4().hex[:8]
    customer_id = create_customer(f"汇总客户-{suffix}")
    create_order_from_typed_rows(customer_id, f"SUM-{suffix}-001", [{"product_name": "闸阀", "unit": "只", "unit_price_yuan": "10", "quantity": "2"}], status="saved", order_date="2026-07-01")
    create_order_from_typed_rows(customer_id, f"SUM-{suffix}-002", [{"product_name": "闸阀", "unit": "只", "unit_price_yuan": "11", "quantity": "3"}], status="saved", order_date="2026-07-02")
    with app.app_context():
        path = generate_account_summary_pdf(customer_id, "2026-07-01", "2026-07-31")
    assert isinstance(path, Path)
    assert path.exists()
    assert path.stat().st_size > 0


def test_account_summary_rows_merge_same_date_and_order_no():
    rows = [
        {"order_date": "2026-07-10", "order_no": "SUM-001", "product_name": "A", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 1000, "subtotal_cents": 1000},
        {"order_date": "2026-07-10", "order_no": "SUM-002", "product_name": "B", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 2000, "subtotal_cents": 2000},
        {"order_date": "2026-07-10", "order_no": "SUM-002", "product_name": "C", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 3000, "subtotal_cents": 3000},
    ]

    prepared = _account_summary_table_rows(rows)

    assert prepared[0]["show_date"] is True
    assert prepared[0]["date_rowspan"] == 3
    assert prepared[1]["show_date"] is False
    assert prepared[1]["show_order_no"] is True
    assert prepared[1]["order_no_rowspan"] == 2
    assert prepared[2]["show_order_no"] is False


def test_save_print_redirects_to_order_pdf():
    init_db()
    suffix = uuid4().hex[:8]
    customer_id = create_customer(f"打印客户-{suffix}")
    app = create_app()
    client = app.test_client()
    order_no = f"PRINT-{suffix}"
    response = client.post("/orders/create", data={
        "customer_id": str(customer_id),
        "customer_name": f"打印客户-{suffix}",
        "order_no": order_no,
        "order_date": "2026-07-10",
        "status": "saved",
        "product_name": ["闸阀"],
        "unit": ["只"],
        "unit_price": ["12"],
        "quantity": ["1"],
        "save_action": "save_print",
    })
    assert response.status_code == 302
    with get_db() as conn:
        order = conn.execute("SELECT id FROM orders WHERE order_no=?", (order_no,)).fetchone()
    assert response.headers["Location"].endswith(f"/orders/{order['id']}/pdf")


def test_new_order_save_print_button_opens_pdf_in_new_tab_and_clears_original_page():
    init_db()
    app = create_app()
    client = app.test_client()

    html = client.get("/orders/new?date=2026-07-10").data.decode("utf-8")

    assert "保存/打印订单" in html
    assert "e.currentTarget.target = '_blank';" in html
    assert "window.location.href = '/orders/new';" in html
