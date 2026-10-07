from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from erp.db import get_db
from erp.utils.audit import log_action


_QUANTITY_SCALE = Decimal("1000")
_COST_SCALE = Decimal("1000000")
_ZERO = Decimal("0")


def _decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label}必须是有效数字") from None
    if not parsed.is_finite():
        raise ValueError(f"{label}必须是有限数字")
    return parsed


def _scaled_quantity(value: Any) -> int:
    quantity = _decimal(value, "数量")
    if quantity < _ZERO:
        raise ValueError("数量不能为负数")
    scaled = quantity * _QUANTITY_SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError("数量最多保留3位小数")
    return int(scaled)


def _cost_micro(value: Any, *, required: bool = True) -> int | None:
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError("数量大于零时必须填写期初成本")
        return None
    cost = _decimal(value, "成本")
    if cost < _ZERO:
        raise ValueError("成本不能为负数")
    scaled = cost * _COST_SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError("成本最多保留6位小数")
    return int(scaled)


def _date_value(value: str | None) -> str:
    result = (value or date.today().isoformat()).strip()
    try:
        date.fromisoformat(result)
    except ValueError:
        raise ValueError("业务日期格式无效") from None
    return result


def _payload_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _average_cost_micro(quantity_3dp: int, cost_total_micro: int) -> int | None:
    if quantity_3dp == 0:
        if cost_total_micro != 0:
            raise ValueError("库存数量为零时库存成本必须为零")
        return None
    return int(
        (Decimal(cost_total_micro) / Decimal(quantity_3dp) * _QUANTITY_SCALE)
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _cost_total_micro(quantity_3dp: int, unit_cost_micro: int) -> int:
    return int(
        (Decimal(quantity_3dp) * Decimal(unit_cost_micro) / _QUANTITY_SCALE)
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _price_cents(value: Any) -> int:
    price = _decimal(value, "拿货单价")
    if price < _ZERO:
        raise ValueError("拿货单价不能为负数")
    scaled = price * Decimal("100")
    if scaled != scaled.to_integral_value():
        raise ValueError("拿货单价最多保留2位小数")
    return int(scaled)


def _subtotal_cents(quantity_3dp: int, unit_price_cents: int) -> int:
    return int(
        (Decimal(quantity_3dp) * Decimal(unit_price_cents) / _QUANTITY_SCALE)
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _product_exists(conn, product_id: int):
    product = conn.execute(
        "SELECT id FROM products WHERE id=? AND deleted_at IS NULL",
        (product_id,),
    ).fetchone()
    if product is None:
        raise ValueError("商品不存在或已删除")


def _state_result(state) -> dict[str, Any]:
    return {
        "product_id": int(state["product_id"]),
        "version": int(state["version"]),
        "quantity_3dp": int(state["quantity_3dp"]),
        "cost_total_micro": int(state["cost_total_micro"]),
        "avg_cost_micro": state["avg_cost_micro"],
    }


def initialize_product(
    product_id: int,
    quantity: Any,
    unit_cost: Any,
    business_date: str | None,
    source: str,
    request_key: str,
    *,
    confirm_zero: bool = False,
) -> dict[str, Any]:
    quantity_3dp = _scaled_quantity(quantity)
    unit_cost_micro = _cost_micro(unit_cost, required=quantity_3dp > 0)
    if quantity_3dp == 0:
        if unit_cost_micro not in (None, 0):
            raise ValueError("零期初的成本必须为零")
        if not confirm_zero:
            raise ValueError("零期初必须明确确认")
        unit_cost_micro = 0
    assert unit_cost_micro is not None
    cost_total_micro = _cost_total_micro(quantity_3dp, unit_cost_micro)
    payload = {
        "product_id": int(product_id),
        "quantity_3dp": quantity_3dp,
        "unit_cost_micro": unit_cost_micro,
        "business_date": _date_value(business_date),
        "source": (source or "").strip(),
        "confirm_zero": bool(confirm_zero),
    }
    digest = _payload_hash(payload)
    with get_db() as conn:
        _product_exists(conn, product_id)
        existing_key = conn.execute(
            "SELECT product_id, payload_hash FROM inventory_initializations WHERE request_key=?",
            (request_key,),
        ).fetchone()
        if existing_key is not None:
            if existing_key["payload_hash"] != digest:
                raise ValueError("幂等键内容不一致")
            state = conn.execute(
                "SELECT * FROM product_inventory_state WHERE product_id=?",
                (product_id,),
            ).fetchone()
            return _state_result(state)
        existing = conn.execute(
            "SELECT * FROM inventory_initializations WHERE product_id=?",
            (product_id,),
        ).fetchone()
        if existing is not None:
            raise ValueError("商品已经初始化")
        conn.execute(
            """
            INSERT INTO product_inventory_state(
                product_id, enabled, quantity_3dp, cost_total_micro, avg_cost_micro, version
            ) VALUES (?, 1, ?, ?, ?, 1)
            """,
            (product_id, quantity_3dp, cost_total_micro, _average_cost_micro(quantity_3dp, cost_total_micro)),
        )
        conn.execute(
            """
            INSERT INTO inventory_initializations(
                product_id, quantity_3dp, cost_total_micro, business_date, source,
                request_key, payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, quantity_3dp, cost_total_micro, payload["business_date"], payload["source"], request_key, digest),
        )
        conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, 'initialization', ?, ?, ?, ?)
            """,
            (product_id, request_key, quantity_3dp, cost_total_micro, payload["business_date"]),
        )
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        log_action("initialize_inventory", "product", product_id, "启用商品期初库存", conn=conn)
        return _state_result(state)


def revise_initialization(
    product_id: int,
    quantity: Any,
    unit_cost: Any,
    reason: str,
    expected_version: int,
) -> dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("期初修订原因不能为空")
    quantity_3dp = _scaled_quantity(quantity)
    unit_cost_micro = _cost_micro(unit_cost, required=quantity_3dp > 0)
    if quantity_3dp == 0:
        if unit_cost_micro not in (None, 0):
            raise ValueError("零期初的成本必须为零")
        unit_cost_micro = 0
    assert unit_cost_micro is not None
    new_cost_total_micro = _cost_total_micro(quantity_3dp, unit_cost_micro)
    with get_db() as conn:
        _product_exists(conn, product_id)
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        initialization = conn.execute(
            "SELECT * FROM inventory_initializations WHERE product_id=?",
            (product_id,),
        ).fetchone()
        if state is None or initialization is None:
            raise ValueError("商品尚未初始化")
        if int(state["version"]) != int(expected_version):
            raise ValueError("版本冲突，请刷新后重试")
        if initialization["locked"]:
            raise ValueError("期初已锁定，不能修订")
        quantity_delta = quantity_3dp - int(state["quantity_3dp"])
        cost_delta = new_cost_total_micro - int(state["cost_total_micro"])
        next_version = int(state["version"]) + 1
        conn.execute(
            """
            UPDATE product_inventory_state
            SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (
                quantity_3dp,
                new_cost_total_micro,
                _average_cost_micro(quantity_3dp, new_cost_total_micro),
                next_version,
                product_id,
            ),
        )
        revision_no = int(initialization["revision_no"]) + 1
        conn.execute(
            """
            UPDATE inventory_initializations
            SET quantity_3dp=?, cost_total_micro=?, revision_no=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (quantity_3dp, new_cost_total_micro, revision_no, product_id),
        )
        conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, 'initialization_revision', ?, ?, ?, ?)
            """,
            (product_id, f"{product_id}:revision:{revision_no}", quantity_delta, cost_delta, initialization["business_date"]),
        )
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        log_action("revise_inventory_initialization", "product", product_id, reason, conn=conn)
        return _state_result(state)


def post_inventory_event(
    product_id: int,
    quantity_delta: Any,
    cost_delta: Any,
    source_type: str,
    source_id: str,
    business_date: str | None,
) -> dict[str, Any]:
    quantity_3dp = _scaled_quantity(quantity_delta)
    if quantity_3dp <= 0:
        raise ValueError("库存事件数量必须大于0")
    unit_cost_micro = _cost_micro(cost_delta)
    assert unit_cost_micro is not None
    cost_delta_micro = _cost_total_micro(quantity_3dp, unit_cost_micro)
    business_date = _date_value(business_date)
    with get_db() as conn:
        _product_exists(conn, product_id)
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        if state is None or not state["enabled"]:
            raise ValueError("商品尚未启用库存")
        if conn.execute(
            "SELECT 1 FROM inventory_postings WHERE source_type=? AND source_id=?",
            (source_type, source_id),
        ).fetchone():
            raise ValueError("库存事件已存在")
        new_quantity = int(state["quantity_3dp"]) + quantity_3dp
        new_cost = int(state["cost_total_micro"]) + cost_delta_micro
        next_version = int(state["version"]) + 1
        posting = conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (product_id, source_type, source_id, quantity_3dp, cost_delta_micro, business_date),
        )
        conn.execute(
            """
            UPDATE product_inventory_state
            SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=?, last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (
                new_quantity,
                new_cost,
                _average_cost_micro(new_quantity, new_cost),
                next_version,
                posting.lastrowid,
                product_id,
            ),
        )
        conn.execute(
            "UPDATE inventory_initializations SET locked=1, updated_at=CURRENT_TIMESTAMP WHERE product_id=?",
            (product_id,),
        )
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        log_action("post_inventory_event", "product", product_id, f"库存事件: {source_type}/{source_id}", conn=conn)
        return _state_result(state)


def _outbound_cost_micro(quantity_3dp: int, stock_quantity_3dp: int, stock_cost_micro: int) -> int:
    """Allocate exact remaining cost, never the already-rounded reference average."""
    if quantity_3dp <= 0 or stock_quantity_3dp <= 0 or quantity_3dp > stock_quantity_3dp:
        raise ValueError("库存不足或出库数量无效")
    if stock_cost_micro < 0:
        raise ValueError("库存成本不能为负数")
    # Integer ROUND_HALF_UP also absorbs the entire residue on final clearance.
    return (2 * stock_cost_micro * quantity_3dp + stock_quantity_3dp) // (2 * stock_quantity_3dp)


def post_sale_order(conn, order_id: int, items: list[dict[str, Any]], business_date: str) -> list[dict[str, int | None]]:
    """Consume enabled inventory and return immutable sale cost snapshots."""
    snapshots: list[dict[str, int | None]] = []
    for line_no, item in enumerate(items, start=1):
        product_id = int(item["product_id"])
        quantity_3dp = _scaled_quantity(item["quantity"])
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        if state is None:
            snapshots.append({"unit_cost_micro": None, "cost_total_micro": None})
            continue
        if not state["enabled"]:
            raise ValueError("商品尚未启用库存")
        if int(state["quantity_3dp"]) < quantity_3dp:
            raise ValueError("库存不足")
        unit_cost_micro = state["avg_cost_micro"]
        if unit_cost_micro is None:
            raise ValueError("商品当前没有可用库存成本")
        cost_total_micro = _outbound_cost_micro(quantity_3dp, int(state["quantity_3dp"]), int(state["cost_total_micro"]))
        new_quantity = int(state["quantity_3dp"]) - quantity_3dp
        new_cost = int(state["cost_total_micro"]) - cost_total_micro
        if new_cost < 0:
            raise ValueError("销售后库存成本不能为负数")
        source_id = str(order_id) if len(items) == 1 else f"{order_id}:{line_no}"
        posting = conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, 'sale', ?, ?, ?, ?)
            """,
            (product_id, source_id, -quantity_3dp, -cost_total_micro, business_date),
        )
        conn.execute(
            """
            UPDATE product_inventory_state
            SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1,
                last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (new_quantity, new_cost, _average_cost_micro(new_quantity, new_cost), posting.lastrowid, product_id),
        )
        conn.execute(
            "UPDATE inventory_initializations SET locked=1, updated_at=CURRENT_TIMESTAMP WHERE product_id=?",
            (product_id,),
        )
        snapshots.append({"unit_cost_micro": int(unit_cost_micro), "cost_total_micro": cost_total_micro})
    return snapshots


