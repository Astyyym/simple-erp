import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product
from erp.services.inventory import (
    create_purchase_order,
    finalize_purchase_order,
    initialize_product,
    update_purchase_draft,
    void_purchase_order,
)
from erp.routes.recycle import _purge_one


def _customer():
    with get_db() as conn:
        row = conn.execute("SELECT id FROM customers WHERE name='虚构拿货往来对象'").fetchone()
    return row['id'] if row else create_customer('虚构拿货往来对象')


def _product():
    init_db()
    product_id = create_product("拿货测试商品", "P1", "个", 2000)
    initialize_product(product_id, "10", "10", "2026-10-01", "期初", "purchase-init-001")
    return product_id


def test_purchase_posts_order_and_updates_moving_average_cost_atomically():
    product_id = _product()

    result = create_purchase_order(
        "NH202610010001",
        "2026-10-01",
        [{"product_id": product_id, "quantity": "10", "unit_cost_yuan": "14.00"}],
        request_key="purchase-001",
        customer_id=_customer(),
        source_notes="市场拿货",
    )

    assert result["order_no"] == "NH202610010001"
    assert result["total_amount_cents"] == 14000
    with get_db() as conn:
        order = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (result["order_id"],)).fetchone()
        item = conn.execute("SELECT * FROM purchase_order_items WHERE purchase_order_id=?", (result["order_id"],)).fetchone()
        state = conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        posting = conn.execute("SELECT * FROM inventory_postings WHERE source_type='purchase'").fetchone()
    assert (order["status"], order["source_notes"]) == ("saved", "市场拿货")
    assert (item["product_id"], item["quantity_3dp"], item["unit_cost_cents"], item["subtotal_cents"]) == (product_id, 10000, 1400, 14000)
    assert (state["quantity_3dp"], state["cost_total_micro"], state["avg_cost_micro"]) == (20000, 240_000_000, 12_000_000)
    assert posting["source_id"] == str(result["order_id"])


def test_purchase_request_is_idempotent_and_different_payload_conflicts():
    product_id = _product()
    first = create_purchase_order(
        "NH202610010002", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-002", customer_id=_customer()
    )
    retry = create_purchase_order(
        "NH202610010002", "2026-10-01", [{"product_id": product_id, "quantity": Decimal("1.000"), "unit_cost_yuan": "14.00"}], request_key="purchase-002", customer_id=_customer()
    )
    assert retry == first
    with pytest.raises(ValueError, match="幂等键内容不一致"):
        create_purchase_order(
            "NH202610010002", "2026-10-01", [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "14"}], request_key="purchase-002", customer_id=_customer()
        )
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings WHERE source_type='purchase'").fetchone()[0] == 1


def test_multi_line_purchase_accumulates_same_product_without_losing_first_line():
    product_id = _product()
    create_purchase_order(
        "NH202610010005",
        "2026-10-01",
        [
            {"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"},
            {"product_id": product_id, "quantity": "2", "unit_cost_yuan": "20"},
        ],
        request_key="purchase-005",
        customer_id=_customer(),
    )
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        items = conn.execute("SELECT COUNT(*) FROM purchase_order_items").fetchone()[0]
    assert tuple(state) == (13000, 154_000_000)
    assert items == 2


def test_purchase_rejects_uninitialized_product_and_invalid_rows_without_partial_write():
    init_db()
    product_id = create_product("未启用拿货商品", "P2", "个", 2000)
    with pytest.raises(ValueError, match="尚未启用库存"):
        create_purchase_order(
            "NH202610010003", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-003", customer_id=_customer()
        )

    enabled_id = _product()
    with pytest.raises(ValueError, match="数量最多保留3位小数"):
        create_purchase_order(
            "NH202610010004", "2026-10-01", [{"product_id": enabled_id, "quantity": "1.0001", "unit_cost_yuan": "14"}], request_key="purchase-004", customer_id=_customer()
        )
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0
        state = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (enabled_id,)).fetchone()
    assert tuple(state) == (10000, 100_000_000)


