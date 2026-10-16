import pytest

from erp.db import init_db
from erp.services.accounting import (
    add_adjustment,
    add_payment,
    create_customer,
    create_order,
    create_product,
    customer_balance_cents,
    mark_order_printed,
    price_for_customer,
    set_customer_price,
    void_order,
    void_payment,
)


def test_customer_ledger_uses_opening_orders_adjustments_and_active_payments():
    init_db()
    customer_id = create_customer("华润消防工程", opening_balance_cents=10000)
    product_id = create_product("黄铜截止阀DN20", "个")
    # 取价口径（2026-10-15）：行内必须给单价，或该客户已有专属价；这里直给行内价。
    order_id = create_order(customer_id, "T-001", [{"product_id": product_id, "quantity": "2", "unit_price_cents": 3500}], status="saved")
    assert customer_balance_cents(customer_id) == 17000

    payment_id = add_payment(customer_id, 5000)
    assert customer_balance_cents(customer_id) == 12000

    add_adjustment(customer_id, -200, "抹零", "discount")
    assert customer_balance_cents(customer_id) == 11800

    void_payment(payment_id, "录错")
    assert customer_balance_cents(customer_id) == 16800

    void_order(order_id, "开错客户")
    assert customer_balance_cents(customer_id) == 9800


def test_draft_order_does_not_affect_balance_but_printed_order_does():
    init_db()
    customer_id = create_customer("中建消防")
    product_id = create_product("闸阀DN50", "只")
    create_order(customer_id, "T-002", [{"product_id": product_id, "quantity": "1", "unit_price_cents": 6800}], status="draft")
    assert customer_balance_cents(customer_id) == 0
    order_id = create_order(customer_id, "T-003", [{"product_id": product_id, "quantity": "1.5", "unit_price_cents": 6800}], status="saved")
    mark_order_printed(order_id)
    assert customer_balance_cents(customer_id) == 10200


def test_customer_specific_price_is_the_only_price_source():
    """取价口径（2026-10-15）：售价唯一来源 = 客户专属价，档案已无默认价可回落。"""
    init_db()
    customer_id = create_customer("专价客户")
    product_id = create_product("沟槽卡箍DN100", "个")
    # 还没有专属价 → 「无价」（None），不是 0，也不是档案价。
    assert price_for_customer(customer_id, product_id) is None
    set_customer_price(customer_id, product_id, 1200)
    assert price_for_customer(customer_id, product_id) == 1200
    set_customer_price(customer_id, product_id, 1000)
    assert price_for_customer(customer_id, product_id) == 1000
    create_order(customer_id, "T-004", [{"product_id": product_id, "quantity": "3"}], status="saved")
    # 事后改专属价不影响已保存单据的历史金额。
    set_customer_price(customer_id, product_id, 900)
    assert customer_balance_cents(customer_id) == 3000


def test_create_order_without_any_price_is_rejected_explicitly():
    """行内没给单价、该客户又没有专属价 → 明确报错，不静默落 0（§八 定案）。"""
    init_db()
    customer_id = create_customer("无价客户")
    product_id = create_product("无价商品", "个")
    with pytest.raises(ValueError, match="暂无专属价"):
        create_order(customer_id, "T-005", [{"product_id": product_id, "quantity": "1"}], status="saved")
    assert customer_balance_cents(customer_id) == 0
