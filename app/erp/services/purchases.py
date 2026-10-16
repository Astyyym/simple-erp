"""拿货 / 退拿货单据服务（原 `services/inventory.py` 的去库存化版本）。

2026-10-16（用户决策 ①）：拿货/退拿货保留为**纯流水单据**——单据 CRUD、打印、NH 单号、
对账并入、账款抵扣、回收站、当天历史全部照旧，只是**不再动库存、不再产生成本**。

因此原 `inventory.py` 里的库存引擎（期初、库存流水、移动平均成本、销售/退货/拿货过账与冲回）
全部删除，只保留单据 CRUD 与通用解析 helper；模块同时改名为 `purchases.py`——
本版本之后「库存」概念在代码里不该再存在，留一个叫 `inventory` 却没有库存的模块只会误导后来人。

保留的 helper 被 `services/purchase_returns.py`（退拿货）复用，所以它们必须留在本模块而不是随手内联。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from erp.db import get_db
from erp.utils.audit import log_action


_QUANTITY_SCALE = Decimal("1000")
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


def _product_exists(conn, product_id: int) -> None:
    product = conn.execute(
        "SELECT id FROM products WHERE id=? AND deleted_at IS NULL",
        (product_id,),
    ).fetchone()
    if product is None:
        raise ValueError("商品不存在或已删除")


def _purchase_customer_id(value: Any) -> int:
    text = str(value)
    if not text.isascii() or not text.isdigit() or int(text) <= 0 or int(text) > 9223372036854775807:
        raise ValueError("必须选择已建档的往来对象")
    return int(text)


def _prepared_purchase_items(items: list[dict[str, Any]]) -> list[dict[str, int]]:
    """校验并整备拿货明细；同时保留**本单单位**（原始提交值，可能为空）。

    决策（2026-10-12 §4.3 / §六 #16）：拿货页单位可编辑，只作用于本单明细，
    **不回写 `products.unit`**。提交空单位由写入点回落该商品的**档案单位**
    （见 `update_purchase_draft` 的 INSERT）。
    """
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
        prepared.append({
            "product_id": product_id,
            "quantity_3dp": quantity_3dp,
            "unit_cost_cents": unit_cost_cents,
            "unit": str(raw.get("unit") or "").strip(),
        })
    return prepared


def _validate_purchase_items(conn, prepared: list[dict[str, int]]) -> None:
    """只校验商品存在。

    v15 起不再有库存启用检查与「拿货日期不得早于商品期初」——商品期初已随库存系统删除
    （用户决策 ②/③），日期下限随之取消。
    """
    for item in prepared:
        _product_exists(conn, item["product_id"])


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
    """新建拿货单（草稿或正式）。正式单与草稿的差别只剩状态标记——不再过账库存。"""
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
    prepared = _prepared_purchase_items(items)
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

        _validate_purchase_items(conn, prepared)

        cur = conn.execute(
            """
            INSERT INTO purchase_orders(order_no, business_date, total_amount_cents, status, source_notes, request_key, payload_hash, customer_id, customer_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_no, business_date, total_amount_cents, status, payload["source_notes"], request_key, digest, customer_id, customer["name"]),
        )
        order_id = int(cur.lastrowid)
        for item in prepared:
            product = conn.execute("SELECT name, unit FROM products WHERE id=?", (item["product_id"],)).fetchone()
            item_unit = str(item.get("unit") or "").strip() or str(product["unit"] or "")
            subtotal_cents = _subtotal_cents(item["quantity_3dp"], item["unit_cost_cents"])
            conn.execute(
                """
                INSERT INTO purchase_order_items(
                    purchase_order_id, product_id, product_name, unit,
                    quantity_3dp, unit_cost_cents, subtotal_cents
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, item["product_id"], product["name"], item_unit, item["quantity_3dp"], item["unit_cost_cents"], subtotal_cents),
            )
        log_action("create_purchase_order", "purchase_order", order_id, f"新增拿货单 {order_no}", conn=conn)
        return {"order_id": order_id, "order_no": order_no, "total_amount_cents": total_amount_cents}


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
        customer = conn.execute("SELECT name FROM customers WHERE id=? AND deleted_at IS NULL AND is_active=1", (customer_id,)).fetchone()
        if customer is None:
            raise ValueError("往来对象不存在或已停用")
        # Editing never changes the allocated number or original business date.
        business_date = order["business_date"]
        _validate_purchase_items(conn, prepared)
        customer_name = order["customer_name"] if order["customer_id"] == customer_id else customer["name"]
        payload = {"order_no": order["order_no"], "customer_id": customer_id, "business_date": business_date, "items": prepared, "source_notes": (source_notes or "").strip(), "status": status}
        conn.execute(
            "UPDATE purchase_orders SET total_amount_cents=?, source_notes=?, payload_hash=?, customer_id=?, customer_name=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (total_amount_cents, payload["source_notes"], _payload_hash(payload), customer_id, customer_name, status, order_id),
        )
        conn.execute("DELETE FROM purchase_order_items WHERE purchase_order_id=?", (order_id,))
        for item in prepared:
            product = conn.execute("SELECT name, unit FROM products WHERE id=?", (item["product_id"],)).fetchone()
            item_unit = str(item.get("unit") or "").strip() or str(product["unit"] or "")
            conn.execute(
                "INSERT INTO purchase_order_items(purchase_order_id, product_id, product_name, unit, quantity_3dp, unit_cost_cents, subtotal_cents) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (order_id, item["product_id"], product["name"], item_unit, item["quantity_3dp"], item["unit_cost_cents"], _subtotal_cents(item["quantity_3dp"], item["unit_cost_cents"])),
            )
        if status == "saved":
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
        _validate_purchase_items(conn, items)
        conn.execute("UPDATE purchase_orders SET status='saved', updated_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
        log_action("finalize_purchase_order", "purchase_order", order_id, f"正式保存拿货单 {order['order_no']}", conn=conn)
        return {"order_id": order_id, "order_no": order["order_no"], "total_amount_cents": int(order["total_amount_cents"]), "status": "saved"}


def void_purchase_order(order_id: int, reason: str) -> dict[str, Any]:
    """作废拿货单：只改状态与原因。

    v15 起不再有库存冲回——库存系统已删除，作废对库存与成本都没有影响。
    """
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
        conn.execute("UPDATE purchase_orders SET status='void', void_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (reason, order_id))
        log_action("void_purchase_order", "purchase_order", order_id, f"作废拿货单: {reason}", conn=conn)
        return {"order_id": order_id, "order_no": order["order_no"], "status": "void"}


def delete_purchase_order(order_id: int, reason: str = "用户删除") -> None:
    """把草稿/已作废的拿货单移入回收站。"""
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
    """从列表删除任意拿货单：正式单先作废再进回收站。

    与 `accounting.void_then_delete_order` 同形。v15 起作废不再冲回库存，
    这条路径只剩「正式单不能直接删」的状态机要求。
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
