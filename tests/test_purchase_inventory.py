from decimal import Decimal

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product
from erp.services.purchases import create_purchase_order, finalize_purchase_order, update_purchase_draft, void_purchase_order
from erp.routes.recycle import _purge_one


def _customer():
    with get_db() as conn:
        row = conn.execute("SELECT id FROM customers WHERE name='虚构拿货往来对象'").fetchone()
    return row['id'] if row else create_customer('虚构拿货往来对象')


def _product():
    init_db()
    product_id = create_product("拿货测试商品P1", "个")

    return product_id


def test_purchase_saves_order_and_line_amounts():
    """拿货单与明细金额落库；v15 起不再写库存/成本（拿货单价 unit_cost_cents 保留）。"""
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
        order = conn.execute("SELECT status, source_notes FROM purchase_orders WHERE id=?", (result["order_id"],)).fetchone()
        item = conn.execute(
            "SELECT product_id, quantity_3dp, unit_cost_cents, subtotal_cents FROM purchase_order_items WHERE purchase_order_id=?",
            (result["order_id"],),
        ).fetchone()
    assert tuple(order) == ("saved", "市场拿货")
    assert tuple(item) == (product_id, 10000, 1400, 14000)


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


def test_multi_line_purchase_keeps_both_lines_of_same_product():
    product_id = _product()
    result = create_purchase_order(
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
        items = conn.execute(
            "SELECT quantity_3dp, unit_cost_cents FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id",
            (result["order_id"],),
        ).fetchall()
    assert [tuple(row) for row in items] == [(1000, 1400), (2000, 2000)]
    assert result["total_amount_cents"] == 5400


def test_purchase_rejects_invalid_rows_without_partial_write():
    """明细校验失败 → 整单不落库（不再有「尚未启用库存」拦截，决策 ②/③）。"""
    product_id = _product()
    with pytest.raises(ValueError, match="数量最多保留3位小数"):
        create_purchase_order(
            "NH202610010004", "2026-10-01", [{"product_id": product_id, "quantity": "1.0001", "unit_cost_yuan": "14"}], request_key="purchase-004", customer_id=_customer()
        )
    with pytest.raises(ValueError, match="必须选择具体商品"):
        create_purchase_order(
            "NH202610010003", "2026-10-01", [{"product_id": "", "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-003", customer_id=_customer()
        )
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0


def test_purchase_draft_of_any_product_can_be_finalized():
    """v15：库存系统删除后，任何已建档商品都能直接正式保存，无「未启用库存」拦截。"""
    init_db()
    product_id = create_product("普通草稿商品P3", "个")
    draft = create_purchase_order(
        "NH202610010012", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-plain-draft-001", status="draft", customer_id=_customer()
    )
    with get_db() as conn:
        assert conn.execute("SELECT status FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()["status"] == "draft"
    finalized = finalize_purchase_order(draft["order_id"])
    assert finalized["status"] == "saved"
    with get_db() as conn:
        assert conn.execute("SELECT status FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()["status"] == "saved"


def test_purchase_browser_path_saves_and_renders_internal_detail():
    product_id = _product()
    client = create_app().test_client()
    new_page = client.get("/purchases/new")
    assert new_page.status_code == 200
    # 统一七列明细（序号/产品名称/单位/数量/单价(元)/金额/操作）；成本列已随库存系统删除。
    new_html = new_page.get_data(as_text=True)
    assert "序号" in new_html and "单价(元)" in new_html and "金额" in new_html and "操作" in new_html
    assert "成本(元)" not in new_html
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


def test_purchase_draft_can_be_edited_then_finalized_and_saved_order_stays_editable():
    """决策 ④（2026-10-16）：正式拿货单编辑锁已撤，可继续编辑。"""
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
        assert conn.execute("SELECT status FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()["status"] == "draft"

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
    assert (order["status"], order["source_notes"], order["total_amount_cents"]) == ("saved", "修改后的内部货源", 4500)
    assert tuple(item) == (3000, 1500)

    # 决策 ④：正式拿货单不再锁定，编辑仍然生效。
    update_purchase_draft(
        draft["order_id"],
        "2026-10-01",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "99"}],
        source_notes="正式单可改",
        customer_id=_customer(),
        status="saved",
    )
    with get_db() as conn:
        order = conn.execute("SELECT status, source_notes, total_amount_cents FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone()
    assert tuple(order) == ("saved", "正式单可改", 9900)
    assert finalize_purchase_order(draft["order_id"])["total_amount_cents"] == 9900


def test_purchase_void_allows_non_last_order_and_both_end_void():
    """2026-10-07 用户决策：拿货单不再要求「必须是该商品末笔」即可作废。

    v15 起作废只改状态，不再反向过账库存（决策 ②/③）。
    """
    product_id = _product()
    first = create_purchase_order(
        "NH202610010007", "2026-10-01", [{"product_id": product_id, "quantity": "2", "unit_cost_yuan": "14"}], request_key="purchase-void-001", customer_id=_customer()
    )
    second = create_purchase_order(
        "NH202610010008", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "20"}], request_key="purchase-void-002", customer_id=_customer()
    )

    # 第一张不是末笔（后面还有 second），现在允许作废。
    voided = void_purchase_order(first["order_id"], "第一张单填错")
    assert voided["status"] == "void"
    with get_db() as conn:
        assert conn.execute("SELECT status FROM purchase_orders WHERE id=?", (second["order_id"],)).fetchone()["status"] == "saved"

    voided_second = void_purchase_order(second["order_id"], "第二张也填错")
    assert voided_second["status"] == "void"
    with get_db() as conn:
        statuses = conn.execute(
            "SELECT status FROM purchase_orders WHERE id IN (?, ?) ORDER BY id", (first["order_id"], second["order_id"])
        ).fetchall()
    assert [row["status"] for row in statuses] == ["void", "void"]


def test_purchase_draft_purge_removes_order_and_items():
    """回收站彻底删除拿货单：单据与明细一起消失，不留外键悬挂（v15 起无库存流水可清）。"""
    product_id = _product()
    draft = create_purchase_order(
        "NH202610010009", "2026-10-01", [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "14"}], request_key="purchase-recycle-001", status="draft", customer_id=_customer()
    )
    with get_db() as conn:
        conn.execute("UPDATE purchase_orders SET deleted_at=CURRENT_TIMESTAMP, delete_reason='测试删除' WHERE id=?", (draft["order_id"],))
        _purge_one(conn, "purchase_order", draft["order_id"])
        assert conn.execute("SELECT 1 FROM purchase_orders WHERE id=?", (draft["order_id"],)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM purchase_order_items WHERE purchase_order_id=?", (draft["order_id"],)).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


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
    assert response.status_code == 200 and "草稿" in html and "正式保存" in html
    with get_db() as conn:
        purchase_id = conn.execute("SELECT id FROM purchase_orders WHERE request_key=?", ("web-purchase-draft-001",)).fetchone()["id"]
    response = client.post(f"/purchases/{purchase_id}/finalize", follow_redirects=True)
    finalized_html = response.get_data(as_text=True)
    # 状态显示「正式保存」，且草稿专属的「正式保存」按钮已消失（/finalize 入口不再渲染）。
    assert response.status_code == 200 and "正式保存" in finalized_html and "/finalize" not in finalized_html
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
