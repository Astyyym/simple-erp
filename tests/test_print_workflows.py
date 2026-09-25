from datetime import date
from pathlib import Path
from uuid import uuid4

from pdf_test_utils import assert_a4_portrait_pdf

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
    assert_a4_portrait_pdf(path.read_bytes())


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
    assert "width:46.5mm" in template or "width: 46.5mm" in template or "width:40mm" in template or "width: 40mm" in template or "width:38mm" in template or "width: 38mm" in template
    assert "print_offset_x_mm" in template
    assert "print_offset_y_mm" in template
    assert "print_scale" in template
    assert "max-width: 186mm" in template or "max-width:186mm" in template
    assert "width: 241mm" not in template and "width:241mm" not in template
    assert "height: 140mm" not in template and "height:140mm" not in template
    assert "order_pdf_page_width_mm" in template
    assert "order_pdf_page_height_mm" in template
    assert "width:38%" in template or "width: 38%" in template
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
    assert (
        template.count("width:46.5mm")
        + template.count("width: 46.5mm")
        + template.count("width:40mm")
        + template.count("width: 40mm")
        + template.count("width:38mm")
        + template.count("width: 38mm")
    ) >= 1
    # Only the page-level calibration translate(...) is allowed — not maker-sign hacks.
    assert "translateX" not in template
    assert "transform: translate({{config.print_offset_x_mm}}mm" in template or "print_offset_x_mm" in template
    assert "max-width: 186mm" in template or "max-width:186mm" in template


def test_print_defaults_are_a4_portrait():
    from erp.config import _merge_defaults

    config = _merge_defaults({})

    assert config["printer_paper_width_mm"] == 210
    assert config["printer_paper_height_mm"] == 297
    assert config["order_pdf_page_width_mm"] == 210
    assert config["order_pdf_page_height_mm"] == 297


def test_order_print_preview_and_sample_pdf_use_a4_portrait():
    from erp.config import load_config, save_config

    init_db()
    save_config(
        {
            "printer_paper_width_mm": 210,
            "printer_paper_height_mm": 297,
            "order_pdf_page_width_mm": 210,
            "order_pdf_page_height_mm": 297,
        }
    )
    cfg = load_config()
    assert int(cfg["printer_paper_width_mm"]) == 210
    assert int(cfg["printer_paper_height_mm"]) == 297
    assert float(cfg["order_pdf_page_width_mm"]) == 210
    assert float(cfg["order_pdf_page_height_mm"]) == 297
    app = create_app()
    client = app.test_client()
    html = client.get("/settings/print-preview").get_data(as_text=True)
    assert "size: 210mm 297mm" in html or "size:210mm 297mm" in html
    assert "max-width: 186mm" in html or "max-width:186mm" in html
    assert "width: 241mm" not in html and "width:241mm" not in html
    assert "height: 140mm" not in html and "height:140mm" not in html
    settings_html = client.get("/settings/").get_data(as_text=True)
    assert "A4 竖向（210×297mm）" in settings_html
    assert "实际大小/100%" in settings_html

    pdf_response = client.get("/settings/print-preview.pdf")
    assert pdf_response.status_code == 200
    assert pdf_response.mimetype == "application/pdf"
    assert_a4_portrait_pdf(pdf_response.data)


