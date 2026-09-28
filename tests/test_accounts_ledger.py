from datetime import datetime, timezone

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services import accounting


def test_customer_account_ledger_separates_current_balance_from_filtered_voidable_events():
    init_db()
    customer_id = accounting.create_customer("账款流水测试客户", opening_balance_cents=20000)

    accounting.create_order_from_typed_rows(
        customer_id,
        "LEDGER-AUG-SALE",
        [{"product_name": "旧销售", "unit": "个", "unit_price_yuan": "400", "quantity": "1"}],
        status="saved",
        order_date="2026-08-20",
    )
    accounting.create_order_from_typed_rows(
        customer_id,
        "LEDGER-SEP-SALE",
        [{"product_name": "消防箱", "unit": "个", "unit_price_yuan": "120", "quantity": "1"}],
        status="saved",
        order_date="2026-09-24",
    )
    accounting.create_order_from_typed_rows(
        customer_id,
        "LEDGER-SEP-RETURN",
        [{"product_name": "退回配件", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-09-23",
        order_type="return",
    )
    void_order_id = accounting.create_order_from_typed_rows(
        customer_id,
        "LEDGER-SEP-VOID",
        [{"product_name": "作废销售", "unit": "个", "unit_price_yuan": "70", "quantity": "1"}],
        status="saved",
        order_date="2026-09-21",
    )
    accounting.void_order(void_order_id, "重复开单")
    accounting.create_order_from_typed_rows(
        customer_id,
        "LEDGER-SEP-DRAFT",
        [{"product_name": "草稿不计账", "unit": "个", "unit_price_yuan": "90", "quantity": "1"}],
        status="draft",
        order_date="2026-09-25",
    )

    accounting.add_payment(customer_id, 15000, payment_date="2026-08-26", method="现金")
    accounting.add_payment(customer_id, 5000, payment_date="2026-09-22", method="转账")
    void_payment_id = accounting.add_payment(customer_id, 2000, payment_date="2026-09-18", method="现金")
    accounting.void_payment(void_payment_id, "重复登记")

    active_adjustment_id = accounting.add_adjustment(customer_id, 500, "差额补记")
    void_adjustment_id = accounting.add_adjustment(customer_id, -300, "错误调整")
    with get_db() as conn:
        conn.execute(
            "UPDATE adjustments SET created_at=? WHERE id=?",
            ("2026-09-25 12:00:00", active_adjustment_id),
        )
        conn.execute(
            "UPDATE adjustments SET status='void', void_reason=?, created_at=? WHERE id=?",
            ("原因已留痕", "2026-09-25 12:00:00", void_adjustment_id),
        )

    ledger = accounting.get_customer_account_ledger(customer_id, "2026-09-01", "2026-09-30")

    assert ledger["opening_balance_cents"] == 20000
    assert ledger["document_net_cents"] == 49000
    assert ledger["adjustment_cents"] == 500
    assert ledger["payment_cents"] == 20000
    assert ledger["current_balance_cents"] == 49500
    assert ledger["period"]["net_change_cents"] == 4500
    assert ledger["period"]["sales_cents"] == 12000
    assert ledger["period"]["returns_cents"] == -3000
    assert ledger["period"]["payments_cents"] == -5000
    assert ledger["period"]["adjustments_cents"] == 500

    entry_types = [entry["type"] for entry in ledger["entries"]]
    assert sorted(entry_types) == sorted(["sale", "return", "sale", "payment", "payment", "adjustment", "adjustment"])
    assert all(entry["date"] >= "2026-09-01" for entry in ledger["entries"])
    assert all(entry["date"] <= "2026-09-30" for entry in ledger["entries"])
    assert "草稿不计账" not in [entry["description"] for entry in ledger["entries"]]

    void_entries = [entry for entry in ledger["entries"] if entry["status"] == "void"]
    assert len(void_entries) == 3
    assert all(entry["balance_delta_cents"] == 0 for entry in void_entries)
    assert {entry["void_reason"] for entry in void_entries} == {"重复开单", "重复登记", "原因已留痕"}


def test_void_adjustment_requires_reason_is_audited_and_cannot_be_repeated():
    init_db()
    customer_id = accounting.create_customer("调整作废客户", opening_balance_cents=10000)
    adjustment_id = accounting.add_adjustment(customer_id, 1500, "差额补记")

    with pytest.raises(ValueError, match="作废原因不能为空"):
        accounting.void_adjustment(adjustment_id, "  ")
    assert accounting.customer_balance_cents(customer_id) == 11500

    accounting.void_adjustment(adjustment_id, "金额录错")

    assert accounting.customer_balance_cents(customer_id) == 10000
    ledger = accounting.get_customer_account_ledger(customer_id)
    void_entry = next(entry for entry in ledger["entries"] if entry["source_id"] == adjustment_id)
    assert void_entry["status"] == "void"
    assert void_entry["void_reason"] == "金额录错"
    assert void_entry["balance_delta_cents"] == 0

    with pytest.raises(ValueError, match="已作废"):
        accounting.void_adjustment(adjustment_id, "重复提交")
    with get_db() as conn:
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='void_adjustment' AND target_id=?",
            (str(adjustment_id),),
        ).fetchone()[0]
    assert audit_count == 1


def test_void_payment_cannot_be_repeated_or_audited_twice():
    init_db()
    customer_id = accounting.create_customer("收款作废客户", opening_balance_cents=5000)
    payment_id = accounting.add_payment(customer_id, 1200, payment_date="2026-09-20")

    accounting.void_payment(payment_id, "重复收款")

    with pytest.raises(ValueError, match="已作废"):
        accounting.void_payment(payment_id, "重复提交")
    assert accounting.customer_balance_cents(customer_id) == 5000
    with get_db() as conn:
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='void_payment' AND target_id=?",
            (str(payment_id),),
        ).fetchone()[0]
    assert audit_count == 1


