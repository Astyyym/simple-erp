"""Batch B：导入扩列 + 预检 + 期初联动 + 总额自检 的专项回归。

补 `test_master_data_import.py` 未覆盖的 B-6/B-7 与同名「更新」分支。
"""

from __future__ import annotations

import html as html_module
import io
import re

from erp import create_app
from erp.db import get_db, init_db


def _upload(client, url, filename, content, *, same_name="skip"):
    return client.post(
        url,
        data={"file": (io.BytesIO(content), filename), "same_name": same_name},
        content_type="multipart/form-data",
    )


def _preview_json(resp):
    m = re.search(r'name="preview_json" value="([^"]*)"', resp.get_data(as_text=True))
    assert m
    return html_module.unescape(m.group(1))


def test_product_import_with_initial_stock_initializes_inventory():
    init_db()
    csv = "商品名称,型号,单位,默认价,期初数量,期初成本（元）,备注\n带期初商品,4kg,个,120,10,8.5,\n".encode("utf-8-sig")
    client = create_app().test_client()
    preview = _upload(client, "/products/import", "p.csv", csv)
    assert preview.status_code == 200
    pj = _preview_json(preview)
    confirmed = client.post("/products/import/confirm", data={"preview_json": pj, "same_name": "skip"}, follow_redirects=True)
    assert confirmed.status_code == 200

    with get_db() as conn:
        product = conn.execute("SELECT * FROM products WHERE name='带期初商品'").fetchone()
        state = conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (product["id"],)).fetchone()
        init = conn.execute("SELECT * FROM inventory_initializations WHERE product_id=?", (product["id"],)).fetchone()
    assert product["origin"] == "import"
    assert product["unit"] == "个"
    assert state is not None and state["enabled"] == 1
    assert state["quantity_3dp"] == 10 * 1000
    assert state["cost_total_micro"] == 85000000  # 10 × 8.50 元
    assert init is not None and init["request_key"].startswith("import-")


def test_product_import_quantity_without_cost_is_error_not_silent():
    init_db()
    csv = "商品名称,型号,单位,默认价,期初数量,期初成本（元）,备注\n缺成本商品,,个,10,5,,\n".encode("utf-8-sig")
    client = create_app().test_client()
    resp = _upload(client, "/products/import", "p.csv", csv)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "错误：1" in html
    assert "必须填期初成本" in html
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM products WHERE name='缺成本商品'").fetchone() is None


def test_product_import_repeated_confirm_does_not_double_initialize():
    init_db()
    csv = "商品名称,型号,单位,默认价,期初数量,期初成本（元）,备注\n幂等期初商品,,个,10,3,2,\n".encode("utf-8-sig")
    client = create_app().test_client()
    preview = _upload(client, "/products/import", "p.csv", csv)
    pj = _preview_json(preview)
    client.post("/products/import/confirm", data={"preview_json": pj, "same_name": "skip"}, follow_redirects=True)
    # 再次确认同一预览（模拟用户重复提交）
    client.post("/products/import/confirm", data={"preview_json": pj, "same_name": "skip"}, follow_redirects=True)
    with get_db() as conn:
        product = conn.execute("SELECT id FROM products WHERE name='幂等期初商品'").fetchone()
        state = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product["id"],)).fetchone()
    assert state["quantity_3dp"] == 3000  # 没有被加两次
    assert state["cost_total_micro"] == 6000000  # 3 × 2 元


def test_product_import_totals_shown_for_reconciliation():
    init_db()
    csv = "商品名称,型号,单位,默认价,期初数量,期初成本（元）,备注\n合计商品甲,,个,10,2,5,\n合计商品乙,,个,20,3,4,\n".encode("utf-8-sig")
    client = create_app().test_client()
    resp = _upload(client, "/products/import", "p.csv", csv)
    html = resp.get_data(as_text=True)
    # 2×5 + 3×4 = 22.00 元
    assert "商品期初库存金额合计" in html
    assert "22.00" in html


def test_customer_import_totals_shown_for_reconciliation():
    init_db()
    csv = "客户名称,电话,地址,期初余额（元）,备注\n欠款客户甲,138,杭州,500,\n欠款客户乙,,,1200.50,\n".encode("utf-8-sig")
    client = create_app().test_client()
    resp = _upload(client, "/customers/import", "c.csv", csv)
    html = resp.get_data(as_text=True)
    assert "客户欠款合计" in html
    assert "1700.50" in html


def test_customer_import_update_overwrites_contact_but_keeps_business_rows():
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO customers(name, phone, address, opening_balance_cents) VALUES ('更新客户','old','oldaddr',0)")
    csv = "客户名称,电话,地址,期初余额（元）,备注\n更新客户,13900000000,新地址,300,\n".encode("utf-8-sig")
    client = create_app().test_client()
    preview = _upload(client, "/customers/import", "c.csv", csv, same_name="update")
    html = preview.get_data(as_text=True)
    assert "将更新：1" in html
    pj = _preview_json(preview)
    client.post("/customers/import/confirm", data={"preview_json": pj, "same_name": "update"}, follow_redirects=True)
    with get_db() as conn:
        row = conn.execute("SELECT phone, address, opening_balance_cents FROM customers WHERE name='更新客户'").fetchone()
    assert row["phone"] == "13900000000"
    assert row["address"] == "新地址"
    assert row["opening_balance_cents"] == 30000


def test_preview_does_not_write_anything():
    init_db()
    csv = "客户名称,电话,地址,期初余额（元）,备注\n不应落库客户,1,2,100,\n".encode("utf-8-sig")
    client = create_app().test_client()
    _upload(client, "/customers/import", "c.csv", csv)
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM customers WHERE name='不应落库客户'").fetchone() is None
