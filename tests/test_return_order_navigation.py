import uuid

from erp.db import get_db


def test_sidebar_exposes_sale_and_return_order_shortcuts():
    from erp import create_app

    client = create_app().test_client()

    response = client.get("/orders/new")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'href="/orders/new"' in html
    assert ">销售单<" in html
    assert 'href="/orders/return/new"' in html
    assert ">退货单<" in html


def test_sidebar_has_four_sections_and_a_gap_before_the_workspace():
    from erp import create_app

    response = create_app().test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert ">业务中心<" not in html
    assert 'class="nav-section active" href="/">工作台</a>' in html
    assert 'class="nav-label">基础资料</div>' in html
    assert 'href="/recycle/">回收站</a>' in html
    assert 'href="/settings/">系统设置</a>' in html
    assert html.count('class="nav-divider"') == 3
    assert 'height:calc(100% - 28px);margin:14px;' in html


def test_return_order_page_uses_same_entry_table_with_return_labels():
    from erp import create_app

    client = create_app().test_client()

    response = client.get("/orders/return/new")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "新建退货单" in html
    assert "退货日期" in html
    assert "退货信息" in html
    assert 'name="customer_name"' in html
    assert 'name="product_name"' in html
    assert 'name="unit"' in html
    assert 'name="quantity"' in html
    assert 'name="unit_price"' in html
    assert 'action="/orders/return/create"' in html


def test_return_order_create_records_return_type_and_reduces_customer_balance():
    from datetime import date

    from erp import create_app
    from erp.services.accounting import customer_balance_cents

    client = create_app().test_client()
    suffix = uuid.uuid4().hex[:8]
    customer_name = f"退货客户{suffix}"
    product_name = f"退货商品{suffix}"

    response = client.post(
        "/orders/return/create",
        data={
            "customer_name": customer_name,
            "customer_id": "",
            "order_date": "2026-07-10",
            "order_no": f"RET-{suffix}",
            "status": "saved",
            "product_name": [product_name],
            "unit": ["个"],
            "quantity": ["2"],
            "unit_price": ["50"],
            "notes": "退货测试",
            "save_action": "save",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with get_db() as conn:
        customer = conn.execute("SELECT id FROM customers WHERE name=?", (customer_name,)).fetchone()
        order = conn.execute(
            "SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1",
            (customer["id"],),
        ).fetchone()
    assert order["order_type"] == "return"
    assert order["total_amount_cents"] == -10000
    assert order["order_date"] == "2026-07-10"
    assert order["order_no"] == "MD202607100001"
    assert customer_balance_cents(int(customer["id"])) == -10000
