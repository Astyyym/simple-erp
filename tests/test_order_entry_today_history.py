from datetime import date, timedelta

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _order(customer_id, number, business_date, *, order_type="sale", status="saved"):
    return create_order_from_typed_rows(
        customer_id,
        number,
        [{"product_name": "测试阀门", "unit": "个", "quantity": "1", "unit_price_yuan": "12.50"}],
        status=status,
        order_date=business_date,
        order_type=order_type,
    )


@pytest.mark.parametrize("path", ["/orders/new", "/orders/return/new"])
def test_new_order_history_uses_business_date_for_both_entry_pages(path):
    init_db()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    customer = create_customer("当天历史演示客户")
    old_id = _order(customer, "HIST-YESTERDAY", yesterday)
    today_sale = _order(customer, "HIST-TODAY-SALE", today)
    today_return = _order(customer, "HIST-TODAY-RETURN", today, order_type="return")
    _order(customer, "HIST-TODAY-DRAFT", today, status="draft")
    void_id = _order(customer, "HIST-TODAY-VOID", today)
    deleted_id = _order(customer, "HIST-TODAY-DELETED", today)
    with get_db() as conn:
        conn.execute("UPDATE orders SET created_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?", (old_id,))
        conn.execute("UPDATE orders SET created_at='2020-01-01 12:00:00' WHERE id=?", (today_sale,))
        conn.execute("UPDATE orders SET status='void' WHERE id=?", (void_id,))
        conn.execute("UPDATE orders SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (deleted_id,))
    response = create_app().test_client().get(path)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "当天历史开单" in html
    history = html.split('id="todayHistoryRows"', 1)[1].split("</tbody>", 1)[0]
    assert history.index("HIST-TODAY-RETURN") < history.index("HIST-TODAY-SALE")
    for absent in ("HIST-YESTERDAY", "HIST-TODAY-DRAFT", "HIST-TODAY-VOID", "HIST-TODAY-DELETED"):
        assert absent not in history
    assert f'/orders/{today_sale}' in history
    assert f'/orders/{today_sale}/edit' in history
    assert f'/orders/{today_return}/edit' in history
    assert "退货单" in history and "销售单" in history
    assert "12.50" in history and "-12.50" in history


def test_today_history_refresh_after_async_save_excludes_backdated_orders():
    init_db()
    client = create_app().test_client()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for business_date in (today, yesterday):
        response = client.post(
            "/orders/create",
            data={
                "customer_name": "当天历史新增客户",
                "order_date": business_date,
                "status": "saved",
                "product_name": ["测试商品"],
                "unit": ["个"],
                "unit_price": ["12.50"],
                "quantity": ["1"],
                "save_action": "save_print",
            },
            headers={"X-Requested-With": "fetch"},
        )
        assert response.status_code == 200
        if business_date == today:
            today_number = response.json["order_no"]
        else:
            yesterday_number = response.json["order_no"]
    result = client.get("/orders/api/today_history")
    assert result.status_code == 200
    rows = result.get_json()["orders"]
    assert [order["order_no"] for order in rows] == [today_number]
    assert all(order["order_date"] == today for order in rows)
    assert yesterday_number not in [order["order_no"] for order in rows]
    html = client.get("/orders/new").get_data(as_text=True)
    assert "refreshTodayHistory" in html
    assert "beforeunload" in html


def test_edit_page_does_not_show_today_history():
    init_db()
    customer = create_customer("当天历史编辑客户")
    order_id = _order(customer, "HIST-EDIT", date.today().isoformat())
    html = create_app().test_client().get(f"/orders/{order_id}/edit").get_data(as_text=True)
    assert "当天历史开单" not in html


def test_today_history_empty_state():
    init_db()
    html = create_app().test_client().get("/orders/new").get_data(as_text=True)
    assert "当天历史开单" in html
    assert "今天还没有正式保存或已打印的单据" in html


def test_editing_yesterdays_order_does_not_move_it_into_todays_history():
    init_db()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    customer = create_customer("补录重编辑客户")
    order_id = _order(customer, "HIST-PAST-EDIT", yesterday)
    client = create_app().test_client()
    result = client.post(
        f"/orders/{order_id}/edit",
        data={
            "customer_id": str(customer),
            "customer_name": "补录重编辑客户",
            "order_no": "HIST-TAMPERED",
            "order_date": today,
            "status": "saved",
            "product_name": ["测试阀门"],
            "unit": ["个"],
            "unit_price": ["18.00"],
            "quantity": ["1"],
        },
    )
    assert result.status_code == 302
    with get_db() as conn:
        order = conn.execute("SELECT order_date, order_no FROM orders WHERE id=?", (order_id,)).fetchone()
    assert order["order_date"] == yesterday
    assert order["order_no"] == "HIST-PAST-EDIT"
    assert client.get("/orders/api/today_history").get_json()["orders"] == []
