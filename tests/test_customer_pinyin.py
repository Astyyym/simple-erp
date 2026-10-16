"""Phase 3 S3：客户侧拼音首字母（纯派生字段，用于客户联想与搜索）。

背景（计划 §13.2 S3）：`customers` 表原本没有拼音列，客户只能按名称精确子串匹配。
本切片：幂等加列 `ensure_customer_pinyin_field` + 回填 `backfill_customer_pinyin_initials`，
并在**全部 5 个写入点**写拼音（漏一处就会导致新客户搜不到）：
`customers.py` 导入新增 / 编辑改名、`orders.py` 开单自动建档、`purchases.py` 拿货自动建档、
`accounting.py` 的 `create_customer`。

本文件锁「派生值真的被写进库」与「加列幂等、回填旧空值」。
"""
from __future__ import annotations

import io

from erp import create_app
from erp.db import get_db, init_db
from erp.utils.pinyin import pinyin_initials
from erp.services.accounting import create_customer


def _initials_of(customer_id: int) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT pinyin_initials FROM customers WHERE id=?", (customer_id,)
        ).fetchone()
    return row["pinyin_initials"]


def _initials_of_name(name: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT pinyin_initials FROM customers WHERE name=?", (name,)
        ).fetchone()
    return row["pinyin_initials"]


def test_customers_table_has_pinyin_column_and_backfills_legacy_rows():
    """老库升级：加列幂等；旧客户按名称回填拼音（不覆盖已有值）。"""
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO customers(name, phone) VALUES ('应急灯老客户', '13800000000')")
        conn.execute("UPDATE customers SET pinyin_initials='' WHERE name='应急灯老客户'")
    init_db()  # 第二次启动：加列幂等 + 回填
    assert _initials_of_name("应急灯老客户") == "yjdlkh"


def test_backfill_does_not_overwrite_existing_value():
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO customers(name, pinyin_initials) VALUES ('老客户甲', 'custom')")
    init_db()
    assert _initials_of_name("老客户甲") == "custom"


def test_create_customer_writes_pinyin_initials():
    init_db()
    customer_id = create_customer("陈紫坤消防器材店")
    assert _initials_of(customer_id) == "czkxfqcd"


def test_rename_customer_refreshes_pinyin_initials():
    init_db()
    customer_id = create_customer("旧名字客户")
    assert _initials_of(customer_id) == "jmzkh"
    client = create_app().test_client()
    client.post(
        f"/customers/{customer_id}/edit",
        data={"name": "新名字客户", "phone": "", "address": "", "opening_balance": "0"},
    )
    assert _initials_of(customer_id) == "xmzkh"


def test_auto_created_customer_from_sale_order_gets_pinyin():
    """销售开单自动建档（orders.py 的 _resolve_customer_id）也写拼音。"""
    init_db()
    client = create_app().test_client()
    client.post(
        "/orders/create",
        data={
            "customer_name": "自动建档新客户",
            "customer_id": "",
            "order_date": "2026-10-01",
            "status": "saved",
            "product_name": ["消火栓"],
            "unit": ["个"],
            "unit_price": ["100"],
            "quantity": ["1"],
        },
    )
    assert _initials_of_name("自动建档新客户") == "zdjdxkh"


def test_auto_created_customer_from_purchase_order_gets_pinyin():
    """拿货开单自动建档（purchases.py 的 _resolve_purchase_customer_id）也写拼音。"""
    from erp.services.accounting import create_product
    from erp.services.inventory import initialize_product

    init_db()
    product_id = create_product("拿货拼音商品", "个")
    initialize_product(product_id, "10", "10", "2026-10-02", "期初", "pinyin-init")
    client = create_app().test_client()
    client.post(
        "/purchases/create",
        data={
            "customer_name": "拿货自动建档对象",
            "business_date": "2026-10-02",
            "product_id": [str(product_id)],
            "quantity": ["1"],
            "unit_cost_yuan": ["10"],
            "request_key": "pinyin-purchase-1",
            "status": "saved",
        },
    )
    assert _initials_of_name("拿货自动建档对象") == "nhzdjddx"


def test_customer_import_writes_pinyin_initials():
    """导入新增分支也写拼音（真实两段式：预览 → 确认落库）。"""
    import html as html_module
    import re as re_module

    from openpyxl import Workbook

    init_db()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["客户名称", "电话", "地址", "期初余额（元）", "备注"])
    sheet.append(["导入拼音客户", "", "", 0, ""])
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    client = create_app().test_client()
    preview_response = client.post(
        "/customers/import",
        data={"file": (buffer, "客户.xlsx"), "same_name": "skip"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    preview_html = preview_response.get_data(as_text=True)
    match = re_module.search(r'name="preview_json" value="([^"]*)"', preview_html)
    assert match, "预检页必须携带 preview_json 隐藏域"
    client.post(
        "/customers/import/confirm",
        data={"preview_json": html_module.unescape(match.group(1)), "same_name": "skip"},
        follow_redirects=True,
    )
    assert _initials_of_name("导入拼音客户") == "drpykh"
