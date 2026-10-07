from decimal import Decimal
from threading import Barrier, Thread

import pytest

from erp.db import get_db, init_db
from erp import create_app
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import create_purchase_order, initialize_product


def _enabled_product():
    init_db()
    product_id = create_product("销售成本商品", "S1", "个", 3000)
    initialize_product(product_id, "10", "10", "2026-10-01", "系统上线期初", "sale-init-001")
    return product_id


def test_saved_sale_decrements_inventory_and_keeps_historical_cost_after_later_purchase():
    product_id = _enabled_product()
    customer_id = create_customer("销售成本客户")

    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010001",
        [{"product_name": "销售成本商品", "spec": "S1", "unit": "个", "unit_price_yuan": "30", "quantity": "3"}],
        status="saved",
        order_date="2026-10-01",
    )

    with get_db() as conn:
        state = conn.execute(
            "SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        item = conn.execute(
            "SELECT product_id, quantity, subtotal_cents, cost_total_micro, unit_cost_micro FROM order_items WHERE order_id=?",
            (sale_id,),
        ).fetchone()
    assert tuple(state) == (7000, 70_000_000, 10_000_000)
    assert tuple(item) == (product_id, "3", 9000, 30_000_000, 10_000_000)

    create_purchase_order(
        "NH202610010001",
        "2026-10-01",
        [{"product_id": product_id, "quantity": "3", "unit_cost_yuan": "20"}],
        request_key="sale-follow-up-purchase-001",
        customer_id=customer_id,
    )

    with get_db() as conn:
        state = conn.execute(
            "SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        item = conn.execute(
            "SELECT cost_total_micro, unit_cost_micro FROM order_items WHERE order_id=?",
            (sale_id,),
        ).fetchone()
    assert tuple(state) == (10000, 130_000_000, 13_000_000)
    assert tuple(item) == (30_000_000, 10_000_000)


