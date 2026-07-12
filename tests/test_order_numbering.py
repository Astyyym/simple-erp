from datetime import date
from uuid import uuid4

from erp import create_app
from erp.db import get_db, init_db
from erp.routes.orders import next_order_no
from erp.services.accounting import create_customer, create_order_from_typed_rows


def test_next_order_no_uses_md_yyyymmdd_and_four_digit_sequence():
    init_db()
    customer_id = create_customer("单号客户")
    create_order_from_typed_rows(
        customer_id,
        "MD202607100001",
        [{"product_name": "阀门", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}],
        status="saved",
        order_date="2026-07-10",
        order_type="sale",
    )
    create_order_from_typed_rows(
        customer_id,
        "MD202607100002",
        [{"product_name": "阀门退", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}],
        status="saved",
        order_date="2026-07-10",
        order_type="return",
    )
    with get_db() as conn:
        assert next_order_no(conn, "2026-07-10") == "MD202607100003"


def test_new_order_page_defaults_today_allows_date_edit_and_locks_order_no():
    init_db()
    today = date.today()
    expected_prefix = f"MD{today.strftime('%Y%m%d')}"
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert expected_prefix in html
    assert 'id="orderDateInput"' in html
    assert 'id="orderNoInput"' in html and "readonly" in html
    assert "默认当天，可改" in html
    assert "next_order_no?date=" in html
    # Date field itself is editable on create (no readonly on orderDateInput line).
    date_line = [line for line in html.splitlines() if 'id="orderDateInput"' in line][0]
    assert "readonly" not in date_line


def test_create_uses_selected_date_and_ignores_client_order_no():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"选日客户{suffix}"
    customer_id = create_customer(customer_name)
    client = create_app().test_client()
    response = client.post(
        "/orders/create",
        data={
            "customer_id": str(customer_id),
            "customer_name": customer_name,
            "order_no": "HACK-SHOULD-NOT-USE",
            "order_date": "2026-07-10",
            "status": "saved",
            "product_name": ["阀门"],
            "unit": ["个"],
            "unit_price": ["2"],
            "quantity": ["1"],
        },
    )
    assert response.status_code == 302
    with get_db() as conn:
        row = conn.execute(
            "SELECT order_no, order_date FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
    assert row is not None
    assert row["order_date"] == "2026-07-10"
    assert row["order_no"] == "MD202607100001"
    assert row["order_no"] != "HACK-SHOULD-NOT-USE"


def test_api_next_order_no_follows_date():
    init_db()
    customer_id = create_customer("预览单号客户")
    create_order_from_typed_rows(
        customer_id,
        "MD202607100001",
        [{"product_name": "阀门", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}],
        status="saved",
        order_date="2026-07-10",
    )
    client = create_app().test_client()
    payload = client.get("/orders/api/next_order_no", query_string={"date": "2026-07-10"}).get_json()
    assert payload["order_date"] == "2026-07-10"
    assert payload["order_no"] == "MD202607100002"


def test_sale_and_return_share_daily_md_sequence_for_selected_date():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"流水共用{suffix}"
    customer_id = create_customer(customer_name)
    client = create_app().test_client()
    client.post(
        "/orders/create",
        data={
            "customer_id": str(customer_id),
            "customer_name": customer_name,
            "order_no": "ignored",
            "order_date": "2026-07-10",
            "status": "saved",
            "product_name": ["A"],
            "unit": ["个"],
            "unit_price": ["1"],
            "quantity": ["1"],
        },
    )
    client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer_id),
            "customer_name": customer_name,
            "order_no": "ignored2",
            "order_date": "2026-07-10",
            "status": "saved",
            "product_name": ["B"],
            "unit": ["个"],
            "unit_price": ["1"],
            "quantity": ["1"],
        },
    )
    with get_db() as conn:
        nos = [
            row["order_no"]
            for row in conn.execute(
                "SELECT order_no FROM orders WHERE customer_id=? ORDER BY id",
                (customer_id,),
            ).fetchall()
        ]
    assert nos == ["MD202607100001", "MD202607100002"]


def test_re_edit_keeps_original_order_no_and_date():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"重编辑客户{suffix}"
    customer_id = create_customer(customer_name)
    original_no = f"OLD-KEEP-{suffix}"
    order_id = create_order_from_typed_rows(
        customer_id,
        original_no,
        [{"product_name": "原商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
        order_date="2026-06-01",
    )
    client = create_app().test_client()
    edit_page = client.get(f"/orders/{order_id}/edit").get_data(as_text=True)
    assert original_no in edit_page
    date_line = [line for line in edit_page.splitlines() if 'id="orderDateInput"' in line][0]
    assert "readonly" in date_line
    response = client.post(
        f"/orders/{order_id}/edit",
        data={
            "customer_id": str(customer_id),
            "customer_name": customer_name,
            "order_no": "MD202607129999",
            "order_date": "2026-07-12",
            "status": "saved",
            "product_name": ["改后商品"],
            "unit": ["个"],
            "unit_price": ["20"],
            "quantity": ["2"],
        },
    )
    assert response.status_code == 302
    with get_db() as conn:
        row = conn.execute(
            "SELECT order_no, order_date FROM orders WHERE id=?",
            (order_id,),
        ).fetchone()
        item = conn.execute(
            "SELECT product_name, quantity FROM order_items WHERE order_id=?",
            (order_id,),
        ).fetchone()
    assert row["order_no"] == original_no
    assert row["order_date"] == "2026-06-01"
    assert item["product_name"] == "改后商品"
    assert float(item["quantity"]) == 2.0
