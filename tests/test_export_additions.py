"""1-D Excel 导出补全：拿货 / 退拿货 / 账款流水。"""
from __future__ import annotations

import io
from uuid import uuid4

from openpyxl import load_workbook

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.purchases import create_purchase_order
from erp.services import purchase_returns


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _rows(response):
    assert response.status_code == 200
    assert XLSX in response.content_type
    assert "attachment" in response.headers.get("Content-Disposition", "")
    workbook = load_workbook(io.BytesIO(response.data), read_only=True, data_only=True)
    return list(workbook.active.iter_rows(values_only=True))


def test_purchase_and_return_excel_exports():
    init_db()
    suffix = uuid4().hex[:6]
    customer_id = create_customer(f"拿货对象{suffix}")
    product_name = f"拿货商品{suffix}"
    create_product(product_name, "个")
    pid = None
    with get_db() as conn:
        pid = conn.execute("SELECT id FROM products WHERE name=?", (product_name,)).fetchone()["id"]

    create_purchase_order(
        f"NH20260101{suffix[:4]}",
        "2026-01-01",
        [{"product_id": pid, "product_name": product_name, "unit": "个", "quantity": "2", "unit_cost_yuan": "10.00"}],
        customer_id=customer_id,
        request_key=f"pk-{suffix}",
        status="saved",
    )
    purchase_returns.create(
        customer_id=customer_id,
        business_date="2026-01-03",
        items=[{"product_id": pid, "product_name": product_name, "unit": "个", "quantity": "1", "unit_price_yuan": "5.00"}],
        request_key=f"rk-{suffix}",
        status="saved",
    )

    client = create_app().test_client()

    purchase_rows = _rows(client.get("/orders/export/purchases.xlsx"))
    assert purchase_rows[0][0] == "单号"
    assert "往来对象" in purchase_rows[0]
    assert "单价（元）" in purchase_rows[0]
    body = [row for row in purchase_rows[1:] if row[0] and str(row[0]).startswith("NH")]
    assert body, "应导出刚建的拿货单明细"
    row = body[0]
    assert row[2] == f"拿货对象{suffix}"
    assert row[5] == product_name
    assert row[7] == "2"           # 数量
    assert row[8] == "10.00"       # 单价（分→元）
    assert row[9] == "20.00"       # 金额
    assert row[11] == "20.00"      # 单据合计

    return_rows = _rows(client.get("/orders/export/purchase_returns.xlsx"))
    assert return_rows[0][0] == "单号"
    ret_body = [r for r in return_rows[1:] if r[0] and str(r[0]).startswith("NH")]
    assert ret_body
    assert ret_body[0][8] == "5.00"


def test_accounts_ledger_excel_export():
    init_db()
    suffix = uuid4().hex[:6]
    customer_id = create_customer(f"流水客户{suffix}")
    create_order_from_typed_rows(
        customer_id,
        f"MD20260102{suffix[:4]}",
        [{"product_name": f"流水商品{suffix}", "unit": "个", "unit_price_yuan": "100.00", "quantity": "2"}],
        status="saved",
        order_date="2026-01-02",
        order_type="sale",
    )
    client = create_app().test_client()
    response = client.get(f"/accounts/export.xlsx?customer_id={customer_id}")
    rows = _rows(response)
    assert rows[0][0] == "日期"
    assert "发生金额（元）" in rows[0]
    assert "余额影响（元）" in rows[0]
    data = [r for r in rows[1:] if r[0] == "2026-01-02"]
    assert data, "应导出该客户的销售流水"
    assert data[0][1] == "销售单"
    assert data[0][4] == "200.00"   # 发生金额
    assert data[0][5] == "200.00"   # 余额影响（销售为正向）


def test_accounts_ledger_export_requires_customer():
    init_db()
    client = create_app().test_client()
    response = client.get("/accounts/export.xlsx")
    assert response.status_code == 400
