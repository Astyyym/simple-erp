"""Batch G 段一：档案来源标记 + 待补全 + 自动建档留痕 + 回收站档案复用。

覆盖 开发短计划/2026-10-08_旧数据迁移承接与备份恢复_执行计划.md 的 G-0a–G-0e 与 G-9。
全部在 pytest 隔离数据根内运行。
"""

from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product
from erp.services.master_data_quality import (
    ORIGIN_IMPORT,
    ORIGIN_MANUAL,
    ORIGIN_ORDER,
    origin_label,
    product_needs_completion,
    customer_needs_completion,
)


def _client():
    init_db()
    return create_app().test_client()


def _row(table: str, name: str):
    with get_db() as conn:
        return conn.execute(f"SELECT * FROM {table} WHERE name=?", (name,)).fetchone()


def _audit_summaries(action: str) -> list[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT summary FROM audit_logs WHERE action=? ORDER BY id", (action,)).fetchall()
    return [r["summary"] for r in rows]


def test_manual_creation_writes_manual_origin():
    init_db()
    create_customer("手工客户")
    create_product("手工商品", "4kg", "个", 12000)
    assert _row("customers", "手工客户")["origin"] == ORIGIN_MANUAL
    assert _row("products", "手工商品")["origin"] == ORIGIN_MANUAL


def test_zero_setup_order_auto_creates_with_order_origin_and_audit():
    client = _client()
    resp = client.post(
        "/orders/create",
        data={
            "customer_name": "零建档客户",
            "product_name": "零建档灭火器",
            "spec": "4kg",
            "unit": "个",
            "unit_price": "88",
            "quantity": "1",
            "status": "saved",
        },
    )
    assert resp.status_code in (302, 303)

    customer = _row("customers", "零建档客户")
    product = _row("products", "零建档灭火器")
    assert customer["origin"] == ORIGIN_ORDER
    assert product["origin"] == ORIGIN_ORDER

    # G-0b：自动建档必须留痕，摘要写明来源单据号。
    customer_logs = _audit_summaries("auto_create_customer")
    product_logs = _audit_summaries("auto_create_product")
    assert any("零建档客户" in s and "MD" in s for s in customer_logs)
    assert any("零建档灭火器" in s and "MD" in s for s in product_logs)


def test_same_name_same_spec_does_not_duplicate():
    client = _client()
    for _ in range(2):
        client.post(
            "/orders/create",
            data={
                "customer_name": "去重客户",
                "product_name": "重复商品",
                "spec": "1个",
                "unit": "个",
                "unit_price": "10",
                "quantity": "1",
                "status": "saved",
            },
        )
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM products WHERE name='重复商品'").fetchone()["c"]
    assert count == 1


# 注：手输「型号」目前传不到后端（开单明细缺 `spec` 字段，见计划 G-3），
# 所以「同名不同型号建两条」要到 G 段二补齐 spec 通道后才能验证。
# 本文件只验证「型号为空」这一零建档默认路径的行为。


def test_import_writes_import_origin():
    client = _client()
    # Batch B 起导入改为两段式：先预检，再确认落库。
    import re as _re
    import html as _html

    csv = "客户名称\n导入客户甲\n".encode("utf-8-sig")
    preview = client.post(
        "/customers/import",
        data={"file": (__import__("io").BytesIO(csv), "customers.csv")},
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    pj = _html.unescape(_re.search(r'name="preview_json" value="([^"]*)"', preview.get_data(as_text=True)).group(1))
    client.post("/customers/import/confirm", data={"preview_json": pj, "same_name": "skip"})
    assert _row("customers", "导入客户甲")["origin"] == ORIGIN_IMPORT


def test_product_pending_completion_is_spec_empty_only():
    init_db()
    # 型号为空 → 待补全；未启用库存不算待补全（那是零建档路径的正常状态）。
    create_product("无型号商品", "", "个", 1000)
    create_product("有型号商品", "4kg", "个", 1000)
    assert product_needs_completion(_row("products", "无型号商品")) is True
    assert product_needs_completion(_row("products", "有型号商品")) is False
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM product_inventory_state").fetchone()["c"] == 0


def test_customer_pending_completion_requires_order_origin_and_no_contact():
    init_db()
    create_customer("手工有电话客户", phone="13800000000")
    assert customer_needs_completion(_row("customers", "手工有电话客户")) is False
    # 手工建但无联系方式 → 不算待补全（只有开单攒出来的才算）。
    create_customer("手工无联系方式")
    assert customer_needs_completion(_row("customers", "手工无联系方式")) is False


def test_zero_setup_customer_shows_in_todo_filter_then_leaves_after_completion():
    client = _client()
    client.post(
        "/orders/create",
        data={
            "customer_name": "待补全客户",
            "product_name": "随便商品",
            "spec": "x",
            "unit": "个",
            "unit_price": "5",
            "quantity": "1",
            "status": "saved",
        },
    )
    todo = client.get("/customers/?quality=todo").get_data(as_text=True)
    with get_db() as conn:
        cid = conn.execute("SELECT id FROM customers WHERE name='待补全客户'").fetchone()["id"]
    assert f"/customers/{cid}/edit" in todo

    # 补全电话后自动退出该筛选（断言列表行，而不是搜索建议 datalist）。
    client.post(
        f"/customers/{cid}/edit",
        data={"name": "待补全客户", "phone": "13900000000", "address": "", "opening_balance": "0"},
    )
    after = client.get("/customers/?quality=todo").get_data(as_text=True)
    assert f"/customers/{cid}/edit" not in after


def test_product_todo_filter_and_origin_label_on_list():
    client = _client()
    client.post(
        "/orders/create",
        data={
            "customer_name": "商品筛选客户",
            "product_name": "空型号商品",
            "spec": "",
            "unit": "个",
            "unit_price": "9",
            "quantity": "1",
            "status": "saved",
        },
    )
    page = client.get("/products/?quality=todo").get_data(as_text=True)
    assert "空型号商品" in page
    assert "开单攒出" in page  # 来源标记


def test_g9_product_in_recycle_bin_is_reused_and_restored():
    client = _client()
    init_db()
    # 开单明细目前不传 spec（G-3 属 G 段二），所以回收站档案用空型号来命中。
    pid = create_product("回收站商品", "", "个", 10000)
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP, delete_reason='用户删除' WHERE id=?", (pid,))

    resp = client.post(
        "/orders/create",
        data={
            "customer_name": "回收站复用客户",
            "product_name": "回收站商品",
            "unit": "个",
            "unit_price": "10",
            "quantity": "1",
            "status": "saved",
        },
    )
    assert resp.status_code in (302, 303)

    with get_db() as conn:
        row = conn.execute("SELECT id, deleted_at FROM products WHERE name='回收站商品'").fetchone()
    assert row["deleted_at"] is None  # 已恢复可见性
    assert row["id"] == pid  # 复用同一条记录，不新增
    assert any("回收站商品" in s and "恢复可见性" in s for s in _audit_summaries("restore_product_on_order"))


def test_g9_customer_in_recycle_bin_is_reused_and_restored_without_new_row():
    client = _client()
    init_db()
    cid = create_customer("回收站客户")
    with get_db() as conn:
        conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
        before = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]

    resp = client.post(
        "/orders/create",
        data={
            "customer_name": "回收站客户",
            "product_name": "任意商品",
            "spec": "z",
            "unit": "个",
            "unit_price": "1",
            "quantity": "1",
            "status": "saved",
        },
    )
    assert resp.status_code in (302, 303)

    with get_db() as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]
        row = conn.execute("SELECT deleted_at FROM customers WHERE id=?", (cid,)).fetchone()
    assert after == before  # 复用同一条记录，不新增
    assert row["deleted_at"] is None  # 恢复可见性
    assert any("回收站客户" in s and "恢复可见性" in s for s in _audit_summaries("restore_customer_on_order"))


