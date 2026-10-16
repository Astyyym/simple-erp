from threading import Barrier, Thread

import pytest

from erp.db import get_db, init_db
from erp import create_app
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.purchases import create_purchase_order


def _product():
    init_db()

    return create_product("销售测试商品S1", "个")


def test_saved_sale_keeps_line_amounts_after_later_purchase():
    """已生效销售单的明细快照不因后续拿货而改变（v15 起不再有成本快照列）。"""
    product_id = _product()
    customer_id = create_customer("销售测试客户")

    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010001",
        [{"product_name": "销售测试商品S1", "unit": "个", "unit_price_yuan": "30", "quantity": "3"}],
        status="saved",
        order_date="2026-10-01",
    )

    with get_db() as conn:
        item = conn.execute(
            "SELECT product_id, quantity, subtotal_cents FROM order_items WHERE order_id=?",
            (sale_id,),
        ).fetchone()
    assert tuple(item) == (product_id, "3", 9000)

    # 后续拿货单只登记自己的流水，不动已生效销售单。
    create_purchase_order(
        "NH202610010001",
        "2026-10-01",
        [{"product_id": product_id, "quantity": "3", "unit_cost_yuan": "20"}],
        request_key="sale-follow-up-purchase-001",
        customer_id=customer_id,
    )

    with get_db() as conn:
        item_after = conn.execute(
            "SELECT product_id, quantity, subtotal_cents FROM order_items WHERE order_id=?",
            (sale_id,),
        ).fetchone()
        order = conn.execute("SELECT total_amount_cents FROM orders WHERE id=?", (sale_id,)).fetchone()
    assert tuple(item_after) == (product_id, "3", 9000)
    assert order["total_amount_cents"] == 9000


def test_sales_browser_submission_retry_returns_original_order_without_duplicate_document():
    """同一 request_key 重交页面表单 → 只落一张单，重交返回原单详情。"""
    product_id = _product()
    customer_id = create_customer("销售页面幂等客户")
    client = create_app().test_client()
    form = {
        "customer_id": str(customer_id),
        "customer_name": "销售页面幂等客户",
        "order_date": "2026-10-01",
        "status": "saved",
        "request_key": "web-sale-retry-001",
        "product_id": [str(product_id)],
        "product_name": ["销售测试商品"],
        "unit": ["个"],
        "quantity": ["1"],
        "unit_price": ["30"],
    }
    first = client.post("/orders/create", data=form)
    retry = client.post("/orders/create", data=form)
    assert first.status_code == 302 and retry.status_code == 302
    with get_db() as conn:
        orders = conn.execute("SELECT id, order_no FROM orders WHERE request_key=?", ("web-sale-retry-001",)).fetchall()
    assert len(orders) == 1 and retry.headers["Location"].endswith("/orders/")


