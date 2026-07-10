from __future__ import annotations

from datetime import date
from typing import Iterable

from erp.db import get_db
from erp.utils.audit import log_action
from erp.utils.money import line_subtotal_cents, yuan_to_cents


def create_customer(name: str, phone: str = "", address: str = "", opening_balance_cents: int = 0) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO customers(name, phone, address, opening_balance_cents) VALUES (?, ?, ?, ?)",
            (name, phone, address, opening_balance_cents),
        )
        customer_id = cur.lastrowid
    log_action("create_customer", "customer", customer_id, f"新增客户: {name}")
    return int(customer_id)


def create_product(name: str, spec: str, unit: str, default_price_cents: int, pinyin_initials: str = "") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO products(name, spec, unit, default_price_cents, pinyin_initials) VALUES (?, ?, ?, ?, ?)",
            (name, spec, unit, default_price_cents, pinyin_initials),
        )
        product_id = cur.lastrowid
    log_action("create_product", "product", product_id, f"新增商品: {name} {spec}")
    return int(product_id)


def set_customer_price(customer_id: int, product_id: int, price_cents: int) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO customer_prices(customer_id, product_id, price_cents)
            VALUES (?, ?, ?)
            ON CONFLICT(customer_id, product_id) DO UPDATE SET price_cents=excluded.price_cents, updated_at=CURRENT_TIMESTAMP
            """,
            (customer_id, product_id, price_cents),
        )
    log_action("edit_customer_price", "customer_price", f"{customer_id}:{product_id}", f"设置客户专属价格 {price_cents} 分")


def price_for_customer(customer_id: int, product_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT price_cents FROM customer_prices WHERE customer_id=? AND product_id=?",
            (customer_id, product_id),
        ).fetchone()
        if row:
            return int(row["price_cents"])
        product = conn.execute("SELECT default_price_cents FROM products WHERE id=?", (product_id,)).fetchone()
        if product is None:
            raise ValueError(f"商品不存在: {product_id}")
        return int(product["default_price_cents"])


def create_order(customer_id: int, order_no: str, items: Iterable[dict], status: str = "draft", order_date: str | None = None, notes: str = "") -> int:
    if status not in {"draft", "saved"}:
        raise ValueError("新建订单只能是 draft 或 saved")
    order_date = order_date or date.today().isoformat()
    prepared = []
    total = 0
    with get_db() as conn:
        for item in items:
            product = conn.execute("SELECT * FROM products WHERE id=?", (item["product_id"],)).fetchone()
            if product is None:
                raise ValueError(f"商品不存在: {item['product_id']}")
            unit_price_cents = int(item.get("unit_price_cents", price_for_customer(customer_id, int(item["product_id"]))))
            subtotal = line_subtotal_cents(str(item["quantity"]), unit_price_cents)
            total += subtotal
            prepared.append((item["product_id"], product["name"], product["spec"], product["unit"], str(item["quantity"]), unit_price_cents, subtotal))
        cur = conn.execute(
            "INSERT INTO orders(order_no, customer_id, order_date, total_amount_cents, status, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (order_no, customer_id, order_date, total, status, notes),
        )
        order_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO order_items(order_id, product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(order_id, *row) for row in prepared],
        )
    log_action("create_order", "order", order_id, f"新增订单 {order_no}，状态 {status}，金额 {total} 分")
    return order_id


def _upsert_product_and_customer_price(conn, customer_id: int, product_name: str, spec: str, unit: str, unit_price_cents: int) -> int:
    product = conn.execute("SELECT * FROM products WHERE name=? AND spec=? ORDER BY id LIMIT 1", (product_name, spec)).fetchone()
    if product is None:
        cur_product = conn.execute(
            "INSERT INTO products(name, spec, unit, default_price_cents, usage_count) VALUES (?, ?, ?, ?, 1)",
            (product_name, spec, unit, unit_price_cents),
        )
        return int(cur_product.lastrowid)
    product_id = int(product["id"])
    conn.execute(
        "UPDATE products SET unit=?, usage_count=usage_count+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (unit, product_id),
    )
    default_price_cents = int(product["default_price_cents"])
    if unit_price_cents != default_price_cents:
        conn.execute(
            """
            INSERT INTO customer_prices(customer_id, product_id, price_cents)
            VALUES (?, ?, ?)
            ON CONFLICT(customer_id, product_id) DO UPDATE SET price_cents=excluded.price_cents, updated_at=CURRENT_TIMESTAMP
            """,
            (customer_id, product_id, unit_price_cents),
        )
    return product_id


def create_order_from_typed_rows(customer_id: int, order_no: str, rows: Iterable[dict], status: str = "draft", order_date: str | None = None, notes: str = "", order_type: str = "sale") -> int:
    """Create an order from Excel-like typed rows.

    Each row may contain product_name, unit, unit_price_yuan, quantity and optional spec.
    Unknown products are saved into products as the operator's product dictionary.
    Existing products with the same name/spec are updated with the latest typed unit/price,
    while each order item keeps its own historical snapshot.
    """
    if status not in {"draft", "saved"}:
        raise ValueError("新建订单只能是 draft 或 saved")
    if order_type not in {"sale", "return"}:
        raise ValueError("单据类型不合法")
    sign = -1 if order_type == "return" else 1
    order_date = order_date or date.today().isoformat()
    prepared = []
    total = 0
    with get_db() as conn:
        for row in rows:
            product_name = str(row.get("product_name", "")).strip()
            if not product_name:
                continue
            quantity = str(row.get("quantity", "")).strip() or "0"
            unit = str(row.get("unit", "")).strip() or "个"
            spec = str(row.get("spec", "")).strip()
            unit_price_cents = yuan_to_cents(row.get("unit_price_yuan", "0"))
            product_id = _upsert_product_and_customer_price(conn, customer_id, product_name, spec, unit, unit_price_cents)
            subtotal = line_subtotal_cents(quantity, unit_price_cents)
            total += subtotal * sign
            prepared.append((product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal))
        cur = conn.execute(
            "INSERT INTO orders(order_no, customer_id, order_date, total_amount_cents, status, order_type, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_no, customer_id, order_date, total, status, order_type, notes),
        )
        order_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO order_items(order_id, product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(order_id, *item) for item in prepared],
        )
    log_action("create_order", "order", order_id, f"录入式新增订单 {order_no}，状态 {status}，金额 {total} 分")
    return order_id


def update_order_from_typed_rows(order_id: int, customer_id: int, order_no: str, rows: Iterable[dict], status: str, order_date: str, notes: str = "") -> None:
    """Replace an existing order's header/items using the same editable table rows."""
    if status not in {"draft", "saved", "printed", "void"}:
        raise ValueError("订单状态不合法")
    prepared = []
    total = 0
    with get_db() as conn:
        for row in rows:
            product_name = str(row.get("product_name", "")).strip()
            if not product_name:
                continue
            quantity = str(row.get("quantity", "")).strip() or "0"
            unit = str(row.get("unit", "")).strip() or "个"
            spec = str(row.get("spec", "")).strip()
            unit_price_cents = yuan_to_cents(row.get("unit_price_yuan", "0"))
            product_id = _upsert_product_and_customer_price(conn, customer_id, product_name, spec, unit, unit_price_cents)
            subtotal = line_subtotal_cents(quantity, unit_price_cents)
            total += subtotal
            prepared.append((order_id, product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal))
        conn.execute(
            "UPDATE orders SET order_no=?, customer_id=?, order_date=?, total_amount_cents=?, status=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (order_no, customer_id, order_date, total, status, notes, order_id),
        )
        conn.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
        conn.executemany(
            "INSERT INTO order_items(order_id, product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            prepared,
        )
    log_action("edit_order", "order", order_id, f"重编辑订单 {order_no}，状态 {status}，金额 {total} 分")


