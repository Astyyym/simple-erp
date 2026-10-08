"""Batch C：保存/打印拆分 + 已打印自动标记（2026-10-08 第二轮修复）。

覆盖审查证据 #6/#7：
- 四个新建开单页都有「保存」与「保存并打印」两个按钮
- 「保存」不标记已打印；「保存并打印」（非草稿）→ status=printed、print_count+1
- 草稿 + 保存并打印 → 保持草稿、不标记
- 第二次保存并打印 → print_count=2
- 已删除无引用的 confirm_print 死路由
"""
from typing import Any

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product
from erp.services.inventory import initialize_product


def _seed_product(spec="C", price=2000):
    pid = create_product("保存打印商品", spec, "个", price)
    initialize_product(pid, "10", "10", "2026-10-01", "系统上线期初", f"batchc-{spec}", confirm_zero=False)
    return pid


def _post(client, route: str, customer_id: int, name: str, *, action: str, status: str = "saved", date: str = "2026-10-07"):
    return client.post(
        route,
        data={
            "customer_id": str(customer_id),
            "customer_name": name,
            "order_date": date,
            "status": status,
            "product_name": ["保存打印商品"],
            "unit": ["个"],
            "unit_price": ["30"],
            "quantity": ["1"],
            "save_action": action,
        },
        headers={"X-Requested-With": "fetch"},
    )


@pytest.mark.parametrize("route", ["/orders/create", "/orders/return/create"])
def test_new_entry_pages_expose_save_and_save_print_buttons(route):
    init_db()
    client = create_app().test_client()
    page = client.get("/orders/return/new" if "return" in route else "/orders/new").get_data(as_text=True)
    assert 'id="saveButton" name="save_action" value="save"' in page
    assert 'name="save_action" value="save_print"' in page
    assert "保存并打印" in page


@pytest.mark.parametrize("route", ["/purchases/new", "/purchases/return/new"])
def test_new_purchase_entry_pages_expose_save_and_save_print_buttons(route):
    init_db()
    page = create_app().test_client().get(route).get_data(as_text=True)
    assert 'id="saveButton" name="save_action" value="save"' in page
    assert 'name="save_action" value="save_print"' in page
    assert "保存并打印" in page


def test_plain_save_does_not_mark_printed():
    init_db()
    customer_id = create_customer("只保存客户")
    _seed_product("PLAIN")
    client = create_app().test_client()
    response = _post(client, "/orders/create", customer_id, "只保存客户", action="save")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True and payload["printed"] is False
    with get_db() as conn:
        row = conn.execute("SELECT status, print_count FROM orders WHERE id=?", (payload["order_id"],)).fetchone()
    assert row["status"] == "saved"
    assert row["print_count"] == 0


def test_save_and_print_marks_printed_and_counts_once():
    init_db()
    customer_id = create_customer("保存打印客户")
    _seed_product("PRINT")
    client = create_app().test_client()
    response = _post(client, "/orders/create", customer_id, "保存打印客户", action="save_print")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["printed"] is True
    with get_db() as conn:
        row = conn.execute("SELECT status, print_count FROM orders WHERE id=?", (payload["order_id"],)).fetchone()
    assert row["status"] == "printed"
    assert row["print_count"] == 1


def test_draft_save_and_print_stays_draft_without_marking():
    init_db()
    customer_id = create_customer("草稿打印客户")
    _seed_product("DRAFT")
    client = create_app().test_client()
    response = _post(client, "/orders/create", customer_id, "草稿打印客户", action="save_print", status="draft")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["printed"] is False
    with get_db() as conn:
        row = conn.execute("SELECT status, print_count FROM orders WHERE id=?", (payload["order_id"],)).fetchone()
    assert row["status"] == "draft"
    assert row["print_count"] == 0


def test_second_save_and_print_increments_print_count():
    init_db()
    customer_id = create_customer("重复打印客户")
    product_id = _seed_product("TWICE")
    client = create_app().test_client()
    payload = _post(client, "/orders/create", customer_id, "重复打印客户", action="save_print").get_json()
    # 用同一条链路的服务层再标记一次（等价于第二次「保存并打印」），count 必须 +1。
    from erp.services.accounting import mark_order_printed
    mark_order_printed(payload["order_id"])
    with get_db() as conn:
        row = conn.execute("SELECT status, print_count FROM orders WHERE id=?", (payload["order_id"],)).fetchone()
    assert row["status"] == "printed"
    assert row["print_count"] == 2


def test_confirm_print_dead_route_is_removed():
    init_db()
    client = create_app().test_client()
    response = client.post("/orders/1/confirm_print")
    # 路由已删除 → 落到全局 404 中文页（不再是一个静默生效的死端点）。
    assert response.status_code == 404