def test_accounts_page_shows_current_balance_and_filtered_mixed_ledger():
    init_db()
    customer_id = accounting.create_customer("流水页面客户", opening_balance_cents=10000)
    accounting.create_order_from_typed_rows(
        customer_id,
        "PAGE-AUG-SALE",
        [{"product_name": "历史水带", "unit": "卷", "unit_price_yuan": "400", "quantity": "1"}],
        status="saved",
        order_date="2026-08-20",
    )
    accounting.create_order_from_typed_rows(
        customer_id,
        "PAGE-SEP-SALE",
        [{"product_name": "消火栓箱", "unit": "台", "unit_price_yuan": "120", "quantity": "1"}],
        status="saved",
        order_date="2026-09-24",
    )
    accounting.create_order_from_typed_rows(
        customer_id,
        "PAGE-SEP-RETURN",
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

    response = create_app().test_client().get(
        f"/accounts/?customer_id={customer_id}&start_date=2026-09-20&end_date=2026-09-30"
    )
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "当前余额" in html
    assert "筛选区间净变动" in html
    assert "全历史 · 不受日期筛选影响" in html
    assert "¥575.00" in html
    assert "+¥75.00" in html
    assert "PAGE-SEP-SALE" in html
    assert "PAGE-SEP-RETURN" in html
    assert "PAGE-AUG-SALE" not in html
    assert f"/accounts/ledger_pdf?customer_id={customer_id}" in html


def test_payment_void_route_records_reason_and_preserves_ledger_filters():
    init_db()
    customer_id = accounting.create_customer("收款作废路由客户", opening_balance_cents=5000)
    payment_id = accounting.add_payment(customer_id, 1200, payment_date="2026-09-22")
    client = create_app().test_client()

    response = client.post(
        f"/accounts/payment/{payment_id}/void",
        data={
            "reason": "重复登记",
            "customer_id": str(customer_id),
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/accounts/?customer_id={customer_id}&start_date=2026-09-01&end_date=2026-09-30"
    )
    with get_db() as conn:
        payment = conn.execute(
            "SELECT status, void_reason FROM payments WHERE id=?",
            (payment_id,),
        ).fetchone()
    assert payment["status"] == "void"
    assert payment["void_reason"] == "重复登记"
    assert accounting.customer_balance_cents(customer_id) == 5000


def test_browser_ledger_pdf_opens_new_tab_without_leaving_the_account_page():
    init_db()
    customer_id = accounting.create_customer("账款 PDF 浏览器客户")
    html = create_app().test_client().get(f"/accounts/?customer_id={customer_id}").get_data(as_text=True)

    link_start = html.index('id="ledgerPdfLink"')
    link_end = html.index(">", link_start)
    link_tag = html[link_start:link_end]
    assert 'target="_blank"' in link_tag
    assert 'rel="noopener"' in link_tag


def test_adjustment_filter_uses_local_date_for_utc_created_at_near_midnight():
    init_db()
    customer_id = accounting.create_customer("调整本地日期客户")
    adjustment_id = accounting.add_adjustment(customer_id, 1000, "本地日期边界")
    local_time = datetime(2026, 9, 26, 0, 30).astimezone()
    stored_utc = local_time.astimezone(timezone.utc).replace(tzinfo=None)
    with get_db() as conn:
        conn.execute(
            "UPDATE adjustments SET created_at=? WHERE id=?",
            (stored_utc.strftime("%Y-%m-%d %H:%M:%S"), adjustment_id),
        )

    local_date = local_time.date().isoformat()
    ledger = accounting.get_customer_account_ledger(customer_id, local_date, local_date)

    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["date"] == local_date
    assert ledger["period"]["adjustments_cents"] == 1000
