"""Batch D：区间两端余额（起始欠款 / 截止欠款）与动态标题。"""

from __future__ import annotations

from pathlib import Path

from erp import create_app
from erp.db import get_db, init_db
from erp.services import accounting
from erp.utils import pdf


def _make_ledger_customer() -> int:
    init_db()
    customer_id = accounting.create_customer("区间两端测试客户", opening_balance_cents=10000)
    accounting.create_order_from_typed_rows(
        customer_id, "D-AUG-SALE",
        [{"product_name": "历史水带", "unit": "卷", "unit_price_yuan": "400", "quantity": "1"}],
        status="saved", order_date="2026-08-20",
    )
    accounting.create_order_from_typed_rows(
        customer_id, "D-SEP-SALE",
        [{"product_name": "消火栓箱", "unit": "台", "unit_price_yuan": "120", "quantity": "1"}],
        status="saved", order_date="2026-09-24",
    )
    accounting.create_order_from_typed_rows(
        customer_id, "D-SEP-RETURN",
        [{"product_name": "退回配件", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date="2026-09-23", order_type="return",
    )
    accounting.add_payment(customer_id, 2000, payment_date="2026-09-22", method="转账")
    adjustment_id = accounting.add_adjustment(customer_id, 500, "差额补记")
    with get_db() as conn:
        conn.execute("UPDATE adjustments SET created_at=? WHERE id=?", ("2026-09-25 12:00:00", adjustment_id))
    return customer_id


def _capture(monkeypatch):
    captured = {}

    class CapturingHtml:
        def __init__(self, string, base_url=None):
            captured["html"] = string

        def write_pdf(self, output_path):
            Path(output_path).write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(pdf, "HTML", CapturingHtml)
    return captured


def test_period_start_and_end_balance():
    customer_id = _make_ledger_customer()
    ledger = accounting.get_customer_account_ledger(customer_id, "2026-09-20", "2026-09-30")
    period = ledger["period"]
    # 起始欠款 = 期初 100 + 区间前（8月销售 400）= 500
    assert period["start_balance_cents"] == 50000
    # 净变动 = 退货 -30 + 销售 120 + 收款 -20 + 调整 5 = 75
    assert period["net_change_cents"] == 7500
    # 截止欠款 = 500 + 75 = 575
    assert period["end_balance_cents"] == 57500


def test_end_balance_equals_current_balance_when_window_covers_today():
    customer_id = _make_ledger_customer()
    # end_date 覆盖全部流水 → 截止欠款必须严格等于当前余额
    ledger = accounting.get_customer_account_ledger(customer_id, "", "2026-12-31")
    assert ledger["period"]["end_balance_cents"] == ledger["current_balance_cents"]
    assert ledger["period"]["start_balance_cents"] == ledger["opening_balance_cents"]


def test_cross_month_quarter_and_non_natural_windows():
    customer_id = _make_ledger_customer()
    # 跨月：8/25 ~ 9/30 —— 8/20 销售早于区间起点，故计入起始欠款
    q3 = accounting.get_customer_account_ledger(customer_id, "2026-08-25", "2026-09-30")
    assert q3["period"]["start_balance_cents"] == 10000 + 40000  # 期初 + 8月销售
    assert q3["period"]["end_balance_cents"] == q3["current_balance_cents"]

    # 非自然月：9/23 ~ 9/24（只含退货 + 销售；9/22 收款早于区间起点，计入起始）
    window = accounting.get_customer_account_ledger(customer_id, "2026-09-23", "2026-09-24")
    assert window["period"]["start_balance_cents"] == 10000 + 40000 - 2000  # 期初 + 8月销售 − 9/22 收款
    assert window["period"]["net_change_cents"] == -3000 + 12000  # 退货 + 销售
    assert window["period"]["end_balance_cents"] == 57000


def test_empty_window_does_not_error():
    customer_id = _make_ledger_customer()
    ledger = accounting.get_customer_account_ledger(customer_id, "2027-01-01", "2027-01-31")
    assert ledger["period"]["net_change_cents"] == 0
    assert ledger["period"]["start_balance_cents"] == ledger["current_balance_cents"]
    assert ledger["period"]["end_balance_cents"] == ledger["current_balance_cents"]


def test_pdf_contains_two_end_balances_and_dynamic_title(monkeypatch):
    customer_id = _make_ledger_customer()
    captured = _capture(monkeypatch)
    app = create_app()
    with app.app_context():
        pdf.generate_account_ledger_pdf(customer_id, "2026-09-20", "2026-09-30")
    html = captured["html"]
    assert "区间起始欠款" in html
    assert "区间截止欠款" in html
    assert "¥500.00" in html  # 起始
    assert "¥575.00" in html  # 截止
    assert "对账单 2026-09-20 ~ 2026-09-30" in html
    # 对外 PDF 不含成本/毛利/库存哨兵
    for sentinel in ("unit_cost", "毛利润", "库存金额"):
        assert sentinel not in html


def test_pdf_title_without_range_says_all():
    customer_id = _make_ledger_customer()
    ledger = accounting.get_customer_account_ledger(customer_id, "", "")
    from erp.utils.pdf import _account_ledger_print_context

    ctx = _account_ledger_print_context(ledger)
    assert ctx["doc_title"] == "客户账款流水（全部）"


def test_pdf_endpoint_still_returns_a4_portrait():
    from pdf_test_utils import assert_a4_portrait_pdf

    customer_id = _make_ledger_customer()
    response = create_app().test_client().get(
        "/accounts/ledger_pdf",
        query_string={"customer_id": customer_id, "start_date": "2026-09-20", "end_date": "2026-09-30"},
    )
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert_a4_portrait_pdf(response.data)
