from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any

from erp.db import get_db
from erp.services.inventory_status import project_inventory_status


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
        "spec": product["spec"] or "",
        "unit": product["unit"],
        "is_active": bool(product["is_active"]),
        "created_date": product["created_at"][:10],
        "sales_quantity_3dp": 0,
        "returns_quantity_3dp": 0,
        "net_sales_quantity_3dp": 0,
        "sales_amount_cents": 0,
        "returns_amount_cents": 0,
        "net_sales_amount_cents": 0,
        "net_cost_cents": 0,
        "net_cost_micro": 0,
        "gross_profit_cents": 0,
        "gross_margin_rate": None,
        "cost_complete": True,
        "unknown_line_count": 0,
        "known_line_count": 0,
        "sales_line_count": 0,
        "returns_line_count": 0,
        "known_net_sales_cents": 0,
        "unknown_net_sales_cents": 0,
        "inventory_enabled": bool(product["inventory_enabled"]),
        "current_quantity_3dp": product["quantity_3dp"] if product["inventory_enabled"] else None,
        "current_avg_cost_micro": product["avg_cost_micro"] if product["inventory_enabled"] else None,
        "current_inventory_cost_micro": product["cost_total_micro"] if product["inventory_enabled"] else None,
        "safety_stock_3dp": int(product["safety_stock_3dp"] or 0),
        "inventory_is_current": True,
        "inventory_status": "未启用",
    }