def test_saved_sale_can_be_re_edited_and_totals_follow_new_rows():
    """决策 ④（2026-10-16）：正式销售单编辑锁已撤；库存系统删除后不再有「已过账」概念。"""
    product_id = _product()
    customer_id = create_customer("销售编辑客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010004",
        [{"product_name": "销售测试商品S1", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
    )

    from erp.services.accounting import update_order_from_typed_rows

    update_order_from_typed_rows(
        sale_id,
        customer_id,
        "MD202610010004",
        [{"product_id": product_id, "product_name": "销售测试商品", "unit": "个", "unit_price_yuan": "30", "quantity": "3"}],
        status="saved",
        order_date="2026-10-01",
        order_type="sale",
    )

    with get_db() as conn:
        order = conn.execute("SELECT status, total_amount_cents FROM orders WHERE id=?", (sale_id,)).fetchone()
        item = conn.execute("SELECT quantity, subtotal_cents FROM order_items WHERE order_id=?", (sale_id,)).fetchone()
    assert tuple(order) == ("saved", 9000)
    assert tuple(item) == ("3", 9000)


def _saved_sale_for_return(*, quantity="5", customer_name="退货客户"):
    product_id = _product()
    customer_id = create_customer(customer_name)
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010005",
        [{"product_id": product_id, "product_name": "销售测试商品S1", "unit": "个", "unit_price_yuan": "20", "quantity": quantity}],
        status="saved",
        order_date="2026-10-01",
    )
    with get_db() as conn:
        sale_item = conn.execute("SELECT id FROM order_items WHERE order_id=?", (sale_id,)).fetchone()
    return product_id, customer_id, sale_id, int(sale_item["id"])


def test_partial_returns_use_original_line_amount_and_release_quantity():
    """分次退货按原销售行单价计价，并把原单可退额度依次占满。"""
    from erp.services.accounting import create_return_order_from_source

    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return()
    first = create_return_order_from_source(
        customer_id,
        "MD202610010006",
        sale_id,
        [{"source_item_id": sale_item_id, "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
    )
    with get_db() as conn:
        first_order = conn.execute("SELECT total_amount_cents, source_order_id FROM orders WHERE id=?", (first,)).fetchone()
        first_item = conn.execute("SELECT source_item_id, quantity, subtotal_cents FROM order_items WHERE order_id=?", (first,)).fetchone()
    assert tuple(first_order) == (-4000, sale_id)
    assert tuple(first_item) == (sale_item_id, "2", 4000)

    second = create_return_order_from_source(
        customer_id,
        "MD202610010007",
        sale_id,
        [{"source_item_id": sale_item_id, "quantity": "3"}],
        status="saved",
        order_date="2026-10-01",
    )
    with get_db() as conn:
        second_order = conn.execute("SELECT total_amount_cents FROM orders WHERE id=?", (second,)).fetchone()
        second_item = conn.execute("SELECT subtotal_cents FROM order_items WHERE order_id=?", (second,)).fetchone()
        returned_quantity = conn.execute(
            "SELECT SUM(CAST(quantity AS REAL)) FROM order_items WHERE source_item_id=?", (sale_item_id,)
        ).fetchone()[0]
    assert second_order["total_amount_cents"] == -6000
    assert second_item["subtotal_cents"] == 6000
    assert returned_quantity == 5  # 2 + 3 = 原销售单的 5 个，额度刚好占满


def test_return_rejects_wrong_customer_and_over_quantity_without_partial_write():
    from erp.services.accounting import create_return_order_from_source

    _, customer_id, sale_id, sale_item_id = _saved_sale_for_return()
    other_customer = create_customer("退货他人")
    with pytest.raises(ValueError, match="同一客户"):
        create_return_order_from_source(
            other_customer, "MD202610010008", sale_id, [{"source_item_id": sale_item_id, "quantity": "1"}], status="saved", order_date="2026-10-01"
        )
    with pytest.raises(ValueError, match="超过可退数量"):
        create_return_order_from_source(
            customer_id, "MD202610010009", sale_id, [{"source_item_id": sale_item_id, "quantity": "6"}], status="saved", order_date="2026-10-01"
        )
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE order_type='return'").fetchone()[0] == 0


def test_sale_with_live_return_can_still_be_voided_and_both_orders_end_void():
    """2026-10-07 用户决策：有有效退货指向的销售单也能作废（不再要求先作废退货）。

    退货单本身继续有效、可独立作废；v15 起作废只改状态，不再冲回库存（决策 ②/③）。
    """
    from erp.services.accounting import create_return_order_from_source, void_order

    _, customer_id, sale_id, sale_item_id = _saved_sale_for_return()
    return_id = create_return_order_from_source(
        customer_id, "MD202610010010", sale_id, [{"source_item_id": sale_item_id, "quantity": "2"}], status="saved", order_date="2026-10-01"
    )
    # 销售单现在可以直接作废，不再被有效退货挡住。
    void_order(sale_id, "误开销售")
    with get_db() as conn:
        assert conn.execute("SELECT status FROM orders WHERE id=?", (sale_id,)).fetchone()["status"] == "void"
    void_order(return_id, "客户取消退货")
    with get_db() as conn:
        statuses = conn.execute("SELECT id, status FROM orders WHERE id IN (?, ?) ORDER BY id", (sale_id, return_id)).fetchall()
    assert [row["status"] for row in statuses] == ["void", "void"]


def test_return_browser_path_posts_decoupled_manual_return():
    product_id, customer_id, _, _ = _saved_sale_for_return(customer_name="退货页面客户")
    client = create_app().test_client()
    new_page = client.get("/orders/return/new")
    html = new_page.get_data(as_text=True)
    assert new_page.status_code == 200
    # 退货单保持手工开单；原销售单是可选的检索式关联（不强制、不改变计价）。
    assert "原销售单（可选）" in html
    assert 'id="returnSourceInput"' in html
    assert 'name="source_order_id"' in html

    response = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer_id),
            "customer_name": "退货页面客户",
            "product_id": [str(product_id)],
            "product_name": ["销售测试商品"],
            "unit": ["个"],
            "quantity": ["1"],
            "unit_price": ["20.00"],
            "order_date": "2026-10-01",
            "status": "saved",
        },
    )
    assert response.status_code == 302
    with get_db() as conn:
        returned = conn.execute("SELECT order_type, source_order_id, total_amount_cents FROM orders WHERE order_type='return'").fetchone()
    assert tuple(returned) == ("return", None, -2000)


