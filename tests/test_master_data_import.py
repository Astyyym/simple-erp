from __future__ import annotations

import io
import re
import uuid

from openpyxl import load_workbook

from erp import create_app
from erp.db import get_db, init_db


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _upload(client, url: str, filename: str, content: bytes, *, same_name: str = "skip"):
    return client.post(
        url,
        data={"file": (io.BytesIO(content), filename), "same_name": same_name},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _preview_json(html: str) -> str:
    m = re.search(r'name="preview_json" value="([^"]*)"', html)
    assert m, "预检页必须携带 preview_json 隐藏域"
    import html as html_module

    return html_module.unescape(m.group(1))


def _confirm(client, confirm_url: str, preview_json: str, *, same_name: str = "skip"):
    return client.post(
        confirm_url,
        data={"preview_json": preview_json, "same_name": same_name},
        follow_redirects=True,
    )


def test_product_and_customer_excel_templates_have_extended_headers():
    client = create_app().test_client()

    product_response = client.get("/products/import/template")
    customer_response = client.get("/customers/import/template")

    assert product_response.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in product_response.content_type
    assert "attachment" in product_response.headers["Content-Disposition"]
    product_sheet = load_workbook(io.BytesIO(product_response.data), read_only=True).active
    # 决策变更（v14）：商品身份 = 单一名称，模板不再有「型号」列（型号直接写进商品名称）。
    assert list(next(product_sheet.iter_rows(values_only=True))) == [
        "商品名称", "品牌", "单位", "默认价", "期初数量", "期初成本（元）", "备注"
    ]

    assert customer_response.status_code == 200
    customer_sheet = load_workbook(io.BytesIO(customer_response.data), read_only=True).active
    assert list(next(customer_sheet.iter_rows(values_only=True))) == [
        "客户名称", "电话", "地址", "期初余额（元）", "备注"
    ]


def test_management_pages_show_chinese_import_controls():
    client = create_app().test_client()
    products = client.get("/products/").get_data(as_text=True)
    customers = client.get("/customers/").get_data(as_text=True)

    assert "/products/import" in products and "下载Excel模板" in products and 'accept=".xlsx,.csv"' in products
    assert "/customers/import" in customers and "下载Excel模板" in customers and 'accept=".xlsx,.csv"' in customers
    assert "预检并导入" in products and "预检并导入" in customers


def test_product_import_preview_then_confirm_writes_rows():
    init_db()
    existing = _name("已有商品")
    fresh = _name("新增商品")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO products(name, unit, default_price_cents) VALUES (?, '箱', 999)",
            (existing,),
        )
    csv_data = f"商品名称,价格\n{fresh},12.34\n{existing},88\n{fresh},20\n空价商品,\n负价商品,-1\n,10\n".encode("utf-8-sig")

    client = create_app().test_client()
    preview = _upload(client, "/products/import", "products.csv", csv_data)
    html = preview.get_data(as_text=True)

    # 预检：未确认不得落库。
    assert preview.status_code == 200
    assert "将新增：1" in html and "跳过：2" in html and "错误：3" in html
    assert "第5行" in html and "价格不能为空" in html
    assert "第6行" in html and "价格不能为负数" in html
    assert "第7行" in html and "商品名称不能为空" in html
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM products WHERE name=?", (fresh,)).fetchone() is None

    # 确认后才落库。
    confirmed = _confirm(client, "/products/import/confirm", _preview_json(html))
    assert confirmed.status_code == 200
    assert "新增：1" in confirmed.get_data(as_text=True)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE name=?", (fresh,)).fetchone()
        old = conn.execute("SELECT * FROM products WHERE name=?", (existing,)).fetchone()
    assert row["unit"] == "个"
    assert row["default_price_cents"] == 1234
    assert old["default_price_cents"] == 999


