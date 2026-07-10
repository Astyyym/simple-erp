from flask import render_template

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp.utils.money import cents_to_yuan
from erp.utils.pdf import _account_summary_table_rows, _account_summary_totals


def test_account_summary_template_marks_return_rows_red_and_prints_net_formula():
    init_db()
    app = create_app()
    customer_id = create_customer("汇总退货客户")
    create_order_from_typed_rows(
        customer_id,
        "SALE-SUMMARY-TPL",
        [{"product_name": "销售阀门", "unit": "个", "unit_price_yuan": "100", "quantity": "1"}],
        status="saved",
        order_date="2026-07-10",
    )
    create_order_from_typed_rows(
        customer_id,
        "RETURN-SUMMARY-TPL",
        [{"product_name": "退货阀门A", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}, {"product_name": "退货阀门B", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-07-11",
        order_type="return",
    )
    rows = [
        {"order_date": "2026-07-10", "order_no": "SALE-SUMMARY-TPL", "order_type": "sale", "product_name": "销售阀门", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 10000, "subtotal_cents": 10000, "order_total_cents": 10000},
        {"order_date": "2026-07-11", "order_no": "RETURN-SUMMARY-TPL", "order_type": "return", "product_name": "退货阀门A", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 2000, "subtotal_cents": 2000, "order_total_cents": -5000},
        {"order_date": "2026-07-11", "order_no": "RETURN-SUMMARY-TPL", "order_type": "return", "product_name": "退货阀门B", "spec": "", "unit": "个", "quantity": "1", "unit_price_cents": 3000, "subtotal_cents": 3000, "order_total_cents": -5000},
    ]
    with app.app_context():
        html = render_template(
            "accounts/summary_pdf.html",
            customer={"name": "汇总退货客户"},
            rows=_account_summary_table_rows(rows),
            start_date="2026-07-10",
            end_date="2026-07-11",
            total_cents=5000,
            totals=_account_summary_totals(rows),
            config={"shop_name": "测试店", "printer_paper_width_mm": 241, "printer_paper_height_mm": 280},
            cents_to_yuan=cents_to_yuan,
        )

    assert 'class="return-row"' in html
    assert "销售总金额 ¥100.00 - 退货总金额 ¥50.00 = 总金额 ¥50.00" in html
    assert html.count("¥-50.00") == 1