def test_sales_and_return_order_pdfs_use_a4_portrait():
    from erp.utils.pdf import generate_order_pdf

    init_db()
    suffix = uuid4().hex[:8]
    customer_id = create_customer(f"竖版打印客户-{suffix}")
    sale_id = create_order_from_typed_rows(
        customer_id,
        f"PORTRAIT-SALE-{suffix}",
        [{"product_name": "销售闸阀", "unit": "只", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_type="sale",
    )
    return_id = create_order_from_typed_rows(
        customer_id,
        f"PORTRAIT-RETURN-{suffix}",
        [{"product_name": "退货闸阀", "unit": "只", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_type="return",
    )
    app = create_app()
    with app.app_context():
        for order_id in (sale_id, return_id):
            path = generate_order_pdf(order_id)
            assert path.exists() and path.stat().st_size > 0
            assert_a4_portrait_pdf(path.read_bytes())


def test_order_print_preview_html_includes_configured_offsets():
    from erp.config import save_config

    init_db()
    save_config({"print_offset_x_mm": 8, "print_offset_y_mm": -10, "print_scale": 1.0})
    app = create_app()
    client = app.test_client()
    html = client.get("/settings/print-preview").get_data(as_text=True)
    assert "translate(8mm, -10mm)" in html or "translate(8.0mm, -10.0mm)" in html
    assert "scale(1.0)" in html or "scale(1)" in html


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
    assert "openPrintUrl(result.pdf_url)" in html
    assert "function openPrintUrl(url)" in html
    assert 'id="openPrintLink"' in html
    assert 'id="continueOrderLink" href="/orders/new"' in html
    assert "e.currentTarget.target = '_blank';" not in html


def test_browser_print_links_keep_blank_target_when_not_desktop():
    """Ordinary browser keeps target=_blank so PDF does not replace the ERP page."""
    init_db()
    app = create_app()
    client = app.test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert 'data-desktop="0"' in html
    assert "openPrintUrl(result.pdf_url)" in html
    # Manual fallback link still opens a new tab in browser mode.
    assert 'id="openPrintLink"' in html
    assert 'target="_blank"' in html
    assert "function isDesktopShell()" in html


def test_desktop_print_links_stay_in_shell_without_blank_target(monkeypatch):
    """Desktop shell must not force system-browser popups (session cookie split)."""
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"桌面打印-{suffix}"
    customer_id = create_customer(customer_name)
    order_id = create_order_from_typed_rows(
        customer_id,
        f"DESK-{suffix}",
        [{"product_name": "桌面阀", "unit": "只", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )
    app = create_app()
    client = app.test_client()

    new_html = client.get("/orders/new").get_data(as_text=True)
    assert 'data-desktop="1"' in new_html
    assert "openPrintUrl(result.pdf_url)" in new_html
    assert 'id="openPrintLink"' in new_html
    # Desktop: openPrintLink must not set target=_blank
    assert 'id="openPrintLink" href="#"' in new_html
    assert 'id="openPrintLink" href="#" target="_blank"' not in new_html

    list_html = client.get("/orders/").get_data(as_text=True)
    assert f'href="/orders/{order_id}/pdf?desktop_preview=1"' in list_html
    assert f'href="/orders/{order_id}/pdf?desktop_preview=1" target="_blank"' not in list_html
    assert 'id="exportSummaryBtn"' in list_html
    assert 'id="exportSummaryBtn" class="btn btn-info" href="/orders/summary_pdf" target="_blank"' not in list_html

    detail_html = client.get(f"/orders/{order_id}").get_data(as_text=True)
    assert f'href="/orders/{order_id}/pdf?desktop_preview=1"' in detail_html
    assert f'href="/orders/{order_id}/pdf?desktop_preview=1" target="_blank"' not in detail_html

    settings_html = client.get("/settings/").get_data(as_text=True)
    assert 'id="settingsPrintPreview"' in settings_html
    assert 'id="settingsPrintPreview" href="/settings/print-preview" target="_blank"' not in settings_html
    assert 'id="settingsPrintPreviewPdf" href="/settings/print-preview.pdf?desktop_preview=1"' in settings_html
    assert 'id="settingsPrintPreviewPdf" href="/settings/print-preview.pdf?desktop_preview=1" target="_blank"' not in settings_html

    accounts_html = client.get(f"/accounts/?customer_id={customer_id}").get_data(as_text=True)
    assert "打印汇总表" in accounts_html
    assert 'target="_blank" href="/accounts/summary_pdf' not in accounts_html
    assert 'href="/accounts/summary_pdf' in accounts_html
    assert 'target="_blank"' not in accounts_html or 'summary_pdf' in accounts_html
    # Stronger: the summary link itself has no target=_blank
    assert 'summary_pdf?customer_id=' in accounts_html
    assert 'summary_pdf?customer_id=' + str(customer_id) in accounts_html or f"customer_id={customer_id}" in accounts_html
    assert 'target="_blank" rel="noopener">打印汇总表' not in accounts_html
    assert 'target="_blank">打印汇总表' not in accounts_html
