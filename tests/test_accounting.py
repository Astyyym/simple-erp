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
    product_id = create_product("黄铜截止阀", "DN20", "个", 3500)
    order_id = create_order(customer_id, "T-001", [{"product_id": product_id, "quantity": "2"}], status="saved")
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
    product_id = create_product("闸阀", "DN50", "只", 6800)
    create_order(customer_id, "T-002", [{"product_id": product_id, "quantity": "1"}], status="draft")
    assert customer_balance_cents(customer_id) == 0
    order_id = create_order(customer_id, "T-003", [{"product_id": product_id, "quantity": "1.5"}], status="saved")
    mark_order_printed(order_id)
    assert customer_balance_cents(customer_id) == 10200


def test_customer_specific_price_overrides_default_without_changing_history():
    init_db()
    customer_id = create_customer("专价客户")
    product_id = create_product("沟槽卡箍", "DN100", "个", 1200)
    assert price_for_customer(customer_id, product_id) == 1200
    set_customer_price(customer_id, product_id, 1000)
    assert price_for_customer(customer_id, product_id) == 1000
    create_order(customer_id, "T-004", [{"product_id": product_id, "quantity": "3"}], status="saved")
    set_customer_price(customer_id, product_id, 900)
    assert customer_balance_cents(customer_id) == 3000