def post_return_order(conn, order_id: int, items: list[dict[str, Any]], business_date: str) -> None:
    """Put returned goods back using the original sale-line cost allocation."""
    for line_no, item in enumerate(items, start=1):
        product_id = int(item["product_id"])
        quantity_3dp = int(item["quantity_3dp"])
        cost_total_micro = int(item["cost_total_micro"])
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=? AND enabled=1",
            (product_id,),
        ).fetchone()
        if state is None:
            raise ValueError("商品尚未启用库存")
        if quantity_3dp <= 0 or cost_total_micro < 0:
            raise ValueError("退货库存流水参数无效")
        source_id = str(order_id) if len(items) == 1 else f"{order_id}:{line_no}"
        posting = conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, 'return', ?, ?, ?, ?)
            """,
            (product_id, source_id, quantity_3dp, cost_total_micro, business_date),
        )
        new_quantity = int(state["quantity_3dp"]) + quantity_3dp
        new_cost = int(state["cost_total_micro"]) + cost_total_micro
        conn.execute(
            """
            UPDATE product_inventory_state
            SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1,
                last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (new_quantity, new_cost, _average_cost_micro(new_quantity, new_cost), posting.lastrowid, product_id),
        )
        conn.execute(
            "UPDATE inventory_initializations SET locked=1, updated_at=CURRENT_TIMESTAMP WHERE product_id=?",
            (product_id,),
        )


