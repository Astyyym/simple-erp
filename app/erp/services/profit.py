from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from erp.db import get_db


_MICRO_PER_CENT = Decimal("10000")


def _micro_to_cents(value: int) -> int:
    return int((Decimal(value) / _MICRO_PER_CENT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _empty_summary() -> dict[str, Any]:
    return {
        "order_count": 0,
        "sales_order_count": 0,
        "return_order_count": 0,
        "sales_cents": 0,
        "returns_cents": 0,
        "net_sales_cents": 0,
        "known_sales_cents": 0,
        "known_returns_cents": 0,
        "known_net_sales_cents": 0,
        "net_cost_cents": 0,
        "gross_profit_cents": 0,
        "gross_margin_rate": None,
        "cost_complete": True,
        "unknown_order_count": 0,
        "unknown_line_count": 0,
        "orders": [],
    }


def summarize_profit(
    *,
    start_date: str = "",
    end_date: str = "",
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Aggregate valid sales/returns from immutable line cost snapshots.

    Unknown-cost lines are excluded from the known-cost subtotal and make the
    selected range incomplete; they are never treated as zero cost.
    """
    conditions = [
        "o.deleted_at IS NULL",
        "o.status IN ('saved', 'printed')",
        "o.order_type IN ('sale', 'return')",
    ]
    params: list[Any] = []
    if start_date:
        conditions.append("o.order_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("o.order_date <= ?")
        params.append(end_date)
    if customer_id is not None:
        conditions.append("o.customer_id = ?")
        params.append(int(customer_id))

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT o.id AS order_id, o.order_no, o.order_date, o.order_type,
                   o.customer_id, c.name AS customer_name,
                   oi.id AS item_id, oi.product_id, oi.product_name, oi.spec,
                   oi.unit, oi.quantity, oi.subtotal_cents,
                   oi.unit_cost_micro, oi.cost_total_micro
            FROM orders o
            JOIN customers c ON c.id=o.customer_id
            JOIN order_items oi ON oi.order_id=o.id
            WHERE {' AND '.join(conditions)}
            ORDER BY o.order_date, o.id, oi.id
            """,
            params,
        ).fetchall()

    summary = _empty_summary()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        order_id = int(row["order_id"])
        order = grouped.setdefault(
            order_id,
            {
                "order_id": order_id,
                "order_no": row["order_no"],
                "order_date": row["order_date"],
                "order_type": row["order_type"],
                "customer_id": int(row["customer_id"]),
                "customer_name": row["customer_name"],
                "amount_cents": 0,
                "known_amount_cents": 0,
                "cost_micro": 0,
                "cost_complete": True,
                "unknown_line_count": 0,
                "item_count": 0,
            },
        )
        sign = -1 if row["order_type"] == "return" else 1
        amount_cents = int(row["subtotal_cents"] or 0)
        order["amount_cents"] += sign * amount_cents
        order["item_count"] += 1
        if row["cost_total_micro"] is None:
            order["cost_complete"] = False
            order["unknown_line_count"] += 1
        else:
            order["known_amount_cents"] += sign * amount_cents
            order["cost_micro"] += sign * int(row["cost_total_micro"])

    summary["orders"] = list(grouped.values())
    summary["order_count"] = len(summary["orders"])
    summary["sales_order_count"] = sum(item["order_type"] == "sale" for item in summary["orders"])
    summary["return_order_count"] = sum(item["order_type"] == "return" for item in summary["orders"])
    summary["sales_cents"] = sum(max(0, int(item["amount_cents"])) for item in summary["orders"] if item["order_type"] == "sale")
    summary["returns_cents"] = sum(abs(int(item["amount_cents"])) for item in summary["orders"] if item["order_type"] == "return")
    summary["net_sales_cents"] = sum(int(item["amount_cents"]) for item in summary["orders"])

    known_orders = [item for item in summary["orders"] if item["known_amount_cents"] or item["cost_complete"]]
    summary["unknown_order_count"] = sum(not item["cost_complete"] for item in summary["orders"])
    summary["unknown_line_count"] = sum(int(item["unknown_line_count"]) for item in summary["orders"])
    summary["cost_complete"] = summary["unknown_order_count"] == 0
    summary["known_sales_cents"] = sum(
        int(item["known_amount_cents"]) for item in known_orders if item["order_type"] == "sale"
    )
    summary["known_returns_cents"] = sum(
        abs(int(item["known_amount_cents"])) for item in known_orders if item["order_type"] == "return"
    )
    summary["known_net_sales_cents"] = sum(int(item["known_amount_cents"]) for item in known_orders)
    summary["net_cost_cents"] = _micro_to_cents(sum(int(item["cost_micro"]) for item in known_orders))
    summary["gross_profit_cents"] = summary["known_net_sales_cents"] - summary["net_cost_cents"]
    if summary["known_net_sales_cents"] > 0:
        summary["gross_margin_rate"] = summary["gross_profit_cents"] / summary["known_net_sales_cents"]
    return summary
