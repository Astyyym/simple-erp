import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp import services
from erp.services.accounting import create_customer, create_order_from_typed_rows


def test_new_order_rejects_unsupported_status_without_creating_customer_or_order():
    init_db()
    client = create_app().test_client()

    with get_db() as conn:
        before_customers = conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
        before_orders = conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"]

    response = client.post(
        "/orders/create",
        data={
            "customer_name": "非法状态孤立客户",
            "status": "printed",
            "product_name": ["不应保存的商品"],
            "unit": ["个"],
            "unit_price": ["10"],
            "quantity": ["1"],
        },
    )

    assert response.status_code == 400
    assert "订单状态无效" in response.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"] == before_customers
        assert conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"] == before_orders


def test_order_edit_cannot_void_and_explicit_void_requires_reason():
    init_db()
    customer_id = create_customer("作废路径客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "VOID-PATH-001",
        [{"product_name": "消防阀门", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )
    client = create_app().test_client()
    edit_data = {
        "customer_id": str(customer_id),
        "customer_name": "作废路径客户",
        "status": "void",
        "product_name": ["消防阀门"],
        "unit": ["个"],
        "unit_price": ["10"],
        "quantity": ["1"],
    }

    ordinary_edit = client.post(f"/orders/{order_id}/edit", data=edit_data)
    assert ordinary_edit.status_code == 400

    missing_reason = client.post(f"/orders/{order_id}/void", data={"reason": "   "})
    assert missing_reason.status_code == 400
    with get_db() as conn:
        unchanged = conn.execute("SELECT status, void_reason FROM orders WHERE id=?", (order_id,)).fetchone()
    assert unchanged["status"] == "saved"
    assert unchanged["void_reason"] == ""

    voided = client.post(f"/orders/{order_id}/void", data={"reason": "  客户取消订单  "})
    assert voided.status_code == 302
    with get_db() as conn:
        order = conn.execute("SELECT status, void_reason FROM orders WHERE id=?", (order_id,)).fetchone()
        audit = conn.execute(
            "SELECT action, summary FROM audit_logs WHERE target_type='order' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(order_id),),
        ).fetchone()
    assert order["status"] == "void"
    assert order["void_reason"] == "客户取消订单"
    assert audit["action"] == "void_order"
    assert "客户取消订单" in audit["summary"]
    assert "  客户取消订单  " not in audit["summary"]

    cannot_reedit = client.post(
        f"/orders/{order_id}/edit",
        data={**edit_data, "status": "saved"},
    )
    assert cannot_reedit.status_code == 400
    with get_db() as conn:
        assert conn.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()["status"] == "void"


def test_void_order_removes_order_from_customer_balance():
    init_db()
    customer_id = create_customer("作废余额客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "VOID-BALANCE-001",
        [{"product_name": "消防水带", "unit": "卷", "unit_price_yuan": "25", "quantity": "2"}],
        status="saved",
    )
    assert services.accounting.customer_balance_cents(customer_id) == 5000

    services.accounting.void_order(order_id, "客户取消")

    assert services.accounting.customer_balance_cents(customer_id) == 0


def test_void_order_rolls_back_when_audit_logging_fails(monkeypatch):
    init_db()
    customer_id = create_customer("作废回滚客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "VOID-ROLLBACK-001",
        [{"product_name": "灭火器", "unit": "具", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(services.accounting, "log_action", fail_audit)
    import pytest

    with pytest.raises(RuntimeError, match="audit unavailable"):
        services.accounting.void_order(order_id, "测试回滚")

    with get_db() as conn:
        row = conn.execute("SELECT status, void_reason FROM orders WHERE id=?", (order_id,)).fetchone()
    assert row["status"] == "saved"
    assert row["void_reason"] == ""


def test_invalid_order_detail_does_not_create_customer():
    init_db()
    client = create_app().test_client()

    with get_db() as conn:
        before_customers = conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
        before_orders = conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"]

    response = client.post(
        "/orders/create",
        data={
            "customer_name": "明细错误不应建档客户",
            "status": "saved",
            "product_name": ["无效单价商品"],
            "unit": ["个"],
            "unit_price": ["不是金额"],
            "quantity": ["1"],
        },
    )

    assert response.status_code == 400
    assert "有效数字" in response.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"] == before_customers
        assert conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"] == before_orders


def test_new_and_edit_forms_only_offer_supported_status_actions():
    init_db()
    customer_id = create_customer("状态表单客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "STATUS-FORM-001",
        [{"product_name": "消防阀门", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )
    client = create_app().test_client()
    new_html = client.get("/orders/new").get_data(as_text=True)
    edit_html = client.get(f"/orders/{order_id}/edit").get_data(as_text=True)

    import re

    new_statuses = re.search(r'<select class="form-select form-select-lg" name="status">(.*?)</select>', new_html, re.S).group(1)
    edit_statuses = re.search(r'<select class="form-select form-select-lg" name="status">(.*?)</select>', edit_html, re.S).group(1)
    assert 'value="saved"' in new_statuses and 'value="draft"' in new_statuses
    assert 'value="printed"' not in new_statuses and 'value="void"' not in new_statuses
    assert 'value="printed"' in edit_statuses
    assert 'value="void"' not in edit_statuses

    return_html = client.get("/orders/return/new").get_data(as_text=True)
    assert "const orderSign = -1;" in return_html
    assert "const line = qty * price * orderSign;" in return_html


def test_negative_quantity_is_rejected_before_customer_creation():
    init_db()
    client = create_app().test_client()

    with get_db() as conn:
        before_customers = conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
        before_orders = conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"]

    response = client.post(
        "/orders/return/create",
        data={
            "customer_name": "负数量不应建档客户",
            "status": "saved",
            "product_name": ["不应保存的商品"],
            "unit": ["个"],
            "unit_price": ["10"],
            "quantity": ["-1"],
        },
    )

    assert response.status_code == 400
    assert "数量必须大于 0" in response.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"] == before_customers
        assert conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"] == before_orders


@pytest.mark.parametrize(
    ("unit_prices", "quantities"),
    [
        (["1e100"], ["1"]),
        (["10"], ["1e100"]),
        (["90000000000000000", "90000000000000000"], ["1", "1"]),
    ],
    ids=["unit-price-quantize-overflow", "quantity-subtotal-overflow", "order-total-overflow"],
)
def test_unrepresentable_order_amount_is_rejected_before_customer_creation(unit_prices, quantities):
    init_db()
    client = create_app().test_client()
    with get_db() as conn:
        before_customers = conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
        before_orders = conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"]

    response = client.post(
        "/orders/create",
        data={
            "customer_name": "金额超出范围不应建档客户",
            "status": "saved",
            "product_name": [f"溢出商品{index}" for index in range(len(unit_prices))],
            "unit": ["个"] * len(unit_prices),
            "unit_price": unit_prices,
            "quantity": quantities,
        },
    )

    assert response.status_code == 400
    assert "金额或数量超出支持范围" in response.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"] == before_customers
        assert conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"] == before_orders