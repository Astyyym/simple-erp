from datetime import date, timedelta

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, void_order


def _create_order(customer_id, order_no, price, order_date, *, status="saved", order_type="sale"):
    return create_order_from_typed_rows(
        customer_id,
        order_no,
        [{"product_name": order_no, "unit": "个", "unit_price_yuan": price, "quantity": "1"}],
        status=status,
        order_date=order_date,
        order_type=order_type,
    )


def test_workbench_shows_today_signed_totals_and_separate_valid_order_counts():
    init_db()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    customer_id = create_customer("工作台统计测试客户")

    _create_order(customer_id, "WB-TODAY-SALE-1", "10.00", today, status="saved")
    printed_id = _create_order(customer_id, "WB-TODAY-SALE-2", "20.00", today, status="saved")
    _create_order(customer_id, "WB-TODAY-RETURN", "5.00", today, status="saved", order_type="return")
    _create_order(customer_id, "WB-YESTERDAY-SALE", "100.00", yesterday, status="saved")
    _create_order(customer_id, "WB-TODAY-DRAFT", "90.00", today, status="draft")
    void_id = _create_order(customer_id, "WB-TODAY-VOID", "80.00", today, status="saved")
    deleted_id = _create_order(customer_id, "WB-TODAY-DELETED", "70.00", today, status="saved")

    with get_db() as conn:
        conn.execute("UPDATE orders SET status='printed' WHERE id=?", (printed_id,))
        conn.execute("UPDATE orders SET deleted_at=? WHERE id=?", (today, deleted_id))
    void_order(void_id, "工作台测试作废")

    response = create_app().test_client().get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "今日净业务额" in html
    assert "¥25.00" in html
    assert 'id="salesCount">2 笔' in html
    assert 'id="returnsCount">1 笔' in html


def test_workbench_recent_five_uses_created_time_across_order_dates_and_excludes_invalid_rows():
    init_db()
    today_date = date.today()
    today = today_date.isoformat()
    yesterday = (today_date - timedelta(days=1)).isoformat()
    two_days_ago = (today_date - timedelta(days=2)).isoformat()
    customer_id = create_customer("工作台最近单据测试客户")

    valid_orders = [
        ("WB-RECENT-1", two_days_ago, f"{today} 14:00:00"),
        ("WB-RECENT-2", today, f"{today} 13:00:00"),
        ("WB-RECENT-3", yesterday, f"{today} 12:00:00"),
        ("WB-RECENT-4", yesterday, f"{yesterday} 23:00:00"),
        ("WB-RECENT-5", two_days_ago, f"{yesterday} 20:00:00"),
        ("WB-TOO-OLD", today, f"{yesterday} 19:00:00"),
    ]
    created_ids = [
        _create_order(customer_id, order_no, "10.00", order_date, status="saved")
        for order_no, order_date, _ in valid_orders
    ]
    draft_id = _create_order(customer_id, "WB-INVALID-DRAFT", "10.00", today, status="draft")
    void_id = _create_order(customer_id, "WB-INVALID-VOID", "10.00", today, status="saved")
    deleted_id = _create_order(customer_id, "WB-INVALID-DELETED", "10.00", today, status="saved")
    void_order(void_id, "最近单据测试作废")

    with get_db() as conn:
        for order_id, (_, _, created_at) in zip(created_ids, valid_orders):
            conn.execute("UPDATE orders SET created_at=? WHERE id=?", (created_at, order_id))
        conn.execute("UPDATE orders SET created_at=? WHERE id=?", (f"{today} 15:00:00", draft_id))
        conn.execute("UPDATE orders SET created_at=? WHERE id=?", (f"{today} 14:30:00", void_id))
        conn.execute("UPDATE orders SET created_at=?, deleted_at=? WHERE id=?", (f"{today} 14:15:00", today, deleted_id))

    response = create_app().test_client().get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "最近业务" in html
    recent_rows = html.split('<tbody id="recentRows">', 1)[1].split("</tbody>", 1)[0]
    assert recent_rows.count("<tr") == 5
    recent_order_nos = [
        order_no
        for order_no, _, _ in valid_orders
        if order_no in recent_rows
    ]
    assert recent_order_nos == ["WB-RECENT-1", "WB-RECENT-2", "WB-RECENT-3", "WB-RECENT-4", "WB-RECENT-5"]
    assert "WB-TOO-OLD" not in recent_rows
    assert "WB-INVALID-DRAFT" not in recent_rows
    assert "WB-INVALID-VOID" not in recent_rows
    assert "WB-INVALID-DELETED" not in recent_rows


def test_workbench_hides_fixed_sidebar_at_compact_viewport():
    init_db()
    response = create_app().test_client().get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "@media(max-width:760px){.sidebar{display:none}" in html
    assert ".page-topbar .company{min-width:0;max-width:45%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" in html
    assert 'href="/orders/new"' in html
    assert 'href="/orders/return/new"' in html


def test_workbench_recent_table_avoids_vertical_inner_scrollbar():
    init_db()
    html = create_app().test_client().get("/").get_data(as_text=True)

    assert ".recent-table-wrap{overflow-x:auto;overflow-y:hidden}" in html
