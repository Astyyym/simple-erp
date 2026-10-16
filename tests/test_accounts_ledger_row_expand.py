"""客户往来流水「点展开看商品明细」（2026-10-14 用户要求）。

用户口径：点某条单据 → 该条目下方展开**该单据的商品明细**，
只显示商品信息（名称/单位/数量/单价/金额），不重复单号、往来对象、日期；
展开时该条目的位置与内容保持不动，不整页刷新、不回顶。

本文件锁两端：
- 数据端 `/accounts/api/ledger_items` 对四类单据真的回得出明细、金额符号与流水行一致；
  收款/调整没有明细（400，而不是静默空列表）。
- 页面端只有四类单据带「展开」按钮、收款/调整仍带「作废」，
  并且展开动画/就地插入的结构钩子都在。
"""
from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import add_payment, create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import create_purchase_order, initialize_product
from erp.services import purchase_returns as purchase_returns_service

ITEM_SOURCES = ("order", "purchase", "purchase_return")


def _seed():
    """一张销售单 + 一张退货单 + 一张拿货单 + 一张退拿货单，都挂同一个客户。"""
    init_db()
    customer_id = create_customer("流水展开虚构客户")
    product_id = create_product("流水展开虚构商品", "个")
    initialize_product(product_id, "20", "20", "2026-10-01", "期初", "ledger-expand-init")

    sale_id = create_order_from_typed_rows(
        customer_id, "MD202610140001",
        [{"product_id": product_id, "product_name": "流水展开虚构商品", "unit": "个",
          "quantity": "3", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-10-14", request_key="ledger-expand-sale",
    )
    with get_db() as conn:
        sale_item_id = conn.execute(
            "SELECT id FROM order_items WHERE order_id=?", (sale_id,)
        ).fetchone()["id"]

    return_id = create_order_from_typed_rows(
        customer_id, "MD202610140002",
        [{"product_id": product_id, "product_name": "流水展开虚构商品", "unit": "个",
          "quantity": "1", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-10-14", order_type="return",
        request_key="ledger-expand-return",
    )

    purchase = create_purchase_order(
        "NH202610140001", "2026-10-14",
        [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="ledger-expand-purchase",
    )
    with get_db() as conn:
        purchase_item_id = conn.execute(
            "SELECT id FROM purchase_order_items WHERE purchase_order_id=?", (purchase["order_id"],)
        ).fetchone()["id"]
    purchase_return = purchase_returns_service.create(
        customer_id=customer_id, source_order_id=purchase["order_id"], business_date="2026-10-14",
        items=[{"source_item_id": purchase_item_id, "quantity": "1"}],
        request_key="ledger-expand-purchase-return", status="saved",
    )
    return {
        "customer_id": customer_id,
        "sale": sale_id, "return": return_id,
        "purchase": purchase["order_id"], "purchase_return": purchase_return["order_id"],
        "sale_item_id": sale_item_id,
    }


def _items(source: str, document_id: int):
    return create_app().test_client().get(f"/accounts/api/ledger_items?source={source}&id={document_id}")


# ---------------------------------------------------------------- 数据端

def test_sale_and_return_items_use_the_ledger_sign_convention():
    ids = _seed()
    sale = _items("order", ids["sale"])
    assert sale.status_code == 200
    payload = sale.get_json()
    assert payload["source"] == "order" and payload["id"] == ids["sale"]
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["product_name"] == "流水展开虚构商品"
    assert item["unit"] == "个"
    assert item["quantity"] == "3"
    assert item["unit_price"] == "30.00"
    assert item["amount"] == "90.00", "销售为正向"

    returned = _items("order", ids["return"]).get_json()["items"][0]
    assert returned["quantity"] == "1"
    assert returned["amount"] == "-30.00", "退货为负向（与流水行一致）"


def test_purchase_and_purchase_return_items_are_available():
    ids = _seed()
    purchase = _items("purchase", ids["purchase"]).get_json()["items"][0]
    # 拿货单的「单价」是拿货单价（unit_cost_cents），与拿货单详情页同一口径。
    assert purchase["unit_price"] == "10.00"
    assert purchase["amount"] == "-20.00", "拿货为负向"
    assert purchase["quantity"] == "2"

    returned = _items("purchase_return", ids["purchase_return"]).get_json()["items"][0]
    assert returned["amount"] == "10.00", "退拿货为正向"


def test_payment_and_adjustment_have_no_item_detail():
    """收款/调整没有商品明细 → 400，不能静默回空列表让页面显示「没有明细」。"""
    for source in ("payment", "adjustment", ""):
        response = _items(source, 1)
        assert response.status_code == 400, source
        assert "没有商品明细" in response.get_json()["error"]


def test_missing_or_unknown_document_is_rejected():
    _seed()
    assert _items("order", 0).status_code == 400, "缺 ID"
    assert _items("order", 999999).status_code == 400, "单据不存在"


def test_detail_only_returns_product_dimensions():
    """只回商品维度：不带单号、往来对象、日期（那些流水行上已经有了）。"""
    ids = _seed()
    payload = _items("order", ids["sale"]).get_json()
    assert set(payload.keys()) == {"source", "id", "items"}
    assert set(payload["items"][0].keys()) == {"product_name", "unit", "quantity", "unit_price", "amount"}


# ---------------------------------------------------------------- 页面端

def _ledger_html(customer_id: int) -> str:
    return create_app().test_client().get(f"/accounts/?customer_id={customer_id}").get_data(as_text=True)


def test_ledger_rows_expose_expand_buttons_for_document_types_only():
    ids = _seed()
    add_payment(ids["customer_id"], 5000, "2026-10-14", "现金", "流水展开收款")
    html = _ledger_html(ids["customer_id"])
    assert html.count("data-ledger-toggle=") == 4, "四类单据各有一个展开按钮"
    for source in ITEM_SOURCES:
        assert f"/accounts/api/ledger_items?source={source}&amp;id=" in html or \
            f"/accounts/api/ledger_items?source={source}&id=" in html, source
    # 收款/调整仍走作废流程，没有展开按钮。
    assert html.count("data-void-url=") == 1
    assert ">展开</button>" in html


def test_expand_animation_hooks_are_wired():
    """结构钩子：就地插入 + 高度过渡（0fr→1fr），不整页刷新。"""
    ids = _seed()
    html = _ledger_html(ids["customer_id"])
    for hook in (
        ".ledger-detail-inner { display:grid;grid-template-rows:0fr",
        ".ledger-detail.is-open .ledger-detail-inner { grid-template-rows:1fr",
        "row.after(detail)",
        "requestAnimationFrame(() => detail.classList.add('is-open'))",
        "detail.classList.remove('is-open')",
        "prefers-reduced-motion",
    ):
        assert hook in html, hook
    # 展开是纯前端插入，不能顺手把页面整块刷新（不能进 data-inplace 表单）。
    assert "data-inplace" in html  # 页面本来就有就地刷新（筛选表单），这里只确认没被误删


def test_expanded_detail_table_shows_product_columns_only():
    """明细表列 = 商品/单位/数量/单价/金额；不含单号、往来对象、日期。"""
    ids = _seed()
    html = _ledger_html(ids["customer_id"])
    assert "const COLUMNS = ['商品', '单位', '数量', '单价', '金额'];" in html
    for forbidden in ("单号", "往来对象", "日期"):
        assert f"COLUMNS = ['{forbidden}" not in html