def post_typed_return_order(conn, order_id: int, items: list[dict[str, Any]], business_date: str) -> list[dict[str, int | None]]:
    """Add goods back at the current moving-average cost.

    Used by decoupled manual returns (a return no longer needs an original sale),
    so the cost basis is the product's current moving-average cost rather than an
    original sale-line allocation.
    """
    snapshots: list[dict[str, int | None]] = []
    for line_no, item in enumerate(items, start=1):
        product_id = int(item["product_id"])
        quantity_3dp = _scaled_quantity(item["quantity"])
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        if state is None:
            # Mirrors sales: products without an inventory record carry no stock,
            # so the return only affects the customer balance and cost stays unknown.
            snapshots.append({"unit_cost_micro": None, "cost_total_micro": None})
            continue
        if not state["enabled"]:
            raise ValueError("商品尚未启用库存")
        unit_cost_micro = state["avg_cost_micro"]
        if unit_cost_micro is None:
            raise ValueError("商品当前没有可用库存成本")
        cost_total_micro = _cost_total_micro(quantity_3dp, int(unit_cost_micro))
        if cost_total_micro < 0:
            raise ValueError("退货库存成本无效")
        source_id = str(order_id) if len(items) == 1 else f"{order_id}:{line_no}"
        posting = conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, 'return', ?, ?, ?, ?)
            """,
            (product_id, source_id, quantity_3dp, cost_total_micro, business_date),
        )
        new_quantity = int(state["quantity_3dp"]) + quantity_3dp
        new_cost = int(state["cost_total_micro"]) + cost_total_micro
        conn.execute(
            """
            UPDATE product_inventory_state
            SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1,
                last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (new_quantity, new_cost, _average_cost_micro(new_quantity, new_cost), posting.lastrowid, product_id),
        )
        conn.execute(
            "UPDATE inventory_initializations SET locked=1, updated_at=CURRENT_TIMESTAMP WHERE product_id=?",
            (product_id,),
        )
        snapshots.append({"unit_cost_micro": int(unit_cost_micro), "cost_total_micro": cost_total_micro})
    return snapshots