def test_saved_sale_with_insufficient_inventory_rolls_back_order_and_inventory():
    product_id = _enabled_product()
    customer_id = create_customer("库存不足客户")

    with pytest.raises(ValueError, match="库存不足"):
        create_order_from_typed_rows(
            customer_id,
            "MD202610010002",
            [{"product_name": "销售成本商品", "spec": "S1", "unit": "个", "unit_price_yuan": "30", "quantity": "11"}],
            status="saved",
            order_date="2026-10-01",
        )

    with get_db() as conn:
        state = conn.execute(
            "SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 0
    assert tuple(state) == (10000, 100_000_000)


def test_sales_browser_path_returns_inventory_error_and_detail_shows_cost_snapshot():
    product_id = _enabled_product()
    customer_id = create_customer("销售页面客户")
    client = create_app().test_client()

    response = client.post(
        "/orders/create",
        data={
            "customer_id": str(customer_id),
            "customer_name": "销售页面客户",
            "order_date": "2026-10-01",
            "status": "saved",
            "product_id": [str(product_id)],
            "product_name": ["销售成本商品"],
            "unit": ["个"],
            "quantity": ["11"],
            "unit_price": ["30"],
        },
    )
    assert response.status_code == 400
    assert "库存不足" in response.get_data(as_text=True)

    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010003",
        [{"product_name": "销售成本商品", "spec": "S1", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-10-01",
    )
    detail = client.get(f"/orders/{sale_id}")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "销售时成本" in html and "<td>10.00</td>" in html and "成本总额" in html and "内部毛利润" in html
    assert "10.000000" not in html


def test_sales_browser_submission_retry_returns_original_order_without_double_posting():
    product_id = _enabled_product()
    customer_id = create_customer("销售页面幂等客户")
    client = create_app().test_client()
    form = {
        "customer_id": str(customer_id),
        "customer_name": "销售页面幂等客户",
        "order_date": "2026-10-01",
        "status": "saved",
        "request_key": "web-sale-retry-001",
        "product_id": [str(product_id)],
        "product_name": ["销售成本商品"],
        "unit": ["个"],
        "quantity": ["1"],
        "unit_price": ["30"],
    }
    first = client.post("/orders/create", data=form)
    retry = client.post("/orders/create", data=form)
    assert first.status_code == 302 and retry.status_code == 302
    with get_db() as conn:
        orders = conn.execute("SELECT id, order_no FROM orders WHERE request_key=?", ("web-sale-retry-001",)).fetchall()
        state = conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert len(orders) == 1 and retry.headers["Location"].endswith("/orders/")
    assert state["quantity_3dp"] == 9000


def test_inventory_posted_sale_rejects_economic_re_edit_without_changing_stock():
    product_id = _enabled_product()
    customer_id = create_customer("销售锁定客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010004",
        [{"product_name": "销售成本商品", "spec": "S1", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
    )

    from erp.services.accounting import update_order_from_typed_rows

    with pytest.raises(ValueError, match="库存已过账"):
        update_order_from_typed_rows(
            sale_id,
            customer_id,
            "MD202610010004",
            [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "30", "quantity": "3"}],
            status="saved",
            order_date="2026-10-01",
            order_type="sale",
        )

    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        item = conn.execute("SELECT quantity, cost_total_micro FROM order_items WHERE order_id=?", (sale_id,)).fetchone()
    assert state["quantity_3dp"] == 8000
    assert tuple(item) == ("2", 20_000_000)


def _saved_sale_for_return(*, quantity="5", customer_name="退货客户"):
    product_id = _enabled_product()
    customer_id = create_customer(customer_name)
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010005",
        [{"product_id": product_id, "product_name": "销售成本商品", "spec": "S1", "unit": "个", "unit_price_yuan": "20", "quantity": quantity}],
        status="saved",
        order_date="2026-10-01",
    )
    with get_db() as conn:
        sale_item = conn.execute("SELECT id FROM order_items WHERE order_id=?", (sale_id,)).fetchone()
    return product_id, customer_id, sale_id, int(sale_item["id"])


def test_partial_returns_use_original_line_amount_and_cost_and_restore_inventory():
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
        first_item = conn.execute("SELECT source_item_id, quantity, subtotal_cents, cost_total_micro FROM order_items WHERE order_id=?", (first,)).fetchone()
        state = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert tuple(first_order) == (-4000, sale_id)
    assert tuple(first_item) == (sale_item_id, "2", 4000, 20_000_000)
    assert tuple(state) == (7000, 70_000_000)

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
        second_item = conn.execute("SELECT subtotal_cents, cost_total_micro FROM order_items WHERE order_id=?", (second,)).fetchone()
        state = conn.execute("SELECT quantity_3dp, cost_total_micro, avg_cost_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert second_order["total_amount_cents"] == -6000
    assert tuple(second_item) == (6000, 30_000_000)
    assert tuple(state) == (10000, 100_000_000, 10_000_000)


def test_return_rejects_wrong_customer_duplicate_and_over_quantity_without_partial_write():
    from erp.services.accounting import create_return_order_from_source

    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return()
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
        state = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert tuple(state) == (5000, 50_000_000)


def test_sale_with_live_return_can_still_be_voided_and_both_release_quantity():
    """2026-10-07 用户决策：有有效退货指向的销售单也能作废（不再要求先作废退货）。

    退货单本身继续有效，两张各自独立冲回自己的库存流水。
    """
    from erp.services.accounting import create_return_order_from_source, void_order

    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return()
    return_id = create_return_order_from_source(
        customer_id, "MD202610010010", sale_id, [{"source_item_id": sale_item_id, "quantity": "2"}], status="saved", order_date="2026-10-01"
    )
    # 销售单现在可以直接作废，不再被有效退货挡住。
    void_order(sale_id, "误开销售")
    with get_db() as conn:
        assert conn.execute("SELECT status FROM orders WHERE id=?", (sale_id,)).fetchone()["status"] == "void"
    void_order(return_id, "客户取消退货")
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp, cost_total_micro FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        statuses = conn.execute("SELECT id, status FROM orders WHERE id IN (?, ?) ORDER BY id", (sale_id, return_id)).fetchall()
    assert tuple(state) == (10000, 100_000_000)
    assert [row["status"] for row in statuses] == ["void", "void"]


def test_return_browser_path_posts_decoupled_manual_return():
    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return(customer_name="退货页面客户")
    client = create_app().test_client()
    new_page = client.get("/orders/return/new")
    html = new_page.get_data(as_text=True)
    assert new_page.status_code == 200
    # 退货单保持手工开单；原销售单改为可选关联追溯（不强制、不改变计价）。
    assert "原销售单（可选）" in html
    assert 'id="returnSourceSelect"' in html
    assert "不关联，直接手工录入" in html
    with get_db() as conn:
        before = conn.execute(
            "SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)
        ).fetchone()[0]

    response = client.post(
        "/orders/return/create",
        data={
            "customer_id": str(customer_id),
            "customer_name": "退货页面客户",
            "product_id": [str(product_id)],
            "product_name": ["销售成本商品"],
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
        after = conn.execute(
            "SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)
        ).fetchone()[0]
    assert tuple(returned) == ("return", None, -2000)
    # 手工退货按当前移动平均成本回冲库存
    assert after == before + 1000


def test_return_draft_can_be_edited_and_finalized_once():
    from erp.services.accounting import create_return_order_from_source, update_return_draft_from_source

    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return(customer_name="退货草稿客户")
    return_id = create_return_order_from_source(
        customer_id, "MD202610010011", sale_id, [{"source_item_id": sale_item_id, "quantity": "1"}], status="draft", order_date="2026-10-01"
    )
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert state["quantity_3dp"] == 5000
    update_return_draft_from_source(
        return_id,
        [{"source_item_id": sale_item_id, "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
        notes="草稿确认",
    )
    with get_db() as conn:
        order = conn.execute("SELECT status, total_amount_cents, notes FROM orders WHERE id=?", (return_id,)).fetchone()
        state = conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert tuple(order) == ("saved", -4000, "草稿确认")
    assert state["quantity_3dp"] == 7000
    with pytest.raises(ValueError, match="正式退货单经济字段已锁定"):
        update_return_draft_from_source(return_id, [{"source_item_id": sale_item_id, "quantity": "1"}], status="saved", order_date="2026-10-01")


def test_return_submission_is_idempotent_and_full_lifecycle_never_restores_effect():
    from erp.services.accounting import create_return_order_from_source, delete_order, restore_order, update_return_draft_from_source, void_order

    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return(quantity="2", customer_name="退货生命周期客户")
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
        state = conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
    assert tuple(order) == ("void", None)
    assert state["quantity_3dp"] == 8000


def test_sale_draft_to_saved_posts_inventory_and_cost_snapshot_atomically():
    from erp.services.accounting import update_order_from_typed_rows

    product_id = _enabled_product()
    customer_id = create_customer("销售草稿客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010012",
        [{"product_id": product_id, "product_name": "销售成本商品", "spec": "S1", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="draft",
        order_date="2026-10-01",
    )
    update_order_from_typed_rows(
        sale_id,
        customer_id,
        "MD202610010012",
        [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
        order_type="sale",
    )
    with get_db() as conn:
        state = conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        item = conn.execute("SELECT unit_cost_micro, cost_total_micro FROM order_items WHERE order_id=?", (sale_id,)).fetchone()
        order = conn.execute("SELECT status FROM orders WHERE id=?", (sale_id,)).fetchone()
    assert state["quantity_3dp"] == 8000
    assert tuple(item) == (10_000_000, 20_000_000)
    assert order["status"] == "saved"


def test_profit_summary_uses_cost_snapshots_and_excludes_void_orders():
    from erp.services.accounting import create_return_order_from_source, void_order
    from erp.services.profit import summarize_profit

    product_id, customer_id, sale_id, sale_item_id = _saved_sale_for_return(customer_name="利润客户")
    return_id = create_return_order_from_source(
        customer_id,
        "MD202610010013",
        sale_id,
        [{"source_item_id": sale_item_id, "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
    )
    summary = summarize_profit(start_date="2026-10-01", end_date="2026-10-01", customer_id=customer_id)
    assert summary["sales_cents"] == 10000
    assert summary["returns_cents"] == 4000
    assert summary["net_sales_cents"] == 6000
    assert summary["net_cost_cents"] == 3000
    assert summary["gross_profit_cents"] == 3000
    assert summary["cost_complete"] is True
    assert summary["unknown_order_count"] == 0

    void_order(return_id, "利润测试撤销")
    after_void = summarize_profit(customer_id=customer_id)
    assert after_void["net_sales_cents"] == 10000
    assert after_void["net_cost_cents"] == 5000
    assert after_void["gross_profit_cents"] == 5000

    create_purchase_order(
        "NH202610010013",
        "2026-10-01",
        [{"product_id": product_id, "quantity": "5", "unit_cost_yuan": "20"}],
        request_key="profit-follow-up-purchase-001",
        customer_id=customer_id,
    )
    after_purchase = summarize_profit(customer_id=customer_id)
    assert after_purchase["gross_profit_cents"] == after_void["gross_profit_cents"]


def test_sale_submission_is_idempotent_and_rejects_same_key_with_different_content():
    product_id = _enabled_product()
    customer_id = create_customer("销售幂等客户")
    rows = [{"product_id": product_id, "product_name": "销售成本商品", "spec": "S1", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}]

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
        assert conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()[0] == 8000


def test_stale_order_version_rejects_edit_without_overwriting_newer_state():
    from erp.services.accounting import update_order_from_typed_rows

    product_id = _enabled_product()
    customer_id = create_customer("版本冲突客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010016",
        [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="draft",
        order_date="2026-10-01",
    )
    with get_db() as conn:
        version = conn.execute("SELECT version FROM orders WHERE id=?", (order_id,)).fetchone()["version"]
    update_order_from_typed_rows(
        order_id,
        customer_id,
        "MD202610010016",
        [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "21", "quantity": "1"}],
        status="draft",
        order_date="2026-10-01",
        expected_version=version,
    )
    with pytest.raises(ValueError, match="版本冲突"):
        update_order_from_typed_rows(
            order_id,
            customer_id,
            "MD202610010016",
            [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "22", "quantity": "1"}],
            status="draft",
            order_date="2026-10-01",
            expected_version=version,
        )
    with get_db() as conn:
        row = conn.execute("SELECT version, total_amount_cents FROM orders WHERE id=?", (order_id,)).fetchone()
    assert tuple(row) == (version + 1, 2100)


def test_two_sales_cannot_both_consume_the_last_inventory():
    product_id = _enabled_product()
    customer_ids = [create_customer("并发销售客户一"), create_customer("并发销售客户二")]
    start = Barrier(2)
    results = []

    def submit(index):
        try:
            start.wait(timeout=5)
            order_id = create_order_from_typed_rows(
                customer_ids[index],
                f"MD20261001001{7 + index}",
                [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "20", "quantity": "10"}],
                status="saved",
                order_date="2026-10-01",
                request_key=f"concurrent-sale-{index}",
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
    assert any("库存不足" in result[1] for result in results if result[0] == "error")
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert conn.execute("SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()[0] == 0


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

    product_id = _enabled_product()
    customer_id = create_customer("销售生命周期客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010018",
        [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
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
    product_id = _enabled_product()
    customer_id = create_customer("销售生命周期页面客户")
    order_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010020",
        [{"product_id": product_id, "product_name": "销售成本商品", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
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