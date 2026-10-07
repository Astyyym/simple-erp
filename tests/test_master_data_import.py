from __future__ import annotations

import io
import uuid
from decimal import Decimal

from openpyxl import load_workbook

from erp import create_app
from erp.db import get_db, init_db


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _upload(client, url: str, filename: str, content: bytes):
    return client.post(
        url,
        data={"file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_product_and_customer_excel_templates_have_exact_headers():
    client = create_app().test_client()

    product_response = client.get("/products/import/template")
    customer_response = client.get("/customers/import/template")

    assert product_response.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in product_response.content_type
    assert "attachment" in product_response.headers["Content-Disposition"]
    product_sheet = load_workbook(io.BytesIO(product_response.data), read_only=True).active
    assert list(next(product_sheet.iter_rows(values_only=True))) == ["商品名称", "型号", "价格"]

    assert customer_response.status_code == 200
    customer_sheet = load_workbook(io.BytesIO(customer_response.data), read_only=True).active
    assert list(next(customer_sheet.iter_rows(values_only=True))) == ["客户名称"]


def test_management_pages_show_chinese_import_controls():
    client = create_app().test_client()
    products = client.get("/products/").get_data(as_text=True)
    customers = client.get("/customers/").get_data(as_text=True)

    assert "/products/import" in products and "下载Excel模板" in products and 'accept=".xlsx,.csv"' in products
    assert "/customers/import" in customers and "下载Excel模板" in customers and 'accept=".xlsx,.csv"' in customers


def test_product_csv_import_adds_valid_rows_and_reports_duplicate_and_error():
    init_db()
    existing = _name("已有商品")
    fresh = _name("新增商品")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO products(name, spec, unit, default_price_cents) VALUES (?, '', '箱', 999)",
            (existing,),
        )
    csv_data = f"商品名称,价格\n{fresh},12.34\n{existing},88\n{fresh},20\n空价商品,\n负价商品,-1\n,10\n".encode("utf-8-sig")

    response = _upload(create_app().test_client(), "/products/import", "products.csv", csv_data)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "新增：1" in html and "跳过：2" in html and "错误：3" in html
    assert "第5行" in html and "价格不能为空" in html
    assert "第6行" in html and "价格不能为负数" in html
    assert "第7行" in html and "商品名称不能为空" in html
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE name=?", (fresh,)).fetchone()
        old = conn.execute("SELECT * FROM products WHERE name=?", (existing,)).fetchone()
    assert row["unit"] == "个"
    assert row["default_price_cents"] == 1234
    assert old["default_price_cents"] == 999


def test_customer_xlsx_import_skips_soft_deleted_and_file_duplicates():
    init_db()
    deleted = _name("软删客户")
    fresh = _name("新增客户")
    with get_db() as conn:
        conn.execute("INSERT INTO customers(name, deleted_at) VALUES (?, CURRENT_TIMESTAMP)", (deleted,))

    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["客户名称"])
    sheet.append([fresh])
    sheet.append([deleted])
    sheet.append([fresh])
    output = io.BytesIO()
    workbook.save(output)

    response = _upload(create_app().test_client(), "/customers/import", "客户.xlsx", output.getvalue())
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "新增：1" in html and "跳过：2" in html and "错误：0" in html
    with get_db() as conn:
        created = conn.execute("SELECT * FROM customers WHERE name=?", (fresh,)).fetchone()
        still_deleted = conn.execute("SELECT * FROM customers WHERE name=?", (deleted,)).fetchone()
    assert created["phone"] == "" and created["address"] == "" and created["opening_balance_cents"] == 0
    assert still_deleted["deleted_at"] is not None


def test_import_rejects_bad_suffix_header_empty_file_and_oversize_without_500():
    init_db()
    keep_name = _name("保留商品")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO products(name, spec, unit, default_price_cents) VALUES (?, '', '个', 100)",
            (keep_name,),
        )
    client = create_app().test_client()
    cases = [
        ("/products/import", "bad.txt", b"x", "仅支持 .xlsx 或 .csv 文件"),
        ("/products/import", "empty.csv", b"", "文件为空"),
        ("/products/import", "header-only.csv", "商品名称,价格\n".encode(), "没有可导入的数据行"),
        ("/products/import", "bad.csv", "名称,金额\n商品,1\n".encode(), "表头错误"),
        ("/customers/import", "large.csv", b"a" * (5 * 1024 * 1024 + 1), "文件不能超过5MB"),
    ]
    for url, filename, content, expected in cases:
        response = _upload(client, url, filename, content)
        html = response.get_data(as_text=True)
        assert response.status_code == 400
        assert expected in html
        if url.startswith("/products/"):
            assert keep_name in html
            assert "下载Excel模板" in html


def test_customer_csv_import_accepts_gb18030():
    init_db()
    fresh = _name("国标客户")
    response = _upload(
        create_app().test_client(),
        "/customers/import",
        "客户.csv",
        f"客户名称\n{fresh}\n".encode("gb18030"),
    )
    assert response.status_code == 200
    assert "新增：1" in response.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT id FROM customers WHERE name=?", (fresh,)).fetchone() is not None


def test_product_import_supports_old_and_new_headers_and_distinct_specs():
    init_db()
    name = _name("同名型号商品")
    deleted = _name("回收站商品")
    with get_db() as conn:
        conn.execute("INSERT INTO products(name, spec, unit, default_price_cents) VALUES (?, 'DN20', '个', 100)", (name,))
        conn.execute("INSERT INTO products(name, spec, unit, default_price_cents, deleted_at) VALUES (?, 'DN25', '个', 100, CURRENT_TIMESTAMP)", (deleted,))

    csv_data = (
        f"商品名称,型号,价格\n{name}, DN20 ,99\n{name},DN25,20\n{name},,30\n{name},DN25,40\n{deleted},DN25,50\n"
    ).encode("utf-8-sig")
    response = _upload(create_app().test_client(), "/products/import", "products.csv", csv_data)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "新增：2" in html and "跳过：3" in html and "错误：0" in html
    with get_db() as conn:
        rows = conn.execute("SELECT id, spec, default_price_cents FROM products WHERE name=? ORDER BY id", (name,)).fetchall()
    assert [(row["spec"], row["default_price_cents"]) for row in rows] == [("DN20", 100), ("DN25", 2000), ("", 3000)]

    old_name = _name("旧格式商品")
    old_csv = f"商品名称,价格\n{old_name},8.88\n".encode("utf-8-sig")
    old_response = _upload(create_app().test_client(), "/products/import", "old.csv", old_csv)
    assert old_response.status_code == 200
    with get_db() as conn:
        old_row = conn.execute("SELECT spec, default_price_cents FROM products WHERE name=?", (old_name,)).fetchone()
    assert old_row["spec"] == "" and old_row["default_price_cents"] == 888
