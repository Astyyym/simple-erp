"""四页开单商品列：统一「手输 + 联想」形态 + 建档口径（2026-11-05 调整）。

用户口径（2026-11-05）：
- 销售单 / 拿货单：手输未建档名称 → **保存时自动建档**。
- 退货单 / 退拿货单：**只能选已建档商品**，未建档拒单，不自动建档。
"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_product

ENTRY_PAGES = ("/orders/new", "/orders/return/new", "/purchases/new", "/purchases/return/new")


def _client():
    init_db()
    return create_app().test_client()


def test_all_four_entry_pages_use_typeable_product_picker():
    """四页商品列同形态：手输输入框 + 候选浮层；原生下拉（点击即全量铺开）不再出现。"""
    client = _client()
    for path in ENTRY_PAGES:
        page = client.get(path).get_data(as_text=True)
        assert 'class="form-control form-control-sm picker-name-input"' in page, path
        assert 'class="suggest-list d-none product-suggestions"' in page, path
        assert 'class="form-select form-select-sm picker-name"' not in page, path


def test_purchase_product_api_lists_archived_products():
    """联想接口只返回已建档商品，按名称/拼音匹配。"""
    client = _client()
    pid = create_product("联想用闸阀DN50", "个")
    data = client.get("/purchases/api/products?q=闸阀").get_json()
    assert [row["name"] for row in data] == ["联想用闸阀DN50"]
    assert data[0]["id"] == pid
    assert data[0]["unit"] == "个"
    assert client.get("/purchases/api/products?q=压根不存在的商品").get_json() == []


def test_purchase_auto_creates_new_product_from_typed_name():
    """拿货单手输未建档名称 → 保存成功并自动建档（origin='order'），不写给客户价。"""
    client = _client()
    resp = client.post(
        "/purchases/create",
        data={
            "customer_name": "拿货自动建档往来",
            "product_name": "从未建档的拿货商品X",
            "product_id": "",
            "quantity": "2",
            "unit": "个",
            "unit_cost_yuan": "10",
            "business_date": "2026-10-13",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    with get_db() as conn:
        product = conn.execute("SELECT id, origin FROM products WHERE name='从未建档的拿货商品X'").fetchone()
        assert product is not None
        assert product["origin"] == "order"
        item = conn.execute(
            "SELECT product_id FROM purchase_order_items WHERE purchase_order_id=?",
            (body["order_id"],),
        ).fetchone()
        assert item["product_id"] == product["id"]
        # 拿货单价是成本，不得写进客户专属价。
        assert conn.execute("SELECT COUNT(*) FROM customer_prices").fetchone()[0] == 0


def test_purchase_matches_existing_product_with_normalized_name():
    """商品管理已有同名商品（全/半角括号、空格差异）→ 拿货直接命中，不重复建档。"""
    client = _client()
    pid = create_product("沟槽明阀DN65（开维喜）", "台")
    resp = client.post(
        "/purchases/create",
        data={
            "customer_name": "规范化命中往来",
            "product_name": "沟槽明阀DN65(开维喜)",
            "product_id": "",
            "quantity": "1",
            "unit": "台",
            "unit_cost_yuan": "88",
            "business_date": "2026-10-13",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
        item = conn.execute(
            "SELECT product_id FROM purchase_order_items WHERE purchase_order_id=?",
            (resp.get_json()["order_id"],),
        ).fetchone()
        assert item["product_id"] == pid


def test_purchase_return_never_auto_creates_products():
    """退拿货手输未建档名称 → 拒单，不落任何单/商品。"""
    client = _client()
    resp = client.post(
        "/purchases/return/create",
        data={
            "customer_name": "退拿货未命中往来",
            "product_name": "退拿货从未建档商品Y",
            "product_id": "",
            "quantity": "1",
            "unit": "个",
            "unit_price_yuan": "10",
            "business_date": "2026-10-13",
            "request_key": "test-return-auto-001",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 400
    assert "已建档商品" in resp.get_json()["message"]
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_return_orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
