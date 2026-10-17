"""手机端测试用的造数工具（不放 conftest：只有手机端测试需要这批数据）。"""
from __future__ import annotations

from datetime import date

from erp.db import get_db, init_db
from erp.services import accounting, purchase_returns
from erp.services.purchases import create_purchase_order


def seed_all(today: str | None = None) -> dict:
    """造 1 个客户 + 4 类各 1 单（同一天）+ 一笔收款 + 一笔调整。

    金额与符号都是断言里的期望值来源：

    - 销售单 2 × 100 = 20000（正）
    - 退货单 1 × 30  = -3000（`orders.total_amount_cents` **库存的就是负数**）
    - 拿货单 3 × 40  = 12000（正）
    - 退拿货单 1 × 40 = 4000（正）
    - 收款 5000（余额减项）、调整 +2000（余额加项）
    - 期初 10000
    """
    init_db()
    day = today or date.today().isoformat()

    customer_id = accounting.create_customer("手机端测试客户", opening_balance_cents=10000)
    other_id = accounting.create_customer("手机端第二客户")
    product_id = accounting.create_product("手机端测试商品 MFZ/ABC4", "具")

    sale_id = accounting.create_order_from_typed_rows(
        customer_id, "MD202610170001",
        [{"product_id": product_id, "product_name": "手机端测试商品 MFZ/ABC4", "unit": "具",
          "quantity": "2", "unit_price_yuan": "100"}],
        status="saved", order_date=day,
    )
    return_id = accounting.create_order_from_typed_rows(
        customer_id, "MD202610170002",
        [{"product_id": product_id, "product_name": "手机端测试商品 MFZ/ABC4", "unit": "具",
          "quantity": "1", "unit_price_yuan": "30"}],
        status="saved", order_date=day, order_type="return",
    )
    purchase_id = create_purchase_order(
        "NH202610170001", day,
        [{"product_id": product_id, "quantity": "3", "unit_cost_yuan": "40"}],
        customer_id=customer_id, request_key="mobile-seed-purchase",
    )["order_id"]
    with get_db() as conn:
        purchase_item_id = conn.execute(
            "SELECT id FROM purchase_order_items WHERE purchase_order_id=?", (purchase_id,)
        ).fetchone()[0]
    purchase_return_id = purchase_returns.create(
        customer_id=customer_id, source_order_id=purchase_id, business_date=day,
        items=[{"source_item_id": purchase_item_id, "quantity": "1"}],
        request_key="mobile-seed-purchase-return",
    )["order_id"]
    accounting.add_payment(customer_id, 5000, payment_date=day, method="现金")
    accounting.add_adjustment(customer_id, 2000, "手机端测试调整")

    # 一个跨月的历史单，用来验「日期筛选不是只能看当天」
    accounting.create_order_from_typed_rows(
        other_id, "MD202609010001",
        [{"product_id": product_id, "product_name": "手机端测试商品 MFZ/ABC4", "unit": "具",
          "quantity": "1", "unit_price_yuan": "50"}],
        status="saved", order_date="2026-09-01",
    )

    return {
        "day": day,
        "customer_id": customer_id,
        "other_customer_id": other_id,
        "product_id": product_id,
        "sale_id": sale_id,
        "return_id": return_id,
        "purchase_id": purchase_id,
        "purchase_return_id": purchase_return_id,
    }


def pair_client(client, *, label: str = "测试手机") -> str:
    """给测试客户端配一台设备（返回 cookie 值，方便断言撤销后失效）。"""
    from erp.services import mobile_pairing

    code, error = mobile_pairing.generate_pair_code()
    assert not error, error
    cookie_value, error = mobile_pairing.verify_pair_code(code, label=label)
    assert not error, error
    client.set_cookie(mobile_pairing.COOKIE_NAME, cookie_value)
    return cookie_value
