from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

from erp.db import get_db
from erp.utils.audit import log_action
from erp.utils.money import line_subtotal_cents, yuan_to_cents


def _validate_quantity(value) -> str:
    try:
        quantity = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("数量必须是有效数字")
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("数量必须大于 0")
    return str(value).strip()


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
            quantity = _validate_quantity(item["quantity"])
            subtotal = line_subtotal_cents(quantity, unit_price_cents)
            total += subtotal
            prepared.append((item["product_id"], product["name"], product["spec"], product["unit"], quantity, unit_price_cents, subtotal))
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
            quantity = _validate_quantity(row.get("quantity", ""))
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


def update_order_from_typed_rows(
    order_id: int,
    customer_id: int,
    order_no: str,
    rows: Iterable[dict],
    status: str,
    order_date: str,
    notes: str = "",
    order_type: str | None = None,
) -> None:
    """Replace an existing order's header/items using the same editable table rows."""
    if status not in {"draft", "saved", "printed"}:
        raise ValueError("订单状态不合法")
    prepared = []
    total = 0
    with get_db() as conn:
        existing = conn.execute("SELECT order_type FROM orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
        if existing is None:
            raise ValueError("订单不存在")
        order_type = order_type or existing["order_type"]
        if order_type not in {"sale", "return"}:
            raise ValueError("单据类型不合法")
        sign = -1 if order_type == "return" else 1
        for row in rows:
            product_name = str(row.get("product_name", "")).strip()
            if not product_name:
                continue
            quantity = _validate_quantity(row.get("quantity", ""))
            unit = str(row.get("unit", "")).strip() or "个"
            spec = str(row.get("spec", "")).strip()
            unit_price_cents = yuan_to_cents(row.get("unit_price_yuan", "0"))
            product_id = _upsert_product_and_customer_price(conn, customer_id, product_name, spec, unit, unit_price_cents)
            subtotal = line_subtotal_cents(quantity, unit_price_cents)
            total += subtotal * sign
            prepared.append((order_id, product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal))
        conn.execute(
            "UPDATE orders SET order_no=?, customer_id=?, order_date=?, total_amount_cents=?, status=?, order_type=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (order_no, customer_id, order_date, total, status, order_type, notes, order_id),
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
    reason = reason.strip()
    if not reason:
        raise ValueError("作废原因不能为空")
    with get_db() as conn:
        order = conn.execute("SELECT status FROM orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
        if order is None:
            raise ValueError("订单不存在")
        if order["status"] not in {"saved", "printed"}:
            raise ValueError("只有已保存或已打印订单可以作废")
        conn.execute("UPDATE orders SET status='void', void_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (reason, order_id))
        log_action("void_order", "order", order_id, f"作废订单: {reason}", conn=conn)


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
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("作废原因不能为空")
    with get_db() as conn:
        payment = conn.execute(
            "SELECT status FROM payments WHERE id=?",
            (payment_id,),
        ).fetchone()
        if payment is None:
            raise ValueError("收款记录不存在")
        if payment["status"] != "active":
            raise ValueError("收款记录已作废")
        conn.execute(
            "UPDATE payments SET status='void', void_reason=? WHERE id=?",
            (reason, payment_id),
        )
        log_action("void_payment", "payment", payment_id, f"作废收款: {reason}", conn=conn)


def add_adjustment(customer_id: int, amount_cents: int, reason: str, adjustment_type: str = "other") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO adjustments(customer_id, amount_cents, adjustment_type, reason) VALUES (?, ?, ?, ?)",
            (customer_id, amount_cents, adjustment_type, reason),
        )
        adjustment_id = int(cur.lastrowid)
    log_action("add_adjustment", "adjustment", adjustment_id, f"账务调整 {amount_cents} 分: {reason}")
    return adjustment_id


def void_adjustment(adjustment_id: int, reason: str) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("作废原因不能为空")
    with get_db() as conn:
        adjustment = conn.execute(
            "SELECT status FROM adjustments WHERE id=?",
            (adjustment_id,),
        ).fetchone()
        if adjustment is None:
            raise ValueError("账务调整记录不存在")
        if adjustment["status"] != "active":
            raise ValueError("账务调整已作废")
        conn.execute(
            "UPDATE adjustments SET status='void', void_reason=?, voided_at=CURRENT_TIMESTAMP WHERE id=?",
            (reason, adjustment_id),
        )
        log_action("void_adjustment", "adjustment", adjustment_id, f"作废账务调整: {reason}", conn=conn)


def customer_balance_cents(customer_id: int) -> int:
    with get_db() as conn:
        customer = conn.execute("SELECT opening_balance_cents FROM customers WHERE id=?", (customer_id,)).fetchone()
        if customer is None:
            raise ValueError(f"客户不存在: {customer_id}")
        orders = conn.execute("SELECT COALESCE(SUM(total_amount_cents), 0) AS v FROM orders WHERE customer_id=? AND status IN ('saved','printed') AND deleted_at IS NULL", (customer_id,)).fetchone()["v"]
        payments = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) AS v FROM payments WHERE customer_id=? AND status='active'", (customer_id,)).fetchone()["v"]
        adjustments = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) AS v FROM adjustments WHERE customer_id=? AND status='active'", (customer_id,)).fetchone()["v"]
        return int(customer["opening_balance_cents"] or 0) + int(orders or 0) + int(adjustments or 0) - int(payments or 0)


def get_customer_account_ledger(customer_id: int, start_date: str = "", end_date: str = "") -> dict:
    """Return one customer's current balance and date-filtered, auditable ledger."""
    start_date = (start_date or "").strip()
    end_date = (end_date or "").strip()
    with get_db() as conn:
        customer = conn.execute(
            "SELECT * FROM customers WHERE id=? AND deleted_at IS NULL",
            (customer_id,),
        ).fetchone()
        if customer is None:
            raise ValueError(f"客户不存在: {customer_id}")

        rows = conn.execute(
            """
            WITH ledger AS (
                SELECT
                    'order' AS source,
                    o.id AS source_id,
                    o.order_type AS type,
                    o.order_date AS date,
                    o.created_at AS sort_at,
                    o.order_no AS reference,
                    COALESCE((
                        SELECT group_concat(
                            trim(oi.product_name || CASE WHEN COALESCE(oi.spec, '')='' THEN '' ELSE ' ' || oi.spec END),
                            '、'
                        )
                        FROM order_items oi WHERE oi.order_id=o.id
                    ), '订单') AS description,
                    o.total_amount_cents AS amount_cents,
                    CASE WHEN o.status='void' THEN 0 ELSE o.total_amount_cents END AS balance_delta_cents,
                    CASE WHEN o.status='void' THEN 'void' ELSE 'active' END AS status,
                    COALESCE(o.void_reason, '') AS void_reason
                FROM orders o
                WHERE o.customer_id=? AND o.deleted_at IS NULL
                  AND o.status IN ('saved','printed','void')

                UNION ALL

                SELECT
                    'payment' AS source,
                    p.id AS source_id,
                    'payment' AS type,
                    p.payment_date AS date,
                    p.created_at AS sort_at,
                    p.method AS reference,
                    CASE WHEN COALESCE(p.notes, '')='' THEN '客户收款' ELSE p.notes END AS description,
                    p.amount_cents AS amount_cents,
                    CASE WHEN p.status='void' THEN 0 ELSE -p.amount_cents END AS balance_delta_cents,
                    p.status AS status,
                    COALESCE(p.void_reason, '') AS void_reason
                FROM payments p
                WHERE p.customer_id=?

                UNION ALL

                SELECT
                    'adjustment' AS source,
                    a.id AS source_id,
                    'adjustment' AS type,
                    date(a.created_at, 'localtime') AS date,
                    datetime(a.created_at, 'localtime') AS sort_at,
                    '账务调整' AS reference,
                    a.reason AS description,
                    a.amount_cents AS amount_cents,
                    CASE WHEN a.status='void' THEN 0 ELSE a.amount_cents END AS balance_delta_cents,
                    a.status AS status,
                    COALESCE(a.void_reason, '') AS void_reason
                FROM adjustments a
                WHERE a.customer_id=?
            )
            SELECT source, source_id, type, date, sort_at, reference, description,
                   amount_cents, balance_delta_cents, status, void_reason
            FROM ledger
            ORDER BY date DESC, sort_at DESC, source ASC, source_id DESC
            """,
            (customer_id, customer_id, customer_id),
        ).fetchall()

    all_entries = [dict(row) for row in rows]
    active_entries = [entry for entry in all_entries if entry["status"] == "active"]
    visible_entries = [
        entry for entry in all_entries
        if (not start_date or entry["date"] >= start_date)
        and (not end_date or entry["date"] <= end_date)
    ]
    active_period_entries = [entry for entry in visible_entries if entry["status"] == "active"]

    document_net_cents = sum(
        entry["balance_delta_cents"] for entry in active_entries
        if entry["type"] in {"sale", "return"}
    )
    adjustment_cents = sum(
        entry["balance_delta_cents"] for entry in active_entries
        if entry["type"] == "adjustment"
    )
    payment_cents = sum(
        entry["amount_cents"] for entry in active_entries
        if entry["type"] == "payment"
    )
    opening_balance_cents = int(customer["opening_balance_cents"] or 0)
    current_balance_cents = opening_balance_cents + sum(
        entry["balance_delta_cents"] for entry in active_entries
    )

    period = {
        "start_date": start_date,
        "end_date": end_date,
        "sales_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "sale"),
        "returns_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "return"),
        "payments_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "payment"),
        "adjustments_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "adjustment"),
    }
    period["net_change_cents"] = sum(
        period[key] for key in ("sales_cents", "returns_cents", "payments_cents", "adjustments_cents")
    )

    return {
        "customer": dict(customer),
        "opening_balance_cents": opening_balance_cents,
        "document_net_cents": document_net_cents,
        "adjustment_cents": adjustment_cents,
        "payment_cents": payment_cents,
        "current_balance_cents": current_balance_cents,
        "period": period,
        "entries": visible_entries,
    }