def _micro_to_cents(value: int) -> int:
    # Inventory cost is stored in millionths of a yuan; money is displayed in cents.
    return int((Decimal(value) / Decimal("10000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def summarize_analytics(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    customer_id: int | None = None,
    product_name: str = "",
    spec: str = "",
    metric: str = "amount",
    document_types: list[str] | None = None,
) -> dict[str, Any]:
    """Return one shared dataset for the Phase 5 heatmap and detail panels.

    The query uses order business dates and only effective sales/returns. Current
    inventory is joined separately so a historical date range never rewrites it.

    ``document_types`` filters which of the four document kinds participate in the
    flow figures. 利润类口径（净销售额/毛利润/产品排行）始终只算销售 + 退货：
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
    spec = (spec or "").strip()
    query_spec = "" if spec == "__unfilled__" else spec
    if spec and not product_name:
        raise ValueError("选择型号前必须先选择商品名称")

    with get_db() as conn:
        if customer_id not in (None, ""):
            if isinstance(customer_id, bool) or not re.fullmatch(r"[1-9][0-9]*", str(customer_id)):
                raise ValueError("往来对象 ID 无效")
            customer_id = int(customer_id)
            if customer_id > 9223372036854775807 or not conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
                raise ValueError("往来对象不存在")
        else:
            customer_id = None
        if spec and product_name and not conn.execute("SELECT 1 FROM products WHERE name=? AND COALESCE(spec,'')=?", (product_name, query_spec)).fetchone():
            raise ValueError("所选型号不存在")
        product_conditions = ["p.deleted_at IS NULL"]
        product_params: list[Any] = []
        if product_name:
            product_conditions.append("p.name = ?")
            product_params.append(product_name)
        if spec and product_name:
            product_conditions.append("COALESCE(p.spec, '') = ?")
            product_params.append(query_spec)
        products = conn.execute(
            f"""
            SELECT p.id, p.name, p.spec, p.unit, p.safety_stock_3dp, p.is_active, p.created_at,
                   COALESCE(s.enabled, 0) AS inventory_enabled,
                   s.quantity_3dp, s.avg_cost_micro, s.cost_total_micro
            FROM products p
            LEFT JOIN product_inventory_state s ON s.product_id=p.id
            WHERE {' AND '.join(product_conditions)}
            ORDER BY p.name COLLATE NOCASE, COALESCE(p.spec, ''), p.id
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
            SELECT o.order_date, o.order_type, oi.product_id, oi.quantity,
                   oi.subtotal_cents, oi.cost_total_micro
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            WHERE {' AND '.join(conditions)}
            ORDER BY o.order_date, o.id, oi.id
            """,
            params,
        ).fetchall()

        # 流水净额：四类单据的业务金额按方向相抵（销售+/退货−/拿货−/退拿货+）。
        # 单据级口径，因此不套用商品/型号筛选，只跟随日期、往来对象与订单类型。
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

        # 单据流水：拿货/退拿货金额与单据数（利润口径之外的补充信息）。
        flow_totals = {"purchase_amount_cents": 0, "purchase_return_amount_cents": 0,
                       "purchase_count": 0, "purchase_return_count": 0}
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
    heatmap = defaultdict(lambda: {"sales_quantity_3dp": 0, "returns_quantity_3dp": 0, "sales_amount_cents": 0, "returns_amount_cents": 0, "net_cost_micro": 0, "has_transactions": False, "known_line_count": 0, "unknown_line_count": 0, "known_net_sales_cents": 0, "unknown_net_sales_cents": 0})
    known_sales_cents = 0
    known_returns_cents = 0
    known_net_cost_micro = 0
    unknown_line_count = 0
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
        for projection in (target, heatmap[row["order_date"]]):
            if row["cost_total_micro"] is None:
                projection["unknown_net_sales_cents"] += sign * amount_cents
            else:
                projection["known_line_count"] += 1
                projection["known_net_sales_cents"] += sign * amount_cents
        if is_return:
            target["returns_quantity_3dp"] += quantity_3dp
            target["returns_amount_cents"] += amount_cents
            heatmap[row["order_date"]]["returns_quantity_3dp"] += quantity_3dp
            heatmap[row["order_date"]]["returns_amount_cents"] += amount_cents
            known_returns_cents += amount_cents if row["cost_total_micro"] is not None else 0
        else:
            target["sales_quantity_3dp"] += quantity_3dp
            target["sales_amount_cents"] += amount_cents
            heatmap[row["order_date"]]["sales_quantity_3dp"] += quantity_3dp
            heatmap[row["order_date"]]["sales_amount_cents"] += amount_cents
            known_sales_cents += amount_cents if row["cost_total_micro"] is not None else 0
        target["net_sales_quantity_3dp"] += sign * quantity_3dp
        target["net_sales_amount_cents"] += sign * amount_cents
        if row["cost_total_micro"] is None:
            target["cost_complete"] = False
            target["unknown_line_count"] += 1
            unknown_line_count += 1
            heatmap[row["order_date"]]["unknown_line_count"] += 1
        else:
            target["net_cost_micro"] += sign * int(row["cost_total_micro"])
            heatmap[row["order_date"]]["net_cost_micro"] += sign * int(row["cost_total_micro"])
            known_net_cost_micro += sign * int(row["cost_total_micro"])

    for target in by_product.values():
        target["net_cost_cents"] = _micro_to_cents(target["net_cost_micro"])
        if target["cost_complete"]:
            target["gross_profit_cents"] = _micro_to_cents(target["net_sales_amount_cents"] * 10000 - target["net_cost_micro"])
        else:
            target["gross_profit_cents"] = None
        if target["net_sales_amount_cents"] > 0 and target["cost_complete"]:
            target["gross_margin_rate"] = Decimal(target["net_sales_amount_cents"] * 10000 - target["net_cost_micro"]) / Decimal(target["net_sales_amount_cents"] * 10000)
        target.update(project_inventory_status(
            enabled=target["inventory_enabled"], quantity_3dp=target["current_quantity_3dp"],
            safety_stock_3dp=target["safety_stock_3dp"],
        ))

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
                "net_cost_micro": values["net_cost_micro"],
                "net_cost_cents": _micro_to_cents(values["net_cost_micro"]),
                "has_transactions": values["has_transactions"] or day in flow_by_day,
                "known_line_count": values["known_line_count"],
                "unknown_line_count": values["unknown_line_count"],
                "cost_complete": values["unknown_line_count"] == 0,
                "known_net_sales_cents": values["known_net_sales_cents"],
                "unknown_net_sales_cents": values["unknown_net_sales_cents"],
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
    net_cost_cents = _micro_to_cents(known_net_cost_micro)
    known_gross_profit_cents = _micro_to_cents((known_sales_cents - known_returns_cents) * 10000 - known_net_cost_micro)
    summary = {
        "sales_quantity_3dp": sales_quantity,
        "returns_quantity_3dp": returns_quantity,
        "net_sales_quantity_3dp": sales_quantity - returns_quantity if quantity_comparable else None,
        "quantity_comparable": quantity_comparable,
        "quantity_unit": quantity_unit,
        "sales_amount_cents": sum(row["sales_amount_cents"] for row in product_rows),
        "returns_amount_cents": sum(row["returns_amount_cents"] for row in product_rows),
        "net_sales_amount_cents": net_sales_amount,
        "known_sales_cents": known_sales_cents,
        "known_returns_cents": known_returns_cents,
        "net_cost_cents": net_cost_cents,
        "gross_profit_cents": known_gross_profit_cents if unknown_line_count == 0 else None,
        "known_gross_profit_cents": known_gross_profit_cents,
        "gross_margin_rate": None,
        "cost_complete": unknown_line_count == 0,
        "unknown_line_count": unknown_line_count,
        "known_line_count": len(rows) - unknown_line_count,
        "known_net_sales_cents": known_sales_cents - known_returns_cents,
        "unknown_net_sales_cents": net_sales_amount - known_sales_cents + known_returns_cents,
        "net_cost_micro": known_net_cost_micro,
        "known_gross_margin_rate": None,
        "inventory_is_current": True,
    }
    known_net_sales = known_sales_cents - known_returns_cents
    if known_net_sales > 0:
        summary["known_gross_margin_rate"] = Decimal(known_net_sales * 10000 - known_net_cost_micro) / Decimal(known_net_sales * 10000)
        if summary["cost_complete"]:
            summary["gross_margin_rate"] = summary["known_gross_margin_rate"]
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
        "gross_profit": sorted(
            [row for row in product_rows if row["cost_complete"]],
            key=lambda row: (-row["gross_profit_cents"], row["product_id"]),
        ),
        "inventory_alert": [
            row for row in sorted(
                product_rows,
                key=lambda row: (
                    {"缺货": 0, "库存告急": 1, "正常": 2, "未知": 3, "未启用": 4}[row["inventory_status"]],
                    row["product_id"],
                ),
            )
            if row["inventory_status"] in {"缺货", "库存告急"}
        ],
    }
    ranking_groups = {key: [{"unit": None, "rows": ranked_rows}] for key, ranked_rows in rankings.items()}
    return {
        "filters": {
            "start_date": start_date or "",
            "end_date": end_date or "",
            "customer_id": int(customer_id) if customer_id is not None else None,
            "product_name": product_name,
            "spec": spec,
            "metric": metric,
            "document_types": sorted(document_filter),
        },
        "summary": summary,
        "heatmap": heatmap_rows,
        "product_rows": product_rows,
        "flow_totals": flow_totals,
        "rankings": rankings,
        "ranking_groups": ranking_groups,
        "profit_excluded_products": sum(not row["cost_complete"] for row in product_rows),
    }
