"""Batch B：导入扩列 + 预检 + 同名「更新」分支 + 总额自检 的专项回归。

补 `test_master_data_import.py` 未覆盖的 B-6/B-7 与同名「更新」分支。
2026-10-16（决策 ⑥）：库存系统整体下线，原「期初联动 / 期初缺成本报错 / 期初幂等 /
商品期初金额合计」四个用例的前提消失，改为钉住新行为——含「期初数量 / 期初成本」列的文件
整表报「表头错误」且零写入，商品预检页不再有期初合计核对行。
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


def test_product_import_rejects_initial_stock_columns_as_header_error():
    """决策 ⑥（2026-10-16）：库存系统已删除，含「期初数量 / 期初成本」列的文件整表报「表头错误」。

    旧用例断言「导入期初列会把商品启用库存并写入期初数量/期初成本」——该行为随库存系统整体
    下线；改为钉住新行为：整表被表头白名单挡下、零写入，且当前模板的表头仍能回导。
    """
    init_db()
    csv = "商品名称,品牌,单位,期初数量,期初成本（元）,备注\n带期初商品,金枪,个,10,8.5,\n".encode("utf-8-sig")
    client = create_app().test_client()
    resp = _upload(client, "/products/import", "p.csv", csv)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400
    assert "表头错误" in html
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM products WHERE name='带期初商品'").fetchone() is None

    # 表头白名单收窄后，当前模板的表头必须还能导回来。
    current = "商品名称,品牌,单位,备注\n带期初商品,金枪,个,\n".encode("utf-8-sig")
    assert _upload(client, "/products/import", "p2.csv", current).status_code == 200


def test_legacy_initial_stock_template_header_is_rejected_not_silently_dropped():
    """旧下载模板（含「默认价 / 期初数量 / 期初成本」）现在整表报错，不是静默丢列。

    旧用例断言「填了期初数量却没填期初成本 → 逐行报错、零写入」；期初列已不再被识别，
    改为钉住更强的保证：整个文件在表头阶段就被挡下，一条也不会落库。
    """
    init_db()
    csv = "商品名称,单位,默认价,期初数量,期初成本（元）,备注\n缺成本商品,个,10,5,,\n".encode("utf-8-sig")
    client = create_app().test_client()
    resp = _upload(client, "/products/import", "p.csv", csv)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400
    assert "表头错误" in html
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM products WHERE name='缺成本商品'").fetchone() is None


def test_product_import_repeated_confirm_does_not_duplicate_rows():
    """重复提交同一份预览不会写出重复档案（旧用例的幂等目标，去掉期初后仍成立）。"""
    init_db()
    csv = "商品名称,品牌,单位,备注\n幂等商品,金枪,个,\n".encode("utf-8-sig")
    client = create_app().test_client()
    preview = _upload(client, "/products/import", "p.csv", csv)
    pj = _preview_json(preview)
    client.post("/products/import/confirm", data={"preview_json": pj, "same_name": "skip"}, follow_redirects=True)
    # 再次确认同一预览（模拟用户重复提交）
    client.post("/products/import/confirm", data={"preview_json": pj, "same_name": "skip"}, follow_redirects=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products WHERE name='幂等商品'").fetchone()[0] == 1
        assert conn.execute("SELECT origin FROM products WHERE name='幂等商品'").fetchone()[0] == "import"


def test_product_import_preview_has_no_initial_stock_totals():
    """决策 ⑥：商品导入预检页不再有「期初库存金额合计」核对行（库存系统下线）。

    客户导入的「客户欠款合计」核对行属于对账口径、照旧保留（同一条里对照断言）。
    """
    init_db()
    client = create_app().test_client()
    product_csv = (
        "商品名称,品牌,单位,备注\n"
        "合计商品甲,金枪,个,\n"
        "合计商品乙,空手,件,\n"
    ).encode("utf-8-sig")
    product_html = _upload(client, "/products/import", "p.csv", product_csv).get_data(as_text=True)
    assert "将新增：2" in product_html
    assert "期初库存金额合计" not in product_html
    assert "核对用合计" not in product_html

    customer_csv = "客户名称,电话,地址,期初余额（元）,备注\n欠款客户甲,138,杭州,500,\n".encode("utf-8-sig")
    customer_html = _upload(client, "/customers/import", "c.csv", customer_csv).get_data(as_text=True)
    assert "客户欠款合计" in customer_html


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
