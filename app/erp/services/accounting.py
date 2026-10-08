from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import sqlite3
from typing import Iterable

from erp.db import get_db
from erp.utils.audit import log_action
from erp.utils.money import line_subtotal_cents, yuan_to_cents
from erp.utils.pinyin import pinyin_initials as build_pinyin_initials
from erp.services.inventory import post_return_order, post_sale_order, post_typed_return_order


def _validate_quantity(value) -> str:
    try:
        quantity = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("数量必须是有效数字")
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("数量必须大于 0")
    return str(value).strip()


def _quantity_3dp(value) -> int:
    """Parse a business quantity without going through binary float."""
    try:
        quantity = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("数量必须是有效数字") from None
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("数量必须大于 0")
    scaled = quantity * Decimal("1000")
    if scaled != scaled.to_integral_value():
        raise ValueError("数量最多保留3位小数")
    return int(scaled)


def _proportional_amount(total: int, numerator: int, denominator: int) -> int:
    return int(
        (Decimal(total) * Decimal(numerator) / Decimal(denominator)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _payload_hash(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _submission_key(request_key: str | None, order_no: str) -> str:
    return (request_key or "").strip() or f"order:{order_no}"


def _existing_order_submission(conn, request_key: str, payload_hash: str) -> int | None:
    existing = conn.execute(
        "SELECT id, payload_hash FROM orders WHERE request_key=?",
        (request_key,),
    ).fetchone()
    if existing is None:
        return None
    if existing["payload_hash"] != payload_hash:
        raise ValueError("幂等键内容不一致")
    return int(existing["id"])


def _typed_order_payload(
    customer_id: int,
    order_no: str,
    rows: list[dict],
    status: str,
    order_date: str,
    notes: str,
    order_type: str,
) -> dict:
    canonical_rows = []
    for row in rows:
        name, spec = normalize_product_identity(row.get("product_name", ""), row.get("spec", ""))
        quantity_3dp = _quantity_3dp(row.get("quantity", ""))
        unit = str(row.get("unit", "")).strip() or "个"
        unit_price_cents = yuan_to_cents(row.get("unit_price_yuan", "0"))
        product_id = int(row["product_id"]) if row.get("product_id") else None
        canonical_rows.append({
            "product_id": product_id,
            "product_name": "" if product_id is not None else name,
            "spec": "" if product_id is not None else spec,
            "unit": unit,
            "quantity_3dp": quantity_3dp,
            "unit_price_cents": unit_price_cents,
        })
    return {
        "customer_id": int(customer_id),
        "order_date": order_date,
        "status": status,
        "order_type": order_type,
        "notes": notes,
        "rows": canonical_rows,
    }


def _average_cost_micro_for_accounting(quantity_3dp: int, cost_total_micro: int) -> int | None:
    if quantity_3dp == 0:
        if cost_total_micro != 0:
            raise ValueError("库存数量为零时库存成本必须为零")
        return None
    return int(
        (Decimal(cost_total_micro) / Decimal(quantity_3dp) * Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def create_customer(name: str, phone: str = "", address: str = "", opening_balance_cents: int = 0) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO customers(name, phone, address, opening_balance_cents) VALUES (?, ?, ?, ?)",
            (name, phone, address, opening_balance_cents),
        )
        customer_id = cur.lastrowid
    log_action("create_customer", "customer", customer_id, f"新增客户: {name}")
    return int(customer_id)


def normalize_product_identity(name: str, spec: str = "") -> tuple[str, str]:
    normalized_name = str(name or "").strip()
    normalized_spec = str(spec or "").strip()
    if not normalized_name:
        raise ValueError("商品名称不能为空")
    return normalized_name, normalized_spec


def _validate_safety_stock(value) -> int:
    try:
        parsed = Decimal(str(value if value is not None and str(value).strip() else "0").strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("安全库存必须是有效数字") from None
    if not parsed.is_finite():
        raise ValueError("安全库存必须是有限数字")
    if parsed < 0:
        raise ValueError("安全库存不能为负数")
    scaled = parsed * Decimal("1000")
    if scaled != scaled.to_integral_value():
        raise ValueError("安全库存最多保留3位小数")
    return int(scaled)


def create_product(name: str, spec: str, unit: str, default_price_cents: int, pinyin_initials: str = "", *, safety_stock=None) -> int:
    name, spec = normalize_product_identity(name, spec)
    safety_stock_3dp = _validate_safety_stock(safety_stock)
    with get_db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO products(name, spec, unit, default_price_cents, pinyin_initials, safety_stock_3dp) VALUES (?, ?, ?, ?, ?, ?)",
                (name, spec, unit, default_price_cents, pinyin_initials, safety_stock_3dp),
            )
        except sqlite3.IntegrityError as exc:
            if "idx_products_business_identity" in str(exc) or "UNIQUE constraint failed: index" in str(exc):
                raise ValueError("商品名称和型号已存在") from exc
            raise
        product_id = cur.lastrowid
    log_action("create_product", "product", product_id, f"新增商品: {name} {spec}")
    return int(product_id)


def update_product_record(product_id: int, name: str, spec: str, unit: str, default_price_cents: int, *, safety_stock=None) -> None:
    name, spec = normalize_product_identity(name, spec)
    safety_stock_3dp = _validate_safety_stock(safety_stock)
    with get_db() as conn:
        inventory_state = conn.execute(
            "SELECT enabled FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        current = conn.execute("SELECT unit FROM products WHERE id=? AND deleted_at IS NULL", (product_id,)).fetchone()
        if current is None:
            raise ValueError("商品不存在")
        if inventory_state is not None and inventory_state["enabled"] and unit.strip() != current["unit"]:
            raise ValueError("单位已锁定")
        try:
            cursor = conn.execute(
                """
                UPDATE products
                SET name=?, spec=?, unit=?, default_price_cents=?, safety_stock_3dp=?, pinyin_initials=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND deleted_at IS NULL
                """,
                (name, spec, unit, default_price_cents, safety_stock_3dp, build_pinyin_initials(name), product_id),
            )
        except sqlite3.IntegrityError as exc:
            if "idx_products_business_identity" in str(exc) or "UNIQUE constraint failed: index" in str(exc):
                raise ValueError("商品名称和型号已存在") from exc
            raise
        if cursor.rowcount == 0:
            raise ValueError("商品不存在")


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
    product_name, spec = normalize_product_identity(product_name, spec)
    product = conn.execute("SELECT * FROM products WHERE TRIM(name)=? AND TRIM(COALESCE(spec, ''))=? ORDER BY id LIMIT 1", (product_name, spec)).fetchone()
    if product is None:
        cur_product = conn.execute(
            "INSERT INTO products(name, spec, unit, default_price_cents, pinyin_initials, usage_count) VALUES (?, ?, ?, ?, ?, 1)",
            (product_name, spec, unit, unit_price_cents, build_pinyin_initials(product_name)),
        )
        return int(cur_product.lastrowid)
    if product["deleted_at"] is not None:
        raise ValueError("商品已在回收站，不能用于新开单")
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


def _select_existing_product(conn, customer_id: int, product_id, unit_price_cents: int) -> tuple[int, str, str, str]:
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        raise ValueError("必须选择具体商品") from None
    product = conn.execute(
        "SELECT id, name, spec, unit, default_price_cents, deleted_at FROM products WHERE id=?",
        (product_id,),
    ).fetchone()
    if product is None or product["deleted_at"] is not None:
        raise ValueError("商品不存在或已删除")
    conn.execute("UPDATE products SET usage_count=usage_count+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (product_id,))
    if unit_price_cents != int(product["default_price_cents"]):
        conn.execute(
            """
            INSERT INTO customer_prices(customer_id, product_id, price_cents)
            VALUES (?, ?, ?)
            ON CONFLICT(customer_id, product_id) DO UPDATE SET price_cents=excluded.price_cents, updated_at=CURRENT_TIMESTAMP
            """,
            (customer_id, product_id, unit_price_cents),
        )
    return int(product["id"]), product["name"], product["spec"] or "", product["unit"]


def create_order_from_typed_rows(
    customer_id: int,
    order_no: str,
    rows: Iterable[dict],
    status: str = "draft",
    order_date: str | None = None,
    notes: str = "",
    order_type: str = "sale",
    request_key: str | None = None,
) -> int:
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
    rows = list(rows)
    request_key = _submission_key(request_key, order_no)
    payload = _typed_order_payload(customer_id, order_no, rows, status, order_date, notes or "", order_type)
    payload_hash = _payload_hash(payload)
    prepared = []
    total = 0
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_id = _existing_order_submission(conn, request_key, payload_hash)
        if existing_id is not None:
            return existing_id
        if status == "saved" and order_type == "sale":
            pass
        for row in rows:
            raw_product_name = str(row.get("product_name", ""))
            if not raw_product_name.strip():
                continue
            product_name, spec = normalize_product_identity(raw_product_name, row.get("spec", ""))
            quantity = _validate_quantity(row.get("quantity", ""))
            unit = str(row.get("unit", "")).strip() or "个"
            unit_price_cents = yuan_to_cents(row.get("unit_price_yuan", "0"))
            if row.get("product_id"):
                product_id, product_name, spec, unit = _select_existing_product(conn, customer_id, row["product_id"], unit_price_cents)
            else:
                product_id = _upsert_product_and_customer_price(conn, customer_id, product_name, spec, unit, unit_price_cents)
            subtotal = line_subtotal_cents(quantity, unit_price_cents)
            total += subtotal * sign
            prepared.append((product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal))
        cur = conn.execute(
            """
            INSERT INTO orders(
                order_no, customer_id, order_date, total_amount_cents, status, order_type,
                notes, request_key, payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_no, customer_id, order_date, total, status, order_type, notes, request_key, payload_hash),
        )
        order_id = int(cur.lastrowid)
        if status == "saved" and order_type == "sale":
            snapshots = post_sale_order(
                conn,
                order_id,
                [{"product_id": item[0], "quantity": item[4]} for item in prepared],
                order_date,
            )
        elif status == "saved" and order_type == "return":
            # Decoupled manual returns post inventory back at the current moving-average cost.
            snapshots = post_typed_return_order(
                conn,
                order_id,
                [{"product_id": item[0], "quantity": item[4]} for item in prepared],
                order_date,
            )
        else:
            snapshots = [{"unit_cost_micro": None, "cost_total_micro": None} for _ in prepared]
        conn.executemany(
            """
            INSERT INTO order_items(
                order_id, product_id, product_name, spec, unit, quantity,
                unit_price_cents, subtotal_cents, unit_cost_micro, cost_total_micro
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(order_id, *item, snapshot["unit_cost_micro"], snapshot["cost_total_micro"])
             for item, snapshot in zip(prepared, snapshots)],
        )
    log_action("create_order", "order", order_id, f"录入式新增订单 {order_no}，状态 {status}，金额 {total} 分")
    return order_id


def _prepare_return_items(conn, source_order_id: int, rows: list[dict], customer_id: int, order_date: str) -> list[dict]:
    source = conn.execute(
        """
        SELECT * FROM orders
        WHERE id=? AND order_type='sale' AND customer_id=? AND deleted_at IS NULL
          AND status IN ('saved', 'printed')
        """,
        (source_order_id, customer_id),
    ).fetchone()
    if source is None:
        other_source = conn.execute("SELECT customer_id, order_type, status FROM orders WHERE id=? AND deleted_at IS NULL", (source_order_id,)).fetchone()
        if other_source is not None and other_source["customer_id"] != customer_id:
            raise ValueError("退货来源必须是同一客户的销售单")
        raise ValueError("退货来源必须是有效销售单")
    if order_date < source["order_date"]:
        raise ValueError("退货日期不能早于原销售日期")
    if order_date > date.today().isoformat():
        raise ValueError("退货日期不能晚于今天")
    if not rows:
        raise ValueError("退货单至少需要一行原销售商品")

    source_ids = []
    for raw in rows:
        try:
            source_ids.append(int(raw.get("source_item_id")))
        except (TypeError, ValueError):
            raise ValueError("退货必须选择原销售明细") from None
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("同一退货单不能重复选择原销售明细")
    placeholders = ",".join("?" for _ in source_ids)
    source_items = conn.execute(
        f"SELECT * FROM order_items WHERE order_id=? AND id IN ({placeholders}) ORDER BY id",
        [source_order_id, *source_ids],
    ).fetchall()
    by_id = {int(item["id"]): item for item in source_items}
    if len(by_id) != len(source_ids):
        raise ValueError("原销售明细不存在")

    prepared = []
    for raw in rows:
        source_item_id = int(raw["source_item_id"])
        source_item = by_id[source_item_id]
        if source_item["unit_cost_micro"] is None or source_item["cost_total_micro"] is None:
            raise ValueError("原销售明细缺少可靠成本，当前退货路径不支持")
        original_quantity = _quantity_3dp(source_item["quantity"])
        requested_quantity = _quantity_3dp(raw.get("quantity"))
        previous_rows = conn.execute(
            """
            SELECT oi.quantity, oi.subtotal_cents, oi.cost_total_micro
            FROM order_items oi
            JOIN orders r ON r.id=oi.order_id
            WHERE r.source_order_id=? AND r.order_type='return'
              AND r.status IN ('saved', 'printed') AND r.deleted_at IS NULL
              AND oi.source_item_id=?
            """,
            (source_order_id, source_item_id),
        ).fetchall()
        previous_quantity = sum(_quantity_3dp(row["quantity"]) for row in previous_rows)
        available = original_quantity - previous_quantity
        if requested_quantity > available:
            raise ValueError("退货数量超过可退数量")
        cumulative_quantity = previous_quantity + requested_quantity
        original_amount = int(source_item["subtotal_cents"])
        original_cost = int(source_item["cost_total_micro"])
        previous_amount = sum(int(row["subtotal_cents"]) for row in previous_rows)
        previous_cost = sum(int(row["cost_total_micro"] or 0) for row in previous_rows)
        if cumulative_quantity == original_quantity:
            cumulative_amount = original_amount
            cumulative_cost = original_cost
        else:
            cumulative_amount = _proportional_amount(original_amount, cumulative_quantity, original_quantity)
            cumulative_cost = _proportional_amount(original_cost, cumulative_quantity, original_quantity)
        prepared.append({
            "source_item_id": source_item_id,
            "product_id": int(source_item["product_id"]),
            "product_name": source_item["product_name"],
            "spec": source_item["spec"] or "",
            "unit": source_item["unit"],
            "quantity": str(raw.get("quantity")).strip(),
            "quantity_3dp": requested_quantity,
            "unit_price_cents": int(source_item["unit_price_cents"]),
            "subtotal_cents": cumulative_amount - previous_amount,
            "unit_cost_micro": int(source_item["unit_cost_micro"]),
            "cost_total_micro": cumulative_cost - previous_cost,
        })
    return prepared


def create_return_order_from_source(
    customer_id: int,
    order_no: str,
    source_order_id: int,
    rows: list[dict],
    *,
    status: str = "saved",
    order_date: str | None = None,
    notes: str = "",
    request_key: str | None = None,
) -> int:
    """Create a return tied to one sale; saved returns restore inventory atomically."""
    if status not in {"draft", "saved"}:
        raise ValueError("新建退货单只能是草稿或正式保存")
    order_date = (order_date or date.today().isoformat()).strip()
    try:
        date.fromisoformat(order_date)
    except ValueError:
        raise ValueError("退货日期格式无效") from None
    request_key = _submission_key(request_key, order_no)
    payload = {
        "customer_id": int(customer_id),
        "source_order_id": int(source_order_id),
        "status": status,
        "order_date": order_date,
        "notes": notes or "",
        "rows": [
            {"source_item_id": int(row["source_item_id"]), "quantity_3dp": _quantity_3dp(row.get("quantity"))}
            for row in rows
        ],
    }
    payload_hash = _payload_hash(payload)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_id = _existing_order_submission(conn, request_key, payload_hash)
        if existing_id is not None:
            return existing_id
        prepared = _prepare_return_items(conn, int(source_order_id), rows, int(customer_id), order_date)
        total_amount = sum(item["subtotal_cents"] for item in prepared)
        cur = conn.execute(
            """
            INSERT INTO orders(
                order_no, customer_id, order_date, total_amount_cents, status, order_type, notes,
                source_order_id, request_key, payload_hash
            ) VALUES (?, ?, ?, ?, ?, 'return', ?, ?, ?, ?)
            """,
            (order_no, customer_id, order_date, -total_amount, status, notes, source_order_id, request_key, payload_hash),
        )
        order_id = int(cur.lastrowid)
        conn.executemany(
            """
            INSERT INTO order_items(
                order_id, product_id, product_name, spec, unit, quantity,
                unit_price_cents, subtotal_cents, unit_cost_micro, cost_total_micro, source_item_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    order_id, item["product_id"], item["product_name"], item["spec"], item["unit"], item["quantity"],
                    item["unit_price_cents"], item["subtotal_cents"], item["unit_cost_micro"], item["cost_total_micro"], item["source_item_id"],
                )
                for item in prepared
            ],
        )
        if status == "saved":
            post_return_order(conn, order_id, prepared, order_date)
        log_action("create_return_order", "order", order_id, f"新增退货单 {order_no}", conn=conn)
        return order_id


def update_return_draft_from_source(
    order_id: int,
    rows: list[dict],
    *,
    status: str,
    order_date: str,
    notes: str = "",
    expected_version: int | None = None,
) -> None:
    """Edit a source-linked return draft, optionally posting it once."""
    if status not in {"draft", "saved"}:
        raise ValueError("退货单只能保存为草稿或正式保存")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE id=? AND order_type='return' AND deleted_at IS NULL",
            (order_id,),
        ).fetchone()
        if order is None:
            raise ValueError("退货单不存在")
        if order["status"] != "draft":
            raise ValueError("正式退货单经济字段已锁定")
        if expected_version is not None and int(order["version"]) != int(expected_version):
            raise ValueError("版本冲突，请刷新后重试")
        prepared = _prepare_return_items(conn, int(order["source_order_id"]), rows, int(order["customer_id"]), order_date)
        total_amount = sum(item["subtotal_cents"] for item in prepared)
        conn.execute(
            "UPDATE orders SET order_date=?, total_amount_cents=?, status=?, notes=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (order_date, -total_amount, status, notes, order_id),
        )
        conn.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
        conn.executemany(
            """
            INSERT INTO order_items(
                order_id, product_id, product_name, spec, unit, quantity,
                unit_price_cents, subtotal_cents, unit_cost_micro, cost_total_micro, source_item_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    order_id, item["product_id"], item["product_name"], item["spec"], item["unit"], item["quantity"],
                    item["unit_price_cents"], item["subtotal_cents"], item["unit_cost_micro"], item["cost_total_micro"], item["source_item_id"],
                )
                for item in prepared
            ],
        )
        if status == "saved":
            post_return_order(conn, order_id, prepared, order_date)
        log_action("edit_return_order", "order", order_id, f"编辑退货单 {order['order_no']}", conn=conn)


def update_order_from_typed_rows(
    order_id: int,
    customer_id: int,
    order_no: str,
    rows: Iterable[dict],
    status: str,
    order_date: str,
    notes: str = "",
    order_type: str | None = None,
    expected_version: int | None = None,
) -> None:
    """Replace an existing order's header/items using the same editable table rows."""
    if status not in {"draft", "saved", "printed"}:
        raise ValueError("订单状态不合法")
    prepared = []
    total = 0
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT order_type, status, version FROM orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
        if existing is None:
            raise ValueError("订单不存在")
        if expected_version is not None and int(existing["version"]) != int(expected_version):
            raise ValueError("版本冲突，请刷新后重试")
        order_type = order_type or existing["order_type"]
        if order_type not in {"sale", "return"}:
            raise ValueError("单据类型不合法")
        if conn.execute(
            """
            SELECT 1 FROM inventory_postings
            WHERE source_type IN ('sale', 'return') AND (source_id=? OR source_id LIKE ?)
            LIMIT 1
            """,
            (str(order_id), f"{order_id}:%"),
        ).fetchone() is not None:
            raise ValueError("库存已过账，经济字段不能重编辑")
        finalize_sale = existing["status"] == "draft" and status == "saved" and order_type == "sale"
        sign = -1 if order_type == "return" else 1
        for row in rows:
            raw_product_name = str(row.get("product_name", ""))
            if not raw_product_name.strip():
                continue
            product_name, spec = normalize_product_identity(raw_product_name, row.get("spec", ""))
            quantity = _validate_quantity(row.get("quantity", ""))
            unit = str(row.get("unit", "")).strip() or "个"
            unit_price_cents = yuan_to_cents(row.get("unit_price_yuan", "0"))
            if row.get("product_id"):
                product_id, product_name, spec, unit = _select_existing_product(conn, customer_id, row["product_id"], unit_price_cents)
            else:
                product_id = _upsert_product_and_customer_price(conn, customer_id, product_name, spec, unit, unit_price_cents)
            subtotal = line_subtotal_cents(quantity, unit_price_cents)
            total += subtotal * sign
            prepared.append((order_id, product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal))
        conn.execute(
            "UPDATE orders SET order_no=?, customer_id=?, order_date=?, total_amount_cents=?, status=?, order_type=?, notes=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (order_no, customer_id, order_date, total, status, order_type, notes, order_id),
        )
        conn.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
        conn.executemany(
            "INSERT INTO order_items(order_id, product_id, product_name, spec, unit, quantity, unit_price_cents, subtotal_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            prepared,
        )
        if finalize_sale:
            snapshots = post_sale_order(
                conn,
                order_id,
                [{"product_id": item[1], "quantity": item[5]} for item in prepared],
                order_date,
            )
            item_ids = conn.execute("SELECT id FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
            conn.executemany(
                "UPDATE order_items SET unit_cost_micro=?, cost_total_micro=? WHERE id=?",
                [(snapshot["unit_cost_micro"], snapshot["cost_total_micro"], row["id"]) for row, snapshot in zip(item_ids, snapshots)],
            )
    log_action("edit_order", "order", order_id, f"重编辑订单 {order_no}，状态 {status}，金额 {total} 分")


def mark_order_printed(order_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE orders SET status='printed', print_count=print_count+1, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('saved','printed')", (order_id,))
    log_action("print_order", "order", order_id, "用户确认打印/补打订单")


def void_order(order_id: int, reason: str, expected_version: int | None = None) -> None:
    reason = reason.strip()
    if not reason:
        raise ValueError("作废原因不能为空")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
        if order is None:
            raise ValueError("订单不存在")
        if expected_version is not None and int(order["version"]) != int(expected_version):
            raise ValueError("版本冲突，请刷新后重试")
        if order["status"] not in {"saved", "printed"}:
            raise ValueError("只有已保存或已打印订单可以作废")

        # 「销售单存在有效退货，不能作废」的旧限制已按用户决策移除（2026-10-07）：
        # 引用它的退货单本身继续有效（退货的库存与金额照常保留），只是失去「原销售单」
        # 这个追溯指向。用户确认不需要保留该追溯。库存不得为负的守恒校验保留。

        postings = conn.execute(
            """
            SELECT * FROM inventory_postings
            WHERE source_type IN ('sale', 'return') AND (source_id=? OR source_id LIKE ?)
            ORDER BY posting_seq
            """,
            (str(order_id), f"{order_id}:%"),
        ).fetchall()
        if postings:
            # 「必须是该商品最后一笔库存业务」的旧限制已按用户决策移除（2026-10-07）：
            # 反向过账后库存数量、销售/拿货金额、账款余额都精确回到删除前的正确值，
            # 只有移动均价/成本总额会漂（移动加权平均的固有性质，且成本只在本机内部页显示、
            # 不进入任何对外打印）。用户确认均价只是「预期参考」，不据此定售价，
            # 因此不再因为非末笔而拒绝删除。库存不得为负的守恒校验保留。
            for posting in reversed(postings):
                reverse_quantity = -int(posting["quantity_delta_3dp"])
                reverse_cost = -int(posting["cost_delta_micro"])
                state = conn.execute(
                    "SELECT * FROM product_inventory_state WHERE product_id=?",
                    (posting["product_id"],),
                ).fetchone()
                if state is None or int(state["quantity_3dp"]) + reverse_quantity < 0 or int(state["cost_total_micro"]) + reverse_cost < 0:
                    raise ValueError("作废后库存不能为负数")
                reverse_type = "sale_void" if posting["source_type"] == "sale" else "return_void"
                reverse_source_id = f"{posting['source_id']}:void"
                reverse_posting = conn.execute(
                    """
                    INSERT INTO inventory_postings(
                        product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (posting["product_id"], reverse_type, reverse_source_id, reverse_quantity, reverse_cost, order["order_date"]),
                )
                new_quantity = int(state["quantity_3dp"]) + reverse_quantity
                new_cost = int(state["cost_total_micro"]) + reverse_cost
                conn.execute(
                    """
                    UPDATE product_inventory_state
                    SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1,
                        last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
                    WHERE product_id=?
                    """,
                    (new_quantity, new_cost, _average_cost_micro_for_accounting(new_quantity, new_cost), reverse_posting.lastrowid, posting["product_id"]),
                )
        conn.execute("UPDATE orders SET status='void', void_reason=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (reason, order_id))
        log_action("void_order", "order", order_id, f"作废订单: {reason}", conn=conn)


def delete_order(order_id: int, reason: str = "用户删除", expected_version: int | None = None) -> None:
    """Move a draft or already-void order to recycle bin without changing business effects."""
    reason = (reason or "用户删除").strip()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT status, deleted_at, version FROM orders WHERE id=?", (order_id,)).fetchone()
        if order is None:
            raise ValueError("订单不存在")
        if order["deleted_at"] is not None:
            raise ValueError("订单已在回收站")
        if expected_version is not None and int(order["version"]) != int(expected_version):
            raise ValueError("版本冲突，请刷新后重试")
        if order["status"] in {"saved", "printed"}:
            raise ValueError("正式单据必须先作废后再删除")
        if order["status"] not in {"draft", "void"}:
            raise ValueError("当前订单状态不能删除")
        conn.execute(
            "UPDATE orders SET deleted_at=CURRENT_TIMESTAMP, delete_reason=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (reason, order_id),
        )
        log_action("delete_order", "order", order_id, "订单移入回收站", conn=conn)


# 列表/行内删除正式单据时自动写入的冲回原因（界面不再让用户填写）。
AUTO_REVERSE_REASON = "列表删除（自动冲回）"


def void_then_delete_order(order_id: int, reason: str = AUTO_REVERSE_REASON) -> None:
    """Delete any order from the list, voiding it first when it is a live document.

    A live (saved/printed) document carries real business effects, so it must be
    reversed before it can leave the ledger. Drafts have no postings and go straight
    to the recycle bin. ``void_order`` keeps enforcing conservation (last posting per
    product, dependent returns, non-negative stock) — its ValueError is surfaced, not
    swallowed, so a batch delete can never silently skip a document.
    """
    with get_db() as conn:
        row = conn.execute("SELECT status, version FROM orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
    if row is None:
        raise ValueError("订单不存在或已在回收站")
    if row["status"] in {"saved", "printed"}:
        void_order(order_id, reason, int(row["version"]))
        with get_db() as conn:
            row = conn.execute("SELECT version FROM orders WHERE id=?", (order_id,)).fetchone()
        delete_order(order_id, reason, int(row["version"]))
        return
    delete_order(order_id, reason, int(row["version"]))


def restore_order(order_id: int, expected_version: int | None = None) -> None:
    """Restore visibility only; a void order never becomes active again."""
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT deleted_at, version FROM orders WHERE id=?", (order_id,)).fetchone()
        if order is None:
            raise ValueError("订单不存在")
        if order["deleted_at"] is None:
            raise ValueError("订单不在回收站")
        if expected_version is not None and int(order["version"]) != int(expected_version):
            raise ValueError("版本冲突，请刷新后重试")
        conn.execute(
            "UPDATE orders SET deleted_at=NULL, delete_reason='', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (order_id,),
        )
        log_action("restore_order", "order", order_id, "订单从回收站恢复可见性", conn=conn)


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
        purchases = conn.execute("SELECT COALESCE(SUM(total_amount_cents), 0) AS v FROM purchase_orders WHERE customer_id=? AND status='saved' AND deleted_at IS NULL", (customer_id,)).fetchone()["v"]
        purchase_returns = conn.execute("SELECT COALESCE(SUM(total_amount_cents), 0) AS v FROM purchase_return_orders WHERE customer_id=? AND status='saved' AND deleted_at IS NULL", (customer_id,)).fetchone()["v"]
        return (int(customer["opening_balance_cents"] or 0) + int(orders or 0) + int(adjustments or 0)
                - int(payments or 0) - int(purchases or 0) + int(purchase_returns or 0))


def get_customer_account_ledger(customer_id: int, start_date: str = "", end_date: str = "", type_filter: list[str] | None = None) -> dict:
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

                UNION ALL

                SELECT
                    'purchase' AS source,
                    po.id AS source_id,
                    'purchase' AS type,
                    po.business_date AS date,
                    po.created_at AS sort_at,
                    po.order_no AS reference,
                    COALESCE((SELECT group_concat(trim(pi.product_name || CASE WHEN COALESCE(pi.spec,'')='' THEN '' ELSE ' ' || pi.spec END), '、') FROM purchase_order_items pi WHERE pi.purchase_order_id=po.id), '拿货') AS description,
                    po.total_amount_cents AS amount_cents,
                    CASE WHEN po.status='void' THEN 0 ELSE -po.total_amount_cents END AS balance_delta_cents,
                    CASE WHEN po.status='void' THEN 'void' ELSE 'active' END AS status,
                    '' AS void_reason
                FROM purchase_orders po
                WHERE po.customer_id=? AND po.deleted_at IS NULL AND po.status IN ('saved','void')

                UNION ALL

                SELECT
                    'purchase_return' AS source,
                    pr.id AS source_id,
                    'purchase_return' AS type,
                    pr.business_date AS date,
                    pr.created_at AS sort_at,
                    pr.order_no AS reference,
                    COALESCE((SELECT group_concat(trim(pri.product_name || CASE WHEN COALESCE(pri.spec,'')='' THEN '' ELSE ' ' || pri.spec END), '、') FROM purchase_return_items pri WHERE pri.purchase_return_id=pr.id), '退拿货') AS description,
                    pr.total_amount_cents AS amount_cents,
                    CASE WHEN pr.status='void' THEN 0 ELSE pr.total_amount_cents END AS balance_delta_cents,
                    CASE WHEN pr.status='void' THEN 'void' ELSE 'active' END AS status,
                    '' AS void_reason
                FROM purchase_return_orders pr
                WHERE pr.customer_id=? AND pr.deleted_at IS NULL AND pr.status IN ('saved','void')
            )
            SELECT source, source_id, type, date, sort_at, reference, description,
                   amount_cents, balance_delta_cents, status, void_reason
            FROM ledger
            ORDER BY date DESC, sort_at DESC, source ASC, source_id DESC
            """,
            (customer_id, customer_id, customer_id, customer_id, customer_id),
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
    purchase_net_cents = sum(
        entry["balance_delta_cents"] for entry in active_entries
        if entry["type"] in {"purchase", "purchase_return"}
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
        "purchases_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "purchase"),
        "purchase_returns_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "purchase_return"),
        "payments_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "payment"),
        "adjustments_cents": sum(entry["balance_delta_cents"] for entry in active_period_entries if entry["type"] == "adjustment"),
    }
    period["net_change_cents"] = sum(
        period[key] for key in ("sales_cents", "returns_cents", "purchases_cents",
                                "purchase_returns_cents", "payments_cents", "adjustments_cents")
    )

    allowed_types = {t for t in (type_filter or []) if t in {
        "sale", "return", "purchase", "purchase_return", "payment", "adjustment"}}
    displayed_entries = [
        entry for entry in visible_entries
        if not allowed_types or entry["type"] in allowed_types
    ]

    return {
        "customer": dict(customer),
        "opening_balance_cents": opening_balance_cents,
        "document_net_cents": document_net_cents,
        "purchase_net_cents": purchase_net_cents,
        "adjustment_cents": adjustment_cents,
        "payment_cents": payment_cents,
        "current_balance_cents": current_balance_cents,
        "period": period,
        "entries": displayed_entries,
    }
