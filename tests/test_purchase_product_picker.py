"""P3（2026-10-13）：四页开单的商品列统一为「手输 + 联想」。

冲突处置：原 G-5 规则（拿货/退拿货保持原生下拉、不做手输）与本次用户要求冲突，
按用户 2026-10-13「和之前冲突的，一本次为准」的答复收口 —— **形态统一**，
但业务差异保留：拿货 / 退拿货**必须命中已建档商品**，未命中不写 product_id、
不自动建档（后端拒单并给出可照做的提示）。
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


def test_purchase_product_api_lists_only_archived_products():
    """联想接口只返回已建档商品，按名称/拼音匹配；未建档的名称不会出现在候选里。"""
    client = _client()
    pid = create_product("联想用闸阀DN50", "个", 1000)
    data = client.get("/purchases/api/products?q=闸阀").get_json()
    assert [row["name"] for row in data] == ["联想用闸阀DN50"]
    assert data[0]["id"] == pid
    assert data[0]["unit"] == "个"
    assert client.get("/purchases/api/products?q=压根不存在的商品").get_json() == []


def test_purchase_pages_never_auto_create_products():
    """手输未命中：不写 product_id → 保存被拒（提示可照做），且不落任何单/商品。"""
    client = _client()
    for path in ("/purchases/create", "/purchases/return/create"):
        resp = client.post(
            path,
            data={
                "customer_name": "未命中往来",
                "product_name": "从未建档的商品X",
                "product_id": "",
                "quantity": "1",
                "unit": "个",
                "unit_cost_yuan": "10",
                "unit_price_yuan": "10",
                "business_date": "2026-10-13",
            },
            headers={"X-Requested-With": "fetch"},
        )
        assert resp.status_code == 400, path
        message = resp.get_json()["message"]
        assert "已建档商品" in message, (path, message)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM purchase_return_orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
