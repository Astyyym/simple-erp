from pathlib import Path
from uuid import uuid4
from datetime import date

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


def test_account_summary_rows_merge_same_date_and_order_no_and_show_order_total_once():
    rows = [
        {"order_date": "2026-07-10", "order_no": "SUM-001", "order_type": "sale", "product_name": "A", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 1000, "subtotal_cents": 1000, "order_total_cents": 1000},
        {"order_date": "2026-07-10", "order_no": "SUM-002", "order_type": "return", "product_name": "B", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 2000, "subtotal_cents": 2000, "order_total_cents": -5000},
        {"order_date": "2026-07-10", "order_no": "SUM-002", "order_type": "return", "product_name": "C", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 3000, "subtotal_cents": 3000, "order_total_cents": -5000},
    ]

    prepared = _account_summary_table_rows(rows)

    assert prepared[0]["show_date"] is True
    assert prepared[0]["date_rowspan"] == 3
    assert prepared[1]["show_date"] is False
    assert prepared[1]["show_order_no"] is True
    assert prepared[1]["order_no_rowspan"] == 2
    assert prepared[1]["show_order_total"] is True
    assert prepared[1]["order_total_rowspan"] == 2
    assert prepared[1]["is_return"] is True
    assert prepared[2]["show_order_no"] is False
    assert prepared[2]["show_order_total"] is False


def test_account_summary_totals_split_sale_return_and_net():
    from erp.utils.pdf import _account_summary_totals

    rows = [
        {"order_no": "S1", "order_type": "sale", "order_total_cents": 10000},
        {"order_no": "S1", "order_type": "sale", "order_total_cents": 10000},
        {"order_no": "R1", "order_type": "return", "order_total_cents": -3000},
        {"order_no": "R1", "order_type": "return", "order_total_cents": -3000},
    ]

    totals = _account_summary_totals(rows)

    assert totals["sale_total_cents"] == 10000
    assert totals["return_total_cents"] == 3000
    assert totals["net_total_cents"] == 7000


def test_order_print_signatures_share_phone_and_address_rows():
    template = Path("app/erp/templates/orders/print_template.html").read_text(encoding="utf-8")

    # Footer uses fixed table columns (WeasyPrint-stable), not flex + translate.
    assert 'table class="footer-grid"' in template or "table class=\"footer-grid\"" in template
    assert "footer-sign" in template
    assert "footer-text" in template
    assert "制单人：{{config.print_maker_name}}" in template
    assert "{{config.print_receiver_label}}" in template
    assert "config.print_order_phone" in template
    assert "config.print_order_address" in template
    assert "config.print_main_business" in template
    assert "config.print_legal_note" in template
    assert "width:46.5mm" in template or "width: 46.5mm" in template
    assert "business-row" not in template
    assert "maker-sign" not in template
    assert "translateX(-15mm)" not in template
    assert "sign-row" not in template
    assert "REDACTED_PHONE_2" not in template
    assert "REDACTED_CONTACT" not in template


def test_order_pdf_footer_sign_columns_share_fixed_width():
    """Regenerate a real PDF path and assert template HTML used for WeasyPrint keeps equal sign cells."""
    from erp.utils.pdf import generate_order_pdf

    init_db()
    suffix = uuid4().hex[:8]
    customer_id = create_customer(f"签字对齐-{suffix}")
    order_id = create_order_from_typed_rows(
        customer_id,
        f"ALIGN-{suffix}",
        [{"product_name": "对齐阀", "unit": "只", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
    )
    app = create_app()
    with app.app_context():
        path = generate_order_pdf(order_id)
    assert path.exists() and path.stat().st_size > 0
    # Source template contract (WeasyPrint input)
    template = Path("app/erp/templates/orders/print_template.html").read_text(encoding="utf-8")
    assert template.count("footer-sign") >= 2
    assert template.count("width:46.5mm") + template.count("width: 46.5mm") >= 1
    assert "translateX" not in template


def test_save_print_redirects_to_order_pdf():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"打印客户-{suffix}"
    customer_id = create_customer(customer_name)
    app = create_app()
    client = app.test_client()
    response = client.post("/orders/create", data={
        "customer_id": str(customer_id),
        "customer_name": customer_name,
        "order_no": f"PRINT-{suffix}",
        "order_date": "2026-07-10",
        "status": "saved",
        "product_name": ["闸阀"],
        "unit": ["只"],
        "unit_price": ["12"],
        "quantity": ["1"],
        "save_action": "save_print",
    })
    assert response.status_code == 303
    with get_db() as conn:
        order = conn.execute(
            "SELECT id, order_no, order_date FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
    assert order is not None
    assert order["order_no"] == "MD202607100001"
    assert order["order_date"] == "2026-07-10"
    assert response.headers["Location"].endswith(f"/orders/{order['id']}/pdf")


def test_new_order_save_print_button_uses_async_save_and_success_actions():
    init_db()
    app = create_app()
    client = app.test_client()

    html = client.get("/orders/new?date=2026-07-10").data.decode("utf-8")

    assert "保存/打印订单" in html
    assert "fetch(orderForm.action" in html
    assert "window.open(result.pdf_url, '_blank', 'noopener')" in html
    assert 'id="openPrintLink"' in html
    assert 'id="continueOrderLink" href="/orders/new"' in html
    assert "e.currentTarget.target = '_blank';" not in html