def test_customer_import_preview_counts_soft_deleted_and_file_duplicates():
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

    client = create_app().test_client()
    preview = _upload(client, "/customers/import", "客户.xlsx", output.getvalue())
    html = preview.get_data(as_text=True)
    assert preview.status_code == 200
    assert "将新增：1" in html and "跳过：1" in html
    # B-8：软删占名明确提示，不静默跳过。
    assert "回收站" in html

    confirmed = _confirm(client, "/customers/import/confirm", _preview_json(html))
    assert confirmed.status_code == 200
    with get_db() as conn:
        created = conn.execute("SELECT * FROM customers WHERE name=?", (fresh,)).fetchone()
        still_deleted = conn.execute("SELECT * FROM customers WHERE name=?", (deleted,)).fetchone()
    assert created["origin"] == "import"
    assert still_deleted["deleted_at"] is not None


def test_import_rejects_bad_suffix_header_empty_file_and_oversize_without_500():
    init_db()
    keep_name = _name("保留商品")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO products(name, unit, default_price_cents) VALUES (?, '个', 100)",
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
    client = create_app().test_client()
    preview = _upload(
        client,
        "/customers/import",
        "客户.csv",
        f"客户名称\n{fresh}\n".encode("gb18030"),
    )
    html = preview.get_data(as_text=True)
    assert preview.status_code == 200
    confirmed = _confirm(client, "/customers/import/confirm", _preview_json(html))
    assert "新增：1" in confirmed.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT id FROM customers WHERE name=?", (fresh,)).fetchone() is not None


def test_product_import_rejects_spec_header_and_still_supports_two_column_header():
    """决策变更（v14）：商品身份 = 单一名称，`spec`（型号）已并入名称。

    原「同名不同型号」用例依赖已删除的型号列，改为钉住新契约：
    - 含「型号」列的旧文件必须**明确报错（表头错误）**，不静默丢型号、也不落库；
    - 旧的两列模板「商品名称,价格」仍可导入（单位回落「个」），旧库不被阻塞。
    """
    init_db()
    name = _name("同名型号商品")
    deleted = _name("回收站商品")
    with get_db() as conn:
        conn.execute("INSERT INTO products(name, unit, default_price_cents) VALUES (?, '个', 100)", (name,))
        conn.execute("INSERT INTO products(name, unit, default_price_cents, deleted_at) VALUES (?, '个', 100, CURRENT_TIMESTAMP)", (deleted,))

    # 含「型号」列的旧文件：整表头精确匹配挡下，明确报错。
    legacy_csv = (
        f"商品名称,型号,价格\n{name}, DN20 ,99\n{name},DN25,20\n{deleted},DN25,50\n"
    ).encode("utf-8-sig")
    client = create_app().test_client()
    rejected = _upload(client, "/products/import", "products.csv", legacy_csv)
    rejected_html = rejected.get_data(as_text=True)
    assert rejected.status_code == 400
    assert "表头错误" in rejected_html
    # 报错即零写入：既有商品价不动，回收站记录不动，也不新增。
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2
        assert conn.execute("SELECT default_price_cents FROM products WHERE name=?", (name,)).fetchone()[0] == 100
        assert conn.execute("SELECT deleted_at FROM products WHERE name=?", (deleted,)).fetchone()[0] is not None

    # 旧两列模板仍可导入（单位回落「个」）。
    old_name = _name("旧格式商品")
    old_csv = f"商品名称,价格\n{old_name},8.88\n".encode("utf-8-sig")
    old_preview = _upload(client, "/products/import", "old.csv", old_csv)
    old_html = old_preview.get_data(as_text=True)
    assert old_preview.status_code == 200
    _confirm(client, "/products/import/confirm", _preview_json(old_html))
    with get_db() as conn:
        old_row = conn.execute("SELECT unit, default_price_cents FROM products WHERE name=?", (old_name,)).fetchone()
    assert old_row["unit"] == "个" and old_row["default_price_cents"] == 888