def post_typed_purchase_return(conn, order_id: int, items: list[dict[str, Any]], business_date: str) -> list[dict[str, int | None]]:
    """Consume enabled inventory at the current moving-average cost.

    Used by decoupled manual purchase returns (a return no longer needs an original
    inbound order), so outbound cost follows the product's current moving-average
    cost and exact remaining cost allocation, mirroring the sales outbound path.
    """
    snapshots: list[dict[str, int | None]] = []
    for line_no, item in enumerate(items, start=1):
        product_id = int(item["product_id"])
        quantity_3dp = int(item["quantity_3dp"])
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=?",
            (product_id,),
        ).fetchone()
        if state is None or not state["enabled"]:
            raise ValueError("商品尚未启用库存")
        if int(state["quantity_3dp"]) < quantity_3dp:
            raise ValueError("库存不足")
        unit_cost_micro = state["avg_cost_micro"]
        if unit_cost_micro is None:
            raise ValueError("商品当前没有可用库存成本")
        cost_total_micro = _outbound_cost_micro(quantity_3dp, int(state["quantity_3dp"]), int(state["cost_total_micro"]))
        new_quantity = int(state["quantity_3dp"]) - quantity_3dp
        new_cost = int(state["cost_total_micro"]) - cost_total_micro
        if new_cost < 0:
            raise ValueError("退拿货后库存成本不能为负数")
        source_id = str(order_id) if len(items) == 1 else f"{order_id}:{line_no}"
        posting = conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, 'purchase_return', ?, ?, ?, ?)
            """,
            (product_id, source_id, -quantity_3dp, -cost_total_micro, business_date),
        )
        conn.execute(
            """
            UPDATE product_inventory_state
            SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1,
                last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (new_quantity, new_cost, _average_cost_micro(new_quantity, new_cost), posting.lastrowid, product_id),
        )
        conn.execute("UPDATE inventory_initializations SET locked=1, updated_at=CURRENT_TIMESTAMP WHERE product_id=?", (product_id,))
        snapshots.append({"unit_cost_micro": int(unit_cost_micro), "cost_total_micro": cost_total_micro, "posting_seq": int(posting.lastrowid)})
    return snapshots