def test_return_draft_can_be_edited_then_saved_and_saved_return_stays_editable():
    """决策 ④（2026-10-16）：退货草稿可编辑，正式退货单同样不再锁定。"""
    from erp.services.accounting import create_return_order_from_source, update_return_draft_from_source

    _, customer_id, sale_id, sale_item_id = _saved_sale_for_return(customer_name="退货草稿客户")
    return_id = create_return_order_from_source(
        customer_id, "MD202610010011", sale_id, [{"source_item_id": sale_item_id, "quantity": "1"}], status="draft", order_date="2026-10-01"
    )
    update_return_draft_from_source(
        return_id,
        [{"source_item_id": sale_item_id, "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
        notes="草稿确认",
    )
    with get_db() as conn:
        order = conn.execute("SELECT status, total_amount_cents, notes FROM orders WHERE id=?", (return_id,)).fetchone()
    assert tuple(order) == ("saved", -4000, "草稿确认")

    # 正式退货单仍可继续编辑（可退额度按「排除本单」重算，不会自己占掉自己的额度）。
    update_return_draft_from_source(
        return_id,
        [{"source_item_id": sale_item_id, "quantity": "3"}],
        status="saved",
        order_date="2026-10-01",
        notes="正式单可改",
    )
    with get_db() as conn:
        order = conn.execute("SELECT status, total_amount_cents, notes FROM orders WHERE id=?", (return_id,)).fetchone()
    assert tuple(order) == ("saved", -6000, "正式单可改")


def test_return_submission_is_idempotent_and_lifecycle_keeps_void_state():
    """幂等重交 + 作废→删除→恢复：恢复只恢复可见性，不回滚作废。"""
    from erp.services.accounting import create_return_order_from_source, delete_order, restore_order, update_return_draft_from_source, void_order

    _, customer_id, sale_id, sale_item_id = _saved_sale_for_return(quantity="2", customer_name="退货生命周期客户")
    rows = [{"source_item_id": sale_item_id, "quantity": "1"}]
    draft_id = create_return_order_from_source(
        customer_id,
        "MD202610010019",
        sale_id,
        rows,
        status="draft",
        order_date="2026-10-01",
        request_key="return-idempotent-001",
    )
    retry_id = create_return_order_from_source(
        customer_id,
        "MD202610010019",
        sale_id,
        [{"source_item_id": sale_item_id, "quantity": "1.000"}],
        status="draft",
        order_date="2026-10-01",
        request_key="return-idempotent-001",
    )
    assert retry_id == draft_id
    delete_order(draft_id, "先删除退货草稿")
    restore_order(draft_id)
    with get_db() as conn:
        version = conn.execute("SELECT version FROM orders WHERE id=?", (draft_id,)).fetchone()["version"]
    update_return_draft_from_source(
        draft_id,
        rows,
        status="saved",
        order_date="2026-10-01",
        expected_version=version,
    )
    void_order(draft_id, "退货生命周期测试作废")
    delete_order(draft_id, "作废后删除退货")
    restore_order(draft_id)
    with get_db() as conn:
        order = conn.execute("SELECT status, deleted_at FROM orders WHERE id=?", (draft_id,)).fetchone()
    assert tuple(order) == ("void", None)


def test_sale_draft_to_saved_updates_status_and_line_amounts():
    from erp.services.accounting import update_order_from_typed_rows

    product_id = _product()
    customer_id = create_customer("销售草稿客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010012",
        [{"product_id": product_id, "product_name": "销售测试商品S1", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="draft",
        order_date="2026-10-01",
    )
    update_order_from_typed_rows(
        sale_id,
        customer_id,
        "MD202610010012",
        [{"product_id": product_id, "product_name": "销售测试商品", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
        order_type="sale",
    )
    with get_db() as conn:
        order = conn.execute("SELECT status, total_amount_cents FROM orders WHERE id=?", (sale_id,)).fetchone()
        item = conn.execute("SELECT quantity, subtotal_cents FROM order_items WHERE order_id=?", (sale_id,)).fetchone()
    assert tuple(order) == ("saved", 6000)
    assert tuple(item) == ("2", 6000)


def test_sale_submission_is_idempotent_and_rejects_same_key_with_different_content():
    product_id = _product()
    customer_id = create_customer("销售幂等客户")
    rows = [{"product_id": product_id, "product_name": "销售测试商品S1", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}]

    first = create_order_from_typed_rows(
        customer_id, "MD202610010014", rows, status="saved", order_date="2026-10-01", request_key="sale-idempotent-001"
    )
    retry = create_order_from_typed_rows(
        customer_id, "MD202610010014", [{**rows[0], "quantity": "2.000"}], status="saved", order_date="2026-10-01", request_key="sale-idempotent-001"
    )
    assert retry == first
    with pytest.raises(ValueError, match="幂等键内容不一致"):
        create_order_from_typed_rows(
            customer_id, "MD202610010015", [{**rows[0], "quantity": "3"}], status="saved", order_date="2026-10-01", request_key="sale-idempotent-001"
        )
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_stale_order_version_rejects_edit_without_overwriting_newer_state():
    from erp.services.accounting import update_order_from_typed_rows

    product_id = _product()
    customer_id = create_customer("版本冲突客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010016",
        [{"product_id": product_id, "product_name": "销售测试商品", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="draft",
        order_date="2026-10-01",
    )
    with get_db() as conn:
        version = conn.execute("SELECT version FROM orders WHERE id=?", (order_id,)).fetchone()["version"]
    update_order_from_typed_rows(
        order_id,
        customer_id,
        "MD202610010016",
        [{"product_id": product_id, "product_name": "销售测试商品", "unit": "个", "unit_price_yuan": "21", "quantity": "1"}],
        status="draft",
        order_date="2026-10-01",
        expected_version=version,
    )
    with pytest.raises(ValueError, match="版本冲突"):
        update_order_from_typed_rows(
            order_id,
            customer_id,
            "MD202610010016",
            [{"product_id": product_id, "product_name": "销售测试商品", "unit": "个", "unit_price_yuan": "22", "quantity": "1"}],
            status="draft",
            order_date="2026-10-01",
            expected_version=version,
        )
    with get_db() as conn:
        row = conn.execute("SELECT version, total_amount_cents FROM orders WHERE id=?", (order_id,)).fetchone()
    assert tuple(row) == (version + 1, 2100)


def test_two_returns_cannot_both_claim_the_last_return_quantity():
    from erp.services.accounting import create_return_order_from_source

    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return(quantity="1", customer_name="并发退货客户")
    start = Barrier(2)
    results = []

    def submit(index):
        try:
            start.wait(timeout=5)
            order_id = create_return_order_from_source(
                customer_id,
                f"MD20261001002{index}",
                sale_id,
                [{"source_item_id": sale_item_id, "quantity": "1"}],
                status="saved",
                order_date="2026-10-01",
                request_key=f"concurrent-return-{index}",
            )
            results.append(("ok", order_id))
        except Exception as exc:
            results.append(("error", str(exc)))

    threads = [Thread(target=submit, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 2
    assert [result[0] for result in results].count("ok") == 1
    assert any("超过可退数量" in result[1] for result in results if result[0] == "error")
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE order_type='return'").fetchone()[0] == 1
        assert conn.execute("SELECT SUM(CAST(quantity AS REAL)) FROM order_items WHERE source_item_id=?", (sale_item_id,)).fetchone()[0] == 1


def test_sale_lifecycle_requires_void_before_delete_and_restore_keeps_void_state():
    from erp.services.accounting import delete_order, restore_order

    product_id = _product()
    customer_id = create_customer("销售生命周期客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010018",
        [{"product_id": product_id, "product_name": "销售测试商品", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_date="2026-10-01",
    )
    with pytest.raises(ValueError, match="先作废"):
        delete_order(order_id, "直接删除")
    from erp.services.accounting import void_order
    void_order(order_id, "生命周期测试作废")
    delete_order(order_id, "移入回收站")
    with get_db() as conn:
        row = conn.execute("SELECT status, deleted_at FROM orders WHERE id=?", (order_id,)).fetchone()
    assert row["status"] == "void" and row["deleted_at"]
    restore_order(order_id)
    with get_db() as conn:
        row = conn.execute("SELECT status, deleted_at FROM orders WHERE id=?", (order_id,)).fetchone()
    assert row["status"] == "void" and row["deleted_at"] is None


def test_order_lifecycle_routes_auto_reverse_then_delete_and_restore_keeps_void_state():
    """列表删除正式单据 = 自动冲回（作废）+ 软删除；恢复只恢复可见性，不回滚作废。"""
    product_id = _product()
    customer_id = create_customer("销售生命周期页面客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010020",
        [{"product_id": product_id, "product_name": "销售测试商品", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_date="2026-10-01",
    )
    client = create_app().test_client()
    deleted = client.post(f"/orders/{order_id}/delete")
    assert deleted.status_code == 302
    with get_db() as conn:
        row = conn.execute("SELECT status, void_reason, deleted_at FROM orders WHERE id=? AND deleted_at IS NOT NULL", (order_id,)).fetchone()
    assert row is not None and row["status"] == "void" and row["void_reason"]
    restored = client.post(f"/recycle/order/{order_id}/restore")
    assert restored.status_code == 302
    with get_db() as conn:
        row = conn.execute("SELECT status, deleted_at FROM orders WHERE id=?", (order_id,)).fetchone()
    assert tuple(row) == ("void", None)