"""Batch G 段二：开单页商品手输（混合控件）+ G-3 spec 通道 + G-5 拿货页提示。"""

from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_product


def _client():
    init_db()
    return create_app().test_client()


def test_order_page_uses_hybrid_typeable_picker():
    client = _client()
    page = client.get("/orders/new").get_data(as_text=True)
    assert 'class="form-control form-control-sm picker-name-input"' in page
    assert 'class="form-control form-control-sm picker-spec-input"' in page
    assert 'name="spec"' in page
    # 旧的不可输入 select 已从销售/退货页移除。
    assert 'form-select form-select-sm picker-name"' not in page
    assert "mountHybrid" in page


def test_hand_typed_spec_reaches_backend_and_creates_product_with_spec():
    """G-3：手输型号必须真的落库为独立商品。"""
    client = _client()
    resp = client.post(
        "/orders/create",
        data={
            "customer_name": "手输客户",
            "product_name": "灭火器",
            "spec": "4kg",
            "unit": "个",
            "unit_price": "100",
            "quantity": "1",
            "status": "saved",
        },
    )
    assert resp.status_code in (302, 303)
    with get_db() as conn:
        row = conn.execute("SELECT name, spec, origin FROM products WHERE name='灭火器'").fetchone()
        item = conn.execute("SELECT product_name, spec FROM order_items ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and row["spec"] == "4kg"
    assert row["origin"] == "order"
    assert item["spec"] == "4kg"


def test_same_name_different_spec_creates_two_products():
    client = _client()
    for spec in ("4kg", "8kg"):
        client.post(
            "/orders/create",
            data={
                "customer_name": "型号区分客户",
                "product_name": "灭火器",
                "spec": spec,
                "unit": "个",
                "unit_price": "100",
                "quantity": "1",
                "status": "saved",
            },
        )
    with get_db() as conn:
        rows = conn.execute("SELECT spec FROM products WHERE name='灭火器' ORDER BY spec").fetchall()
    assert [r["spec"] for r in rows] == ["4kg", "8kg"]


def test_same_name_same_spec_reuses_one_product():
    client = _client()
    for _ in range(2):
        client.post(
            "/orders/create",
            data={
                "customer_name": "复用客户",
                "product_name": "灭火器",
                "spec": "4kg",
                "unit": "个",
                "unit_price": "100",
                "quantity": "1",
                "status": "saved",
            },
        )
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM products WHERE name='灭火器' AND spec='4kg'").fetchone()["c"]
    assert count == 1


def test_empty_spec_still_works_and_is_distinct_from_filled_spec():
    client = _client()
    client.post("/orders/create", data={
        "customer_name": "空型号客户", "product_name": "管件", "spec": "", "unit": "个",
        "unit_price": "5", "quantity": "1", "status": "saved",
    })
    client.post("/orders/create", data={
        "customer_name": "空型号客户", "product_name": "管件", "spec": "DN20", "unit": "个",
        "unit_price": "6", "quantity": "1", "status": "saved",
    })
    with get_db() as conn:
        rows = conn.execute("SELECT spec FROM products WHERE name='管件' ORDER BY spec").fetchall()
    assert [r["spec"] for r in rows] == ["", "DN20"]


def test_edit_page_roundtrips_spec_into_picker_inputs():
    client = _client()
    pid = create_product("回填商品", "DN25", "个", 1000)
    with get_db() as conn:
        cid = conn.execute("INSERT INTO customers(name) VALUES ('回填客户')").lastrowid
    client.post("/orders/create", data={
        "customer_id": str(cid), "customer_name": "回填客户", "product_id": str(pid),
        "product_name": "回填商品", "spec": "DN25", "unit": "个", "unit_price": "10",
        "quantity": "1", "status": "saved",
    })
    with get_db() as conn:
        oid = conn.execute("SELECT id FROM orders ORDER BY id DESC LIMIT 1").fetchone()["id"]
    page = client.get(f"/orders/{oid}/edit").get_data(as_text=True)
    assert 'value="回填商品"' in page
    assert 'value="DN25"' in page


def test_purchase_pages_show_zero_setup_hint_and_keep_selects():
    """G-5：拿货/退拿货本轮不做手输，只加提示；控件仍是两级下拉。"""
    client = _client()
    for path, hint_id in (("/purchases/new", "purchaseZeroSetupHint"), ("/purchases/return/new", "purchaseReturnZeroSetupHint")):
        page = client.get(path).get_data(as_text=True)
        assert hint_id in page
        assert "启用库存后才能入库" in page
        # 控件未改成手输。
        assert 'picker-name-input' not in page
        assert 'class="form-select form-select-sm picker-name"' in page