def _purchase_customer_id(value: Any) -> int:
    text = str(value)
    if not text.isascii() or not text.isdigit() or int(text) <= 0 or int(text) > 9223372036854775807:
        raise ValueError("必须选择已建档的往来对象")
    return int(text)


def create_purchase_order(
    order_no: str,
    business_date: str | None,
    items: list[dict[str, Any]],
    *,
    customer_id: int,
    request_key: str,
    source_notes: str = "",
    status: str = "saved",
) -> dict[str, Any]:
    """Create a draft or saved inbound order; saved orders post in this transaction."""
    customer_id = _purchase_customer_id(customer_id)
    order_no = (order_no or "").strip()
    request_key = (request_key or "").strip()
    if not order_no:
        raise ValueError("拿货单号不能为空")
    if not request_key:
        raise ValueError("拿货单幂等键不能为空")
    if status not in {"draft", "saved"}:
        raise ValueError("拿货单状态只能是草稿或正式保存")
    if not items:
        raise ValueError("拿货单至少需要一行商品")
    business_date = _date_value(business_date)
    if business_date > date.today().isoformat():
        raise ValueError("拿货日期不能晚于今天")
    prepared: list[dict[str, Any]] = []
    for raw in items:
        product_id = int(raw.get("product_id"))
        quantity_3dp = _scaled_quantity(raw.get("quantity"))
        if quantity_3dp <= 0:
            raise ValueError("数量必须大于0")
        unit_cost_cents = _price_cents(raw.get("unit_cost_yuan"))
        prepared.append({"product_id": product_id, "quantity_3dp": quantity_3dp, "unit_cost_cents": unit_cost_cents})
    payload = {
        "order_no": order_no,
        "customer_id": customer_id,
        "business_date": business_date,
        "items": prepared,
        "source_notes": (source_notes or "").strip(),
        "status": status,
    }
    digest = _payload_hash(payload)
    total_amount_cents = sum(_subtotal_cents(item["quantity_3dp"], item["unit_cost_cents"]) for item in prepared)

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, order_no, total_amount_cents, payload_hash FROM purchase_orders WHERE request_key=?",
            (request_key,),
        ).fetchone()
        if existing is not None:
            if existing["payload_hash"] != digest:
                raise ValueError("幂等键内容不一致")
            return {
                "order_id": int(existing["id"]),
                "order_no": existing["order_no"],
                "total_amount_cents": int(existing["total_amount_cents"]),
            }
        if conn.execute("SELECT 1 FROM purchase_orders WHERE order_no=?", (order_no,)).fetchone() is not None:
            raise ValueError("拿货单号已存在")
        customer = conn.execute("SELECT name FROM customers WHERE id=? AND deleted_at IS NULL AND is_active=1", (customer_id,)).fetchone()
        if customer is None:
            raise ValueError("往来对象不存在或已停用")

        checked: list[dict[str, Any]] = []
        for item in prepared:
            _product_exists(conn, item["product_id"])
            state = conn.execute(
                "SELECT * FROM product_inventory_state WHERE product_id=? AND enabled=1",
                (item["product_id"],),
            ).fetchone()
            if status == "saved" and state is None:
                raise ValueError("商品尚未启用库存")
            initialization = conn.execute(
                "SELECT business_date FROM inventory_initializations WHERE product_id=?",
                (item["product_id"],),
            ).fetchone()
            if initialization is not None and business_date < initialization["business_date"]:
                raise ValueError("拿货日期不能早于商品期初日期")
            checked.append(item)

        cur = conn.execute(
            """
            INSERT INTO purchase_orders(order_no, business_date, total_amount_cents, status, source_notes, request_key, payload_hash, customer_id, customer_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_no, business_date, total_amount_cents, status, payload["source_notes"], request_key, digest, customer_id, customer["name"]),
        )
        order_id = int(cur.lastrowid)
        for line_no, item in enumerate(checked, start=1):
            product = conn.execute("SELECT name, spec, unit FROM products WHERE id=?", (item["product_id"],)).fetchone()
            subtotal_cents = _subtotal_cents(item["quantity_3dp"], item["unit_cost_cents"])
            conn.execute(
                """
                INSERT INTO purchase_order_items(
                    purchase_order_id, product_id, product_name, spec, unit,
                    quantity_3dp, unit_cost_cents, subtotal_cents
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, item["product_id"], product["name"], product["spec"] or "", product["unit"], item["quantity_3dp"], item["unit_cost_cents"], subtotal_cents),
            )
            if status == "draft":
                continue
            state = conn.execute(
                "SELECT * FROM product_inventory_state WHERE product_id=? AND enabled=1",
                (item["product_id"],),
            ).fetchone()
            if state is None:
                raise ValueError("商品尚未启用库存")
            cost_delta_micro = subtotal_cents * 10_000
            posting = conn.execute(
                """
                INSERT INTO inventory_postings(
                    product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
                ) VALUES (?, 'purchase', ?, ?, ?, ?)
                """,
                (item["product_id"], str(order_id) if len(checked) == 1 else f"{order_id}:{line_no}", item["quantity_3dp"], cost_delta_micro, business_date),
            )
            new_quantity = int(state["quantity_3dp"]) + item["quantity_3dp"]
            new_cost = int(state["cost_total_micro"]) + cost_delta_micro
            conn.execute(
                """
                UPDATE product_inventory_state
                SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1,
                    last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
                WHERE product_id=?
                """,
                (new_quantity, new_cost, _average_cost_micro(new_quantity, new_cost), posting.lastrowid, item["product_id"]),
            )
            conn.execute("UPDATE inventory_initializations SET locked=1, updated_at=CURRENT_TIMESTAMP WHERE product_id=?", (item["product_id"],))
        log_action("create_purchase_order", "purchase_order", order_id, f"新增拿货单 {order_no}", conn=conn)
        return {"order_id": order_id, "order_no": order_no, "total_amount_cents": total_amount_cents}


