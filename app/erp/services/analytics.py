"""销售/退货数据分析：月历热力图、单据流水与商品排行。

2026-10-16（决策 ②/③）：成本/毛利润/库存口径全部下线——库存系统与成本快照列已删除，
本模块只保留**数量与金额**口径（净销售额、单据流水净额、数量/金额排行）。
原先的 `net_cost_*` / `gross_profit_*` / `gross_margin_rate` / `cost_complete` /
`inventory_*` / `safety_stock_3dp` 字段与 `profit_excluded_products` 计数一并移除。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any

from erp.db import get_db


def _parse_date(value: str | None, label: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{label}格式无效") from None


def _date_range(start_date: str | None, end_date: str | None, observed_dates: list[str]) -> list[str]:
    if not start_date or not end_date:
        if not observed_dates:
            return []
        start_date = start_date or min(observed_dates)
        end_date = end_date or max(observed_dates)
    assert start_date is not None and end_date is not None
    begin = date.fromisoformat(start_date)
    finish = date.fromisoformat(end_date)
    return [(begin + timedelta(days=offset)).isoformat() for offset in range((finish - begin).days + 1)]


def _empty_product(product: Any) -> dict[str, Any]:
    return {
        "product_id": int(product["id"]),
        "product_name": product["name"],
        "unit": product["unit"],
        "is_active": bool(product["is_active"]),
        "created_date": product["created_at"][:10],
        "sales_quantity_3dp": 0,
        "returns_quantity_3dp": 0,
        "net_sales_quantity_3dp": 0,
        "sales_amount_cents": 0,
        "returns_amount_cents": 0,
        "net_sales_amount_cents": 0,
        "sales_line_count": 0,
        "returns_line_count": 0,
    }


def summarize_analytics(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    customer_id: int | None = None,
    product_name: str = "",
    metric: str = "amount",
    document_types: list[str] | None = None,
) -> dict[str, Any]:
    """Return one shared dataset for the heatmap and detail panels.

    The query uses order business dates and only effective sales/returns.

    ``document_types`` filters which of the four document kinds participate in the
    flow figures. 数量/金额口径（净销售额、商品排行）始终只算销售 + 退货：
    picking 拿货/退拿货 only affects the flow heatmap and the 单据流水 card.
    """
    start_date = _parse_date(start_date, "起始日期")
    end_date = _parse_date(end_date, "结束日期")
    if start_date and end_date and start_date > end_date:
        raise ValueError("起始日期不能晚于结束日期")
    metric = (metric or "amount").strip()
    if metric not in {"quantity", "amount"}:
        raise ValueError("分析指标不合法")
    _VALID_DOCUMENT_TYPES = {"sale", "return", "purchase", "purchase_return"}
    document_filter = {str(item).strip() for item in (document_types or []) if str(item).strip()}
    if unknown := document_filter - _VALID_DOCUMENT_TYPES:
        raise ValueError("订单类型不合法")
    product_name = (product_name or "").strip()

    with get_db() as conn:
        if customer_id not in (None, ""):
            if isinstance(customer_id, bool) or not re.fullmatch(r"[1-9][0-9]*", str(customer_id)):
                raise ValueError("往来对象 ID 无效")
            customer_id = int(customer_id)
            if customer_id > 9223372036854775807 or not conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
                raise ValueError("往来对象不存在")
        else:
            customer_id = None
        product_conditions = ["p.deleted_at IS NULL"]
        product_params: list[Any] = []
        if product_name:
            product_conditions.append("p.name = ?")
            product_params.append(product_name)
        products = conn.execute(
            f"""
            SELECT p.id, p.name, p.unit, p.is_active, p.created_at
            FROM products p
            WHERE {' AND '.join(product_conditions)}
            ORDER BY p.name COLLATE NOCASE, p.id
            """,
            product_params,
        ).fetchall()
        product_ids = [int(row["id"]) for row in products]

        conditions = [
            "o.deleted_at IS NULL",
            "o.status IN ('saved', 'printed')",
            "o.order_type IN ('sale', 'return')",
            "oi.product_id IS NOT NULL",
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
        if product_ids:
            placeholders = ",".join("?" for _ in product_ids)
            conditions.append(f"oi.product_id IN ({placeholders})")
            params.extend(product_ids)
        else:
            # No active product can satisfy the selected product filter.
            conditions.append("1 = 0")
        rows = conn.execute(
            f"""
            SELECT o.order_date, o.order_type, oi.product_id, oi.quantity, oi.subtotal_cents
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            WHERE {' AND '.join(conditions)}
            ORDER BY o.order_date, o.id, oi.id
            """,
            params,
        ).fetchall()

        # 流水净额：四类单据的业务金额按方向相抵（销售+/退货−/拿货−/退拿货+）。
        # 单据级口径，因此不套用商品筛选，只跟随日期、往来对象与订单类型。
        def flow_conditions_for(date_column: str) -> tuple[str, list[Any]]:
            conditions = ["deleted_at IS NULL", "status IN ('saved', 'printed')"]
            params: list[Any] = []
            if start_date:
                conditions.append(f"{date_column} >= ?")
                params.append(start_date)
            if end_date:
                conditions.append(f"{date_column} <= ?")
                params.append(end_date)
            if customer_id is not None:
                conditions.append("customer_id = ?")
                params.append(int(customer_id))
            return " AND ".join(conditions), params

        def type_allowed(document_type: str) -> bool:
            """订单类型多选：未选=全部；四类分别为 sale/return/purchase/purchase_return。"""
            return not document_filter or document_type in document_filter

        order_flow_where, order_flow_params = flow_conditions_for("order_date")
        purchase_flow_where, purchase_flow_params = flow_conditions_for("business_date")
        flow_parts = []
        if type_allowed("sale"):
            flow_parts.append(
                f"SELECT order_date AS flow_date, 1 AS sign, total_amount_cents FROM orders "
                f"WHERE {order_flow_where} AND order_type='sale'"
            )
        if type_allowed("return"):
            flow_parts.append(
                f"SELECT order_date AS flow_date, -1 AS sign, total_amount_cents FROM orders "
                f"WHERE {order_flow_where} AND order_type='return'"
            )
        if type_allowed("purchase"):
            flow_parts.append(
                f"SELECT business_date AS flow_date, -1 AS sign, total_amount_cents FROM purchase_orders "
                f"WHERE {purchase_flow_where}"
            )
        if type_allowed("purchase_return"):
            flow_parts.append(
                f"SELECT business_date AS flow_date, 1 AS sign, total_amount_cents FROM purchase_return_orders "
                f"WHERE {purchase_flow_where}"
            )
        flow_params: list[Any] = []
        for part in flow_parts:
            flow_params.extend(order_flow_params if "order_date AS" in part else purchase_flow_params)
        flow_rows = conn.execute("\n UNION ALL \n".join(flow_parts), flow_params).fetchall() if flow_parts else []

        # 单据流水：四类单据的金额与单据数。张数一律走**单据级**口径
        # （跟随日期 / 往来对象 / 订单类型，排除已作废与回收站），与 flow_rows 的
        # 净额算法同源，故卡片上的「N 张」与「流水净额」永远自洽。
        flow_totals = {"purchase_amount_cents": 0, "purchase_return_amount_cents": 0,
                       "sale_count": 0, "return_count": 0,
                       "purchase_count": 0, "purchase_return_count": 0}
        if type_allowed("sale"):
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM orders WHERE {order_flow_where} AND order_type='sale'",
                order_flow_params,
            ).fetchone()
            flow_totals["sale_count"] = int(row["c"])
        if type_allowed("return"):
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM orders WHERE {order_flow_where} AND order_type='return'",
                order_flow_params,
            ).fetchone()
            flow_totals["return_count"] = int(row["c"])
        if type_allowed("purchase"):
            row = conn.execute(
                f"SELECT COUNT(*) AS c, COALESCE(SUM(total_amount_cents),0) AS t FROM purchase_orders WHERE {purchase_flow_where}",
                purchase_flow_params,
            ).fetchone()
            flow_totals["purchase_count"] = int(row["c"])
            flow_totals["purchase_amount_cents"] = int(row["t"])
        if type_allowed("purchase_return"):
            row = conn.execute(
                f"SELECT COUNT(*) AS c, COALESCE(SUM(total_amount_cents),0) AS t FROM purchase_return_orders WHERE {purchase_flow_where}",
                purchase_flow_params,
            ).fetchone()
            flow_totals["purchase_return_count"] = int(row["c"])
            flow_totals["purchase_return_amount_cents"] = int(row["t"])
        flow_totals["net_flow_cents"] = sum(
            int(r["sign"]) * int(r["total_amount_cents"] or 0) for r in flow_rows
        )

    by_product = {int(row["id"]): _empty_product(row) for row in products}
    heatmap = defaultdict(lambda: {"sales_quantity_3dp": 0, "returns_quantity_3dp": 0, "sales_amount_cents": 0, "returns_amount_cents": 0, "has_transactions": False})
    for row in rows:
        product_id = int(row["product_id"])
        target = by_product[product_id]
        heatmap[row["order_date"]]["has_transactions"] = True
        quantity_3dp = int(
            (Decimal(str(row["quantity"])) * Decimal("1000")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        amount_cents = int(row["subtotal_cents"] or 0)
        is_return = row["order_type"] == "return"
        sign = -1 if is_return else 1
        target["returns_line_count" if is_return else "sales_line_count"] += 1
        if is_return:
            target["returns_quantity_3dp"] += quantity_3dp
            target["returns_amount_cents"] += amount_cents
            heatmap[row["order_date"]]["returns_quantity_3dp"] += quantity_3dp
            heatmap[row["order_date"]]["returns_amount_cents"] += amount_cents
        else:
            target["sales_quantity_3dp"] += quantity_3dp
            target["sales_amount_cents"] += amount_cents
            heatmap[row["order_date"]]["sales_quantity_3dp"] += quantity_3dp
            heatmap[row["order_date"]]["sales_amount_cents"] += amount_cents
        target["net_sales_quantity_3dp"] += sign * quantity_3dp
        target["net_sales_amount_cents"] += sign * amount_cents

    observed_dates = [row["order_date"] for row in rows]
    days = _date_range(start_date, end_date, observed_dates)
    flow_by_day: dict[str, int] = defaultdict(int)
    for row in flow_rows:
        flow_by_day[row["flow_date"]] += int(row["sign"]) * int(row["total_amount_cents"] or 0)
    heatmap_rows = []
    for day in days:
        values = heatmap[day]
        heatmap_rows.append(
            {
                "date": day,
                "has_transactions": values["has_transactions"] or day in flow_by_day,
                "sales_quantity_3dp": values["sales_quantity_3dp"],
                "returns_quantity_3dp": values["returns_quantity_3dp"],
                "net_quantity_3dp": values["sales_quantity_3dp"] - values["returns_quantity_3dp"],
                "sales_amount_cents": values["sales_amount_cents"],
                "returns_amount_cents": values["returns_amount_cents"],
                "net_amount_cents": values["sales_amount_cents"] - values["returns_amount_cents"],
                "flow_net_cents": flow_by_day.get(day, 0),
            }
        )

    product_rows = sorted(
        by_product.values(),
        key=lambda row: (-int(row["net_sales_amount_cents"]), int(row["product_id"])),
    )
    units = {row["unit"] for row in product_rows}
    quantity_comparable = len(units) <= 1
    quantity_unit = next(iter(units), None) if quantity_comparable else None
    if not quantity_comparable:
        metric = "amount"
        for cell in heatmap_rows:
            cell["net_quantity_3dp"] = None
    metric_values = []
    for cell in heatmap_rows:
        cell["metric_value"] = (
            cell["flow_net_cents"]
            if metric == "amount"
            else cell["net_quantity_3dp"]
        )
        if cell["metric_value"] is not None:
            metric_values.append(abs(int(cell["metric_value"])))
    metric_max = max(metric_values, default=0)
    for cell in heatmap_rows:
        value = cell["metric_value"]
        cell["intensity"] = (
            min(4, (abs(int(value)) * 4 + metric_max - 1) // metric_max)
            if value is not None and metric_max
            else 0
        )
    sales_quantity = sum(row["sales_quantity_3dp"] for row in product_rows)
    returns_quantity = sum(row["returns_quantity_3dp"] for row in product_rows)
    net_sales_amount = sum(row["net_sales_amount_cents"] for row in product_rows)
    summary = {
        "sales_quantity_3dp": sales_quantity,
        "returns_quantity_3dp": returns_quantity,
        "net_sales_quantity_3dp": sales_quantity - returns_quantity if quantity_comparable else None,
        "quantity_comparable": quantity_comparable,
        "quantity_unit": quantity_unit,
        "sales_amount_cents": sum(row["sales_amount_cents"] for row in product_rows),
        "returns_amount_cents": sum(row["returns_amount_cents"] for row in product_rows),
        "net_sales_amount_cents": net_sales_amount,
    }
    cutoff = end_date or date.today().isoformat()
    rankings = {
        # 数量榜只按数量排序，全品类同一张榜，不按单位分组；单位随数值一起显示。
        "hot_sales": sorted(
            [row for row in product_rows if row["net_sales_quantity_3dp"] > 0],
            key=lambda row: (-row["net_sales_quantity_3dp"], row["product_id"]),
        ),
        "low_sales": sorted(
            [row for row in product_rows if row["sales_quantity_3dp"] > 0 and row["net_sales_quantity_3dp"] > 0],
            key=lambda row: (row["net_sales_quantity_3dp"], row["product_id"]),
        ),
        "net_returns": sorted(
            [row for row in product_rows if row["returns_line_count"] and row["net_sales_quantity_3dp"] <= 0],
            key=lambda row: (row["net_sales_quantity_3dp"], row["product_id"]),
        ),
        "unsold": [
            row for row in product_rows
            if row["sales_line_count"] == 0 and row["returns_line_count"] == 0
            and row["is_active"] and row["created_date"] <= cutoff
        ],
    }
    ranking_groups = {key: [{"unit": None, "rows": ranked_rows}] for key, ranked_rows in rankings.items()}
    return {
        "filters": {
            "start_date": start_date or "",
            "end_date": end_date or "",
            "customer_id": int(customer_id) if customer_id is not None else None,
            "product_name": product_name,
            "metric": metric,
            "document_types": sorted(document_filter),
        },
        "summary": summary,
        "heatmap": heatmap_rows,
        "product_rows": product_rows,
        "flow_totals": flow_totals,
        "rankings": rankings,
        "ranking_groups": ranking_groups,
    }
