"""开单页商品单框手输（名称即身份）+ 拿货页提示。

v14 变更：商品身份 = 单一名称，型号已并入名称 → 原来「名称 + 型号」两框与 `spec`
提交通道整条消失；下面这些用例按新契约重写，原意图（手输必须真的落库为商品、
同名复用、不同名称是两个商品、编辑页回填）逐条保留。
"""

from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_product


def _client():
    init_db()
    return create_app().test_client()


def test_order_page_uses_single_typeable_name_picker():
    client = _client()
    page = client.get("/orders/new").get_data(as_text=True)
    assert 'class="form-control form-control-sm picker-name-input"' in page
    assert "mountHybrid" in page
    # v14：型号框与 `spec` 提交字段都已随列删除。
    assert "picker-spec-input" not in page
    assert 'name="spec"' not in page


def test_hand_typed_name_reaches_backend_and_creates_product():
    """手输名称（含型号文字）必须真的落库为商品与明细。"""
    client = _client()
    resp = client.post(
        "/orders/create",
        data={
            "customer_name": "手输客户",
            "product_name": "灭火器4kg",
            "unit": "个",
            "unit_price": "100",
            "quantity": "1",
            "status": "saved",
        },
    )
    assert resp.status_code in (302, 303)
    with get_db() as conn:
        row = conn.execute("SELECT name, origin FROM products WHERE name='灭火器4kg'").fetchone()
        item = conn.execute("SELECT product_name FROM order_items ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["origin"] == "order"
    assert item["product_name"] == "灭火器4kg"


def test_different_names_create_two_products():
    """原「同名不同型号建两条」→ v14 由「名称不同即两个商品」承担。"""
    client = _client()
    for name in ("灭火器4kg", "灭火器8kg"):
        client.post(
            "/orders/create",
            data={
                "customer_name": "名称区分客户",
                "product_name": name,
                "unit": "个",
                "unit_price": "100",
                "quantity": "1",
                "status": "saved",
            },
        )
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name FROM products WHERE name LIKE '灭火器%' ORDER BY name"
        ).fetchall()
    assert [r["name"] for r in rows] == ["灭火器4kg", "灭火器8kg"]


def test_same_name_reuses_one_product():
    client = _client()
    for _ in range(2):
        client.post(
            "/orders/create",
            data={
                "customer_name": "复用客户",
                "product_name": "灭火器4kg",
                "unit": "个",
                "unit_price": "100",
                "quantity": "1",
                "status": "saved",
            },
        )
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM products WHERE name='灭火器4kg'"
        ).fetchone()["c"]
    assert count == 1


def test_distinct_names_are_distinct_products():
    """原「空型号与有型号是两个商品」→ 现在由「名称不同即两个商品」承担。"""
    client = _client()
    client.post("/orders/create", data={
        "customer_name": "名称区分客户", "product_name": "管件", "unit": "个",
        "unit_price": "5", "quantity": "1", "status": "saved",
    })
    client.post("/orders/create", data={
        "customer_name": "名称区分客户", "product_name": "管件DN20", "unit": "个",
        "unit_price": "6", "quantity": "1", "status": "saved",
    })
    with get_db() as conn:
        rows = conn.execute("SELECT name FROM products ORDER BY name").fetchall()
    assert [r["name"] for r in rows] == ["管件", "管件DN20"]


def test_edit_page_roundtrips_name_into_picker_input():
    client = _client()
    pid = create_product("回填商品DN25", "个")
    with get_db() as conn:
        cid = conn.execute("INSERT INTO customers(name) VALUES ('回填客户')").lastrowid
    client.post("/orders/create", data={
        "customer_id": str(cid), "customer_name": "回填客户", "product_id": str(pid),
        "product_name": "回填商品DN25", "unit": "个", "unit_price": "10",
        "quantity": "1", "status": "saved",
    })
    with get_db() as conn:
        oid = conn.execute("SELECT id FROM orders ORDER BY id DESC LIMIT 1").fetchone()["id"]
    page = client.get(f"/orders/{oid}/edit").get_data(as_text=True)
    assert 'value="回填商品DN25"' in page
    assert 'name="spec"' not in page


def test_purchase_pages_show_zero_setup_hint_and_use_typeable_picker():
    """G-5（2026-10-13 P3 修订，冲突以本次为准）：提示保留；控件与销售/退货同形态。

    形态统一为「手输 + 联想」，但业务差异保留：**必须命中已建档商品**
    （未命中不写 product_id、后端拒单，见 test_purchase_product_picker.py）。
    """
    client = _client()
    for path, hint_id, hint_text in (
        ("/purchases/new", "purchaseZeroSetupHint", "新商品需先到「商品管理」页建档后才能用于拿货单。"),
        ("/purchases/return/new", "purchaseReturnZeroSetupHint", "新商品需先到「商品管理」页建档后才能用于退拿货单。"),
    ):
        page = client.get(path).get_data(as_text=True)
        assert hint_id in page
        # 决策变更（2026-10-16 ②/③）：库存系统删除后，提示不再说「启用库存后才能入库」，
        # 改为「新商品需先到商品管理页建档」——零建档提示本身仍在，这正是本用例的核心。
        assert hint_text in page
        # 与销售 / 退货同形态：手输框 + 候选浮层，不再是原生下拉。
        assert 'class="form-control form-control-sm picker-name-input"' in page
        assert 'class="suggest-list d-none product-suggestions"' in page
        assert 'class="form-select form-select-sm picker-name"' not in page
        assert "picker-spec" not in page