def _prepared_purchase_items(items: list[dict[str, Any]]) -> list[dict[str, int]]:
    if not items:
        raise ValueError("拿货单至少需要一行商品")
    prepared = []
    for raw in items:
        try:
            product_id = int(raw.get("product_id"))
        except (TypeError, ValueError):
            raise ValueError("必须选择具体商品") from None
        quantity_3dp = _scaled_quantity(raw.get("quantity"))
        if quantity_3dp <= 0:
            raise ValueError("数量必须大于0")
        unit_cost_cents = _price_cents(raw.get("unit_cost_yuan"))
        prepared.append({"product_id": product_id, "quantity_3dp": quantity_3dp, "unit_cost_cents": unit_cost_cents})
    return prepared


def _validate_purchase_items(conn, business_date: str, prepared: list[dict[str, int]], *, require_enabled: bool = True) -> None:
    for item in prepared:
        _product_exists(conn, item["product_id"])
        state = conn.execute(
            "SELECT 1 FROM product_inventory_state WHERE product_id=? AND enabled=1",
            (item["product_id"],),
        ).fetchone()
        if require_enabled and state is None:
            raise ValueError("商品尚未启用库存")
        initialization = conn.execute(
            "SELECT business_date FROM inventory_initializations WHERE product_id=?",
            (item["product_id"],),
        ).fetchone()
        if initialization is not None and business_date < initialization["business_date"]:
            raise ValueError("拿货日期不能早于商品期初日期")