def mark_order_printed(order_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE orders SET status='printed', print_count=print_count+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('saved','printed')", (order_id,))
    log_action("print_order", "order", order_id, "用户确认打印/补打订单")


def void_order(order_id: int, reason: str) -> None:
    if not reason.strip():
        raise ValueError("作废原因不能为空")
    with get_db() as conn:
        conn.execute("UPDATE orders SET status='void', void_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('saved','printed')", (reason, order_id))
    log_action("void_order", "order", order_id, f"作废订单: {reason}")


def add_payment(customer_id: int, amount_cents: int, payment_date: str | None = None, method: str = "现金", notes: str = "") -> int:
    payment_date = payment_date or date.today().isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO payments(customer_id, amount_cents, payment_date, method, notes) VALUES (?, ?, ?, ?, ?)",
            (customer_id, amount_cents, payment_date, method, notes),
        )
        payment_id = int(cur.lastrowid)
    log_action("add_payment", "payment", payment_id, f"收款 {amount_cents} 分")
    return payment_id


def void_payment(payment_id: int, reason: str) -> None:
    if not reason.strip():
        raise ValueError("作废原因不能为空")
    with get_db() as conn:
        conn.execute("UPDATE payments SET status='void', void_reason=? WHERE id=?", (reason, payment_id))
    log_action("void_payment", "payment", payment_id, f"作废收款: {reason}")


def add_adjustment(customer_id: int, amount_cents: int, reason: str, adjustment_type: str = "other") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO adjustments(customer_id, amount_cents, adjustment_type, reason) VALUES (?, ?, ?, ?)",
            (customer_id, amount_cents, adjustment_type, reason),
        )
        adjustment_id = int(cur.lastrowid)
    log_action("add_adjustment", "adjustment", adjustment_id, f"账务调整 {amount_cents} 分: {reason}")
    return adjustment_id


def customer_balance_cents(customer_id: int) -> int:
    with get_db() as conn:
        customer = conn.execute("SELECT opening_balance_cents FROM customers WHERE id=?", (customer_id,)).fetchone()
        if customer is None:
            raise ValueError(f"客户不存在: {customer_id}")
        orders = conn.execute("SELECT COALESCE(SUM(total_amount_cents), 0) AS v FROM orders WHERE customer_id=? AND status IN ('saved','printed') AND deleted_at IS NULL", (customer_id,)).fetchone()["v"]
        payments = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) AS v FROM payments WHERE customer_id=? AND status='active'", (customer_id,)).fetchone()["v"]
        adjustments = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) AS v FROM adjustments WHERE customer_id=? AND status='active'", (customer_id,)).fetchone()["v"]
        return int(customer["opening_balance_cents"] or 0) + int(orders or 0) + int(adjustments or 0) - int(payments or 0)