def test_uninitialized_product_may_be_saved_as_draft_but_not_finalized():
    init_db()
    product_id = create_product("未启用草稿商品", "P3", "个", 2000)
    draft = create_purchase_order(
        "NH202610010012", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-uninitialized-draft-001", status="draft", customer_id=_customer()
    )
    with pytest.raises(ValueError, match="商品尚未启用库存"):
        finalize_purchase_order(draft["order_id"])
    with get_db() as conn:
        assert conn.execute("SELECT status FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()["status"] == "draft"
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings").fetchone()[0] == 0


def test_purchase_form_lists_uninitialized_product_as_draft_only():
    init_db()
    product_id = create_product("页面草稿商品", "P4", "个", 2000)
    page = create_app().test_client().get("/purchases/new").get_data(as_text=True)
    # 未启用库存的商品仍进入两级选择器目录，并标记为只能存草稿。
    match = re.search(r'<script type="application/json" id="productCatalog">(.*?)</script>', page, re.DOTALL)
    assert match is not None, "missing product catalog"
    catalog = json.loads(match.group(1))
    entry = next(item for item in catalog if item["id"] == product_id)
    assert entry["inventory_enabled"] is False
    picker = (Path(__file__).resolve().parents[1] / "app" / "erp" / "static" / "order-product-picker.js").read_text(encoding="utf-8")
    assert "未启用，仅可保存草稿" in picker


def test_purchase_browser_path_saves_and_renders_internal_detail():
    product_id = _product()
    client = create_app().test_client()
    new_page = client.get("/purchases/new")
    assert new_page.status_code == 200
    # 统一八列明细（序号/产品名称/单位/数量/单价/金额/成本/操作），单价列头不再单独叫“拿货单价（元）”。
    new_html = new_page.get_data(as_text=True)
    assert "序号" in new_html and "单价(元)" in new_html and "金额" in new_html and "成本(元)" in new_html
    response = client.post(
        "/purchases/create",
        data={
            "customer_id": str(_customer()),
            "business_date": "2026-10-01",
            "source_notes": "仅内部可见货源备注",
            "product_id": [str(product_id)],
            "quantity": ["10"],
            "unit_cost_yuan": ["14.00"],
            "request_key": "web-purchase-retry-001",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "NH202610010001" in html and "仅内部可见货源备注" in html and "14.00" in html
    retry = client.post(
        "/purchases/create",
        data={
            "customer_id": str(_customer()),
            "business_date": "2026-10-01",
            "source_notes": "仅内部可见货源备注",
            "product_id": [str(product_id)],
            "quantity": ["10"],
            "unit_cost_yuan": ["14.00"],
            "request_key": "web-purchase-retry-001",
        },
        follow_redirects=True,
    )
    assert retry.status_code == 200 and "NH202610010001" in retry.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1  # explicitly seeded; never auto-created
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 1


def test_purchase_draft_can_be_edited_then_finalized_once():
    product_id = _product()
    draft = create_purchase_order(
        "NH202610010006",
        "2026-10-01",
        [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "14"}],
        request_key="purchase-draft-001",
        customer_id=_customer(),
        status="draft",
    )
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        order = conn.execute("SELECT status FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()
    assert tuple(state) == (10000, 100_000_000)
    assert order["status"] == "draft"

    update_purchase_draft(
        draft["order_id"],
        "2026-10-01",
        [{"product_id": product_id, "quantity": "3", "unit_cost_yuan": "15"}],
        source_notes="修改后的内部货源",
        customer_id=_customer(),
    )
    finalized = finalize_purchase_order(draft["order_id"])
    assert finalized["order_no"] == draft["order_no"]
    with get_db() as conn:
        order = conn.execute("SELECT status, source_notes, total_amount_cents FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()
        item = conn.execute("SELECT quantity_3dp, unit_cost_cents FROM purchase_order_items WHERE purchase_order_id=?", (draft["order_id"],)).fetchone()
        state = conn.execute("SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert (order["status"], order["source_notes"], order["total_amount_cents"]) == ("saved", "修改后的内部货源", 4500)
    assert tuple(item) == (3000, 1500)
    assert tuple(state) == (13000, 145_000_000, 11_153_846)

    with pytest.raises(ValueError, match="正式拿货单经济字段已锁定"):
        update_purchase_draft(
            draft["order_id"],
            "2026-10-01",
            [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "99"}],
            source_notes="不应覆盖",
            customer_id=_customer(),
        )
    assert finalize_purchase_order(draft["order_id"]) == finalized


def test_purchase_void_allows_non_last_inventory_event_and_restores_state():
    """2026-10-07 用户决策：拿货单也不再要求「必须是该商品末笔」即可作废。

    反向过账后库存数量精确回退；均价按移动加权平均重算（可能漂），这是接受的取舍。
    """
    product_id = _product()
    first = create_purchase_order(
        "NH202610010007", "2026-10-01", [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "14"}], request_key="purchase-void-001", customer_id=_customer()
    )
    second = create_purchase_order(
        "NH202610010008", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "20"}], request_key="purchase-void-002", customer_id=_customer()
    )
    with get_db() as conn:
        qty_before = conn.execute(
            "SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)
        ).fetchone()["quantity_3dp"]

    # 第一张不是末笔（后面还有 second），现在允许作废。
    voided = void_purchase_order(first["order_id"], "第一张单填错")
    assert voided["status"] == "void"
    with get_db() as conn:
        state = conn.execute(
            "SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
    # 数量精确回退 2 个（first 的拿货量）。
    assert int(state["quantity_3dp"]) == int(qty_before) - 2000
    assert int(state["cost_total_micro"]) >= 0

    voided_second = void_purchase_order(second["order_id"], "第二张也填错")
    assert voided_second["status"] == "void"
    with get_db() as conn:
        state = conn.execute(
            "SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        postings = conn.execute("SELECT source_type FROM inventory_postings ORDER BY posting_seq").fetchall()
    assert int(state["quantity_3dp"]) == int(qty_before) - 3000
    assert postings[-1]["source_type"] == "purchase_void"


def test_purchase_draft_recycle_and_purge_do_not_touch_inventory():
    product_id = _product()
    draft = create_purchase_order(
        "NH202610010009", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-recycle-001", status="draft", customer_id=_customer()
    )
    with get_db() as conn:
        conn.execute("UPDATE purchase_orders SET deleted_at=CURRENT_TIMESTAMP, delete_reason='测试删除' WHERE id=?", (draft["order_id"],))
        state_before = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        _purge_one(conn, "purchase_order", draft["order_id"])
        assert conn.execute("SELECT 1 FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone() is None
        state_after = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert tuple(state_before) == tuple(state_after) == (10000, 100_000_000)


def test_expired_recycle_cleanup_removes_saved_purchase_order():
    """2026-10-07 起：过期的回收站拿货单会被真正清掉（旧版因 `status != draft` 恒不生效）。"""
    product_id = _product()
    saved = create_purchase_order(
        "NH202610010013", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-cleanup-saved-001", customer_id=_customer()
    )
    with get_db() as conn:
        conn.execute("UPDATE purchase_orders SET deleted_at='2020-01-01 00:00:00' WHERE id=?", (saved["order_id"],))
        from erp.db import purge_expired_recycle_bin
        purge_expired_recycle_bin(conn)
        assert conn.execute("SELECT 1 FROM purchase_orders WHERE id=?", (saved["order_id"],)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM purchase_order_items WHERE purchase_order_id=?", (saved["order_id"],)).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_purchase_browser_path_supports_draft_finalize_and_void():
    product_id = _product()
    client = create_app().test_client()
    response = client.post(
        "/purchases/create",
        data={
            "business_date": "2026-10-01",
            "source_notes": "草稿货源",
            "customer_id": str(_customer()),
            "product_id": [str(product_id)],
            "quantity": ["2"],
            "unit_cost_yuan": ["14"],
            "request_key": "web-purchase-draft-001",
            "status": "draft",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "草稿" in html and "正式保存并入库" in html
    with get_db() as conn:
        purchase_id = conn.execute("SELECT id FROM purchase_orders WHERE request_key=?", ("web-purchase-draft-001",)).fetchone()["id"]
    response = client.post(f"/purchases/{purchase_id}/finalize", follow_redirects=True)
    assert response.status_code == 200 and "正式保存·已入库" in response.get_data(as_text=True)
    response = client.post(f"/purchases/{purchase_id}/void", data={"reason": "页面测试作废"}, follow_redirects=True)
    assert response.status_code == 200 and "页面测试作废" in response.get_data(as_text=True)


def test_purchase_draft_can_move_to_recycle_bin_from_detail():
    product_id = _product()
    draft = create_purchase_order(
        "NH202610010011", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-delete-001", status="draft", customer_id=_customer()
    )
    client = create_app().test_client()
    response = client.post(f"/purchases/{draft['order_id']}/delete", follow_redirects=True)
    assert response.status_code == 200 and "NH202610010011" in response.get_data(as_text=True)
    with get_db() as conn:
        order = conn.execute("SELECT status, deleted_at FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()
    assert order["status"] == "draft" and order["deleted_at"]


def test_purchase_appears_as_separate_type_in_document_management():
    product_id = _product()
    create_purchase_order(
        "NH202610010010", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-list-001", customer_id=_customer()
    )
    client = create_app().test_client()
    response = client.get("/orders/?date_mode=range&start_date=2026-10-01&end_date=2026-10-01")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "拿货单" in html and "NH202610010010" in html