def _post_purchase_items(conn, order_id: int, business_date: str, items) -> None:
    for line_no, item in enumerate(items, start=1):
        state = conn.execute(
            "SELECT * FROM product_inventory_state WHERE product_id=? AND enabled=1",
            (item["product_id"],),
        ).fetchone()
        if state is None:
            raise ValueError("商品尚未启用库存")
        source_id = str(order_id) if len(items) == 1 else f"{order_id}:{line_no}"
        cost_delta_micro = _subtotal_cents(item["quantity_3dp"], item["unit_cost_cents"]) * 10_000
        posting = conn.execute(
            """
            INSERT INTO inventory_postings(
                product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date
            ) VALUES (?, 'purchase', ?, ?, ?, ?)
            """,
            (item["product_id"], source_id, item["quantity_3dp"], cost_delta_micro, business_date),
        )
        new_quantity = int(state["quantity_3dp"]) + item["quantity_3dp"]
        new_cost = int(state["cost_total_micro"]) + cost_delta_micro
        conn.execute(
            """
            UPDATE product_inventory_state
            SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1,
                last_posting_seq=?, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (new_quantity, new_cost, _average_cost_micro(new_quantity, new_cost), posting.lastrowid, item["product_id"]),
        )
        conn.execute("UPDATE inventory_initializations SET locked=1, updated_at=CURRENT_TIMESTAMP WHERE product_id=?", (item["product_id"],))


def _order_items_as_prepared(conn, order_id: int) -> list[dict[str, int]]:
    rows = conn.execute(
        "SELECT product_id, quantity_3dp, unit_cost_cents FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id",
        (order_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_purchase_draft(
    order_id: int,
    business_date: str | None,
    items: list[dict[str, Any]],
    *,
    customer_id: int,
    source_notes: str = "",
    status: str = "draft",
) -> dict[str, Any]:
    customer_id = _purchase_customer_id(customer_id)
    if status not in {"draft", "saved"}:
        raise ValueError("拿货单状态只能是草稿或正式保存")
    prepared = _prepared_purchase_items(items)
    total_amount_cents = sum(_subtotal_cents(item["quantity_3dp"], item["unit_cost_cents"]) for item in prepared)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
        if order is None:
            raise ValueError("拿货单不存在")
        if order["status"] != "draft":
            raise ValueError("正式拿货单经济字段已锁定")
        customer = conn.execute("SELECT name FROM customers WHERE id=? AND deleted_at IS NULL AND is_active=1", (customer_id,)).fetchone()
        if customer is None:
            raise ValueError("往来对象不存在或已停用")
        # Editing never changes the allocated number or original business date.
        business_date = order["business_date"]
        _validate_purchase_items(conn, business_date, prepared, require_enabled=status == "saved")
        customer_name = order["customer_name"] if order["customer_id"] == customer_id else customer["name"]
        payload = {"order_no": order["order_no"], "customer_id": customer_id, "business_date": business_date, "items": prepared, "source_notes": (source_notes or "").strip(), "status": status}
        conn.execute(
            "UPDATE purchase_orders SET total_amount_cents=?, source_notes=?, payload_hash=?, customer_id=?, customer_name=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (total_amount_cents, payload["source_notes"], _payload_hash(payload), customer_id, customer_name, status, order_id),
        )
        conn.execute("DELETE FROM purchase_order_items WHERE purchase_order_id=?", (order_id,))
        for item in prepared:
            product = conn.execute("SELECT name, spec, unit FROM products WHERE id=?", (item["product_id"],)).fetchone()
            conn.execute(
                "INSERT INTO purchase_order_items(purchase_order_id, product_id, product_name, spec, unit, quantity_3dp, unit_cost_cents, subtotal_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, item["product_id"], product["name"], product["spec"] or "", product["unit"], item["quantity_3dp"], item["unit_cost_cents"], _subtotal_cents(item["quantity_3dp"], item["unit_cost_cents"])),
            )
        if status == "saved":
            _post_purchase_items(conn, order_id, business_date, prepared)
            log_action("finalize_purchase_order", "purchase_order", order_id, f"正式保存拿货单 {order['order_no']}", conn=conn)
        return {"order_id": order_id, "order_no": order["order_no"], "total_amount_cents": total_amount_cents}


def finalize_purchase_order(order_id: int) -> dict[str, Any]:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
        if order is None:
            raise ValueError("拿货单不存在")
        if order["status"] == "saved":
            return {"order_id": order_id, "order_no": order["order_no"], "total_amount_cents": int(order["total_amount_cents"]), "status": "saved"}
        if order["status"] != "draft":
            raise ValueError("已作废拿货单不能正式保存")
        customer_id = _purchase_customer_id(order["customer_id"])
        if conn.execute("SELECT 1 FROM customers WHERE id=? AND deleted_at IS NULL AND is_active=1", (customer_id,)).fetchone() is None:
            raise ValueError("往来对象不存在或已停用")
        items = _order_items_as_prepared(conn, order_id)
        _validate_purchase_items(conn, order["business_date"], items)
        _post_purchase_items(conn, order_id, order["business_date"], items)
        conn.execute("UPDATE purchase_orders SET status='saved', updated_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
        log_action("finalize_purchase_order", "purchase_order", order_id, f"正式保存拿货单 {order['order_no']}", conn=conn)
        return {"order_id": order_id, "order_no": order["order_no"], "total_amount_cents": int(order["total_amount_cents"]), "status": "saved"}


def latest_effective_inventory_posting(conn, product_id: int):
    """Ignore reversed pairs, not physical latest rows or mutable visibility."""
    rows = conn.execute("SELECT * FROM inventory_postings WHERE product_id=? ORDER BY posting_seq DESC", (product_id,)).fetchall()
    reversed_sources = {
        (str(row["source_type"])[:-5], str(row["source_id"])[:-5])
        for row in rows if str(row["source_type"]).endswith("_void") and str(row["source_id"]).endswith(":void")
    }
    for row in rows:
        kind = str(row["source_type"])
        if kind.endswith("_void") or (kind, str(row["source_id"])) in reversed_sources:
            continue
        return row
    return None


def void_purchase_order(order_id: int, reason: str) -> dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("作废原因不能为空")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
        if order is None:
            raise ValueError("拿货单不存在")
        if order["status"] != "saved":
            raise ValueError("只有正式保存的拿货单可以作废")
        items = _order_items_as_prepared(conn, order_id)
        expected_source_ids_by_product: dict[int, set[str]] = {}
        for line_no, item in enumerate(items, start=1):
            source_id = str(order_id) if len(items) == 1 else f"{order_id}:{line_no}"
            expected_source_ids_by_product.setdefault(item["product_id"], set()).add(source_id)
        # 「必须是该商品最后一笔库存业务」的旧限制已按用户决策移除（2026-10-07）。
        # 保留「原库存流水必须存在且数量一致」的真实性校验：那是数据完整性，不是顺序限制。
        for line_no, item in enumerate(reversed(items), start=1):
            source_id = str(order_id) if len(items) == 1 else f"{order_id}:{len(items) - line_no + 1}"
            state = conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (item["product_id"],)).fetchone()
            original = conn.execute(
                "SELECT quantity_delta_3dp, cost_delta_micro FROM inventory_postings WHERE source_type='purchase' AND source_id=? AND product_id=?",
                (source_id, item["product_id"]),
            ).fetchone()
            if original is None or int(original["quantity_delta_3dp"]) != item["quantity_3dp"]:
                raise ValueError("拿货原库存流水不完整，不能作废")
            cost_delta_micro = int(original["cost_delta_micro"])
            new_quantity = int(state["quantity_3dp"]) - item["quantity_3dp"]
            new_cost = int(state["cost_total_micro"]) - cost_delta_micro
            if new_quantity < 0 or new_cost < 0:
                raise ValueError("作废后库存成本不能为负数")
            posting = conn.execute(
                "INSERT INTO inventory_postings(product_id, source_type, source_id, quantity_delta_3dp, cost_delta_micro, business_date) VALUES (?, 'purchase_void', ?, ?, ?, ?)",
                (item["product_id"], f"{source_id}:void", -item["quantity_3dp"], -cost_delta_micro, order["business_date"]),
            )
            conn.execute(
                "UPDATE product_inventory_state SET quantity_3dp=?, cost_total_micro=?, avg_cost_micro=?, version=version+1, last_posting_seq=?, updated_at=CURRENT_TIMESTAMP WHERE product_id=?",
                (new_quantity, new_cost, _average_cost_micro(new_quantity, new_cost), posting.lastrowid, item["product_id"]),
            )
        conn.execute("UPDATE purchase_orders SET status='void', void_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (reason, order_id))
        log_action("void_purchase_order", "purchase_order", order_id, f"作废拿货单: {reason}", conn=conn)
        return {"order_id": order_id, "order_no": order["order_no"], "status": "void"}


def delete_purchase_order(order_id: int, reason: str = "用户删除") -> None:
    """Move a draft/void 拿货单 to the recycle bin without touching stock."""
    reason = (reason or "用户删除").strip()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT status, deleted_at FROM purchase_orders WHERE id=?", (order_id,)).fetchone()
        if order is None:
            raise ValueError("拿货单不存在")
        if order["deleted_at"] is not None:
            raise ValueError("拿货单已在回收站")
        if order["status"] not in {"draft", "void"}:
            raise ValueError("正式拿货单必须先作废，不能直接删除")
        conn.execute(
            "UPDATE purchase_orders SET deleted_at=CURRENT_TIMESTAMP, delete_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (reason, order_id),
        )
        log_action("delete_purchase_order", "purchase_order", order_id, "拿货单移入回收站", conn=conn)


def void_then_delete_purchase_order(order_id: int, reason: str = "列表删除（自动冲回）") -> None:
    """Delete any 拿货单 from the list, voiding it first when it is a live document.

    Mirrors ``accounting.void_then_delete_order``: live documents are reversed so the
    stock ledger stays conserved; ``void_purchase_order`` keeps its own guards
    (last posting per product, non-negative stock) and their errors are surfaced.
    """
    with get_db() as conn:
        row = conn.execute("SELECT status FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (order_id,)).fetchone()
    if row is None:
        raise ValueError("拿货单不存在或已在回收站")
    if row["status"] == "saved":
        void_purchase_order(order_id, reason)
        delete_purchase_order(order_id, reason)
        return
    delete_purchase_order(order_id, reason)