def test_g9_historical_order_link_unchanged_after_reuse():
    client = _client()
    init_db()
    cid = create_customer("历史关联客户")
    client.post(
        "/orders/create",
        data={
            "customer_id": str(cid),
            "customer_name": "历史关联客户",
            "product_name": "历史商品",
            "spec": "1",
            "unit": "个",
            "unit_price": "10",
            "quantity": "2",
            "status": "saved",
        },
    )
    with get_db() as conn:
        order = conn.execute("SELECT id, customer_id, total_amount_cents FROM orders ORDER BY id DESC LIMIT 1").fetchone()
        conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))

    # 回收站里同名再开一单 → 复用并恢复。
    client.post(
        "/orders/create",
        data={
            "customer_name": "历史关联客户",
            "product_name": "历史商品",
            "spec": "1",
            "unit": "个",
            "unit_price": "10",
            "quantity": "1",
            "status": "saved",
        },
    )
    with get_db() as conn:
        after = conn.execute("SELECT customer_id, total_amount_cents FROM orders WHERE id=?", (order["id"],)).fetchone()
    assert after["customer_id"] == order["customer_id"]
    assert after["total_amount_cents"] == order["total_amount_cents"]


def test_origin_label_unknown_for_legacy_rows():
    assert origin_label(None) == "未知"
    assert origin_label("") == "未知"
    assert origin_label(ORIGIN_MANUAL) == "手工新建"
    assert origin_label(ORIGIN_IMPORT) == "批量导入"
    assert origin_label(ORIGIN_ORDER) == "开单攒出"
