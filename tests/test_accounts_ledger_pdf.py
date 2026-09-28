from pathlib import Path

from pdf_test_utils import assert_a4_portrait_pdf
from erp import create_app
from erp.db import get_db, init_db
from erp.services import accounting
from erp.utils import pdf


def _make_ledger_customer() -> int:
    init_db()
    customer_id = accounting.create_customer("流水 PDF 测试客户", opening_balance_cents=10000)
    accounting.create_order_from_typed_rows(
        customer_id,
        "PDF-AUG-SALE",
        [{"product_name": "历史消防水带", "unit": "卷", "unit_price_yuan": "400", "quantity": "1"}],
        status="saved",
        order_date="2026-08-20",
    )
    accounting.create_order_from_typed_rows(
        customer_id,
        "PDF-SEP-SALE",
        [{"product_name": "消火栓箱", "unit": "台", "unit_price_yuan": "120", "quantity": "1"}],
        status="saved",
        order_date="2026-09-24",
    )
    accounting.create_order_from_typed_rows(
        customer_id,
        "PDF-SEP-RETURN",
        [{"product_name": "退回配件", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-09-23",
        order_type="return",
    )
    accounting.add_payment(customer_id, 2000, payment_date="2026-09-22", method="转账")
    adjustment_id = accounting.add_adjustment(customer_id, 500, "差额补记")
    with get_db() as conn:
        conn.execute(
            "UPDATE adjustments SET created_at=? WHERE id=?",
            ("2026-09-25 12:00:00", adjustment_id),
        )
    return customer_id


def test_account_ledger_pdf_renders_current_balance_and_filtered_period_from_shared_ledger(tmp_path, monkeypatch):
    customer_id = _make_ledger_customer()
    captured = {}

    class CapturingHtml:
        def __init__(self, string, base_url=None):
            captured["html"] = string

        def write_pdf(self, output_path):
            Path(output_path).write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(pdf, "HTML", CapturingHtml)
    app = create_app()
    with app.app_context():
        generated_path = pdf.generate_account_ledger_pdf(
            customer_id,
            "2026-09-20",
            "2026-09-30",
        )

    html = captured["html"]
    assert generated_path.suffix == ".pdf"
    assert generated_path.read_bytes().startswith(b"%PDF")
    assert "当前余额" in html
    assert "筛选区间净变动" in html
    assert "2026-09-20" in html and "2026-09-30" in html
    assert "¥575.00" in html
    assert "+¥75.00" in html
    assert "PDF-SEP-SALE" in html
    assert "PDF-SEP-RETURN" in html
    assert "PDF-AUG-SALE" not in html


def test_account_ledger_pdf_endpoint_returns_a_real_a4_portrait_pdf():
    customer_id = _make_ledger_customer()
    response = create_app().test_client().get(
        "/accounts/ledger_pdf",
        query_string={
            "customer_id": customer_id,
            "start_date": "2026-09-20",
            "end_date": "2026-09-30",
        },
    )

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert len(response.data) > 1000
    assert_a4_portrait_pdf(response.data)
