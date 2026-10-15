"""Batch D：区间两端余额（起始欠款 / 截止欠款）。

服务层级口径（`get_customer_account_ledger`）的测试全部保留——账款管理页与
往来交易对账单（2026-10-14 起合并收款/调整/余额两段式）都复用同一口径。
原「PDF 含两端余额与动态标题 / 全部标题 / A4 端点」三条已随打印账款流水 PDF
删除，迁到对账快照测试中以新纸面覆盖（见 test_customer_reconciliation.py）。
"""

from __future__ import annotations

from erp.db import get_db, init_db
from erp.services import accounting


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
