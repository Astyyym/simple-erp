from pathlib import Path
from flask import render_template
from weasyprint import HTML

from erp.config import load_config, project_path
from erp.db import get_db
from erp.utils.money import cents_to_yuan


def generate_order_pdf(order_id: int) -> Path:
    with get_db() as conn:
        order = conn.execute("SELECT o.*, c.name AS customer_name, c.phone, c.address FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone()
        items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    if order is None:
        raise ValueError(f"订单不存在: {order_id}")
    config = load_config()
    html = render_template("orders/print_template.html", order=order, items=items, config=config, cents_to_yuan=cents_to_yuan)
    out = project_path("temp_pdf", f"order_{order_id}.pdf")
    HTML(string=html, base_url=str(project_path())).write_pdf(out)
    return out


def _account_summary_display_dates(rows, start_date: str, end_date: str) -> tuple[str, str]:
    """Return the concrete date range shown on the printable customer summary."""
    if not rows:
        return start_date, end_date
    row_dates = [row["order_date"] for row in rows]
    return start_date or min(row_dates), end_date or max(row_dates)


def _account_summary_table_rows(rows) -> list[dict]:
    """Add rowspan metadata so repeated dates/order numbers print as merged cells."""
    prepared = [dict(row) for row in rows]
    date_counts: dict[str, int] = {}
    order_counts: dict[str, int] = {}
    for row in prepared:
        date_counts[row["order_date"]] = date_counts.get(row["order_date"], 0) + 1
        order_counts[row["order_no"]] = order_counts.get(row["order_no"], 0) + 1

    seen_dates: set[str] = set()
    seen_orders: set[str] = set()
    for row in prepared:
        order_date = row["order_date"]
        order_no = row["order_no"]
        row["show_date"] = order_date not in seen_dates
        row["date_rowspan"] = date_counts[order_date]
        row["show_order_no"] = order_no not in seen_orders
        row["order_no_rowspan"] = order_counts[order_no]
        seen_dates.add(order_date)
        seen_orders.add(order_no)
    return prepared


def generate_account_summary_pdf(customer_id: int, start_date: str = "", end_date: str = "") -> Path:
    conditions = ["o.customer_id=?", "o.deleted_at IS NULL", "o.status IN ('saved','printed')"]
    params = [customer_id]
    if start_date:
        conditions.append("o.order_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("o.order_date <= ?")
        params.append(end_date)
    where_clause = " AND ".join(conditions)
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        rows = conn.execute(
            f"""
            SELECT o.order_no, o.order_date, oi.product_name, oi.spec, oi.unit,
                   oi.quantity, oi.unit_price_cents, oi.subtotal_cents
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            WHERE {where_clause}
            ORDER BY o.order_date ASC, o.id ASC, oi.id ASC
            """,
            params,
        ).fetchall()
        total_row = conn.execute(
            f"SELECT COALESCE(SUM(o.total_amount_cents), 0) AS total FROM orders o WHERE {where_clause}",
            params,
        ).fetchone()
    if customer is None:
        raise ValueError(f"客户不存在: {customer_id}")
    config = load_config()
    total_cents = int(total_row["total"] or 0)
    display_start_date, display_end_date = _account_summary_display_dates(rows, start_date, end_date)
    html = render_template(
        "accounts/summary_pdf.html",
        customer=customer,
        rows=_account_summary_table_rows(rows),
        start_date=display_start_date,
        end_date=display_end_date,
        total_cents=total_cents,
        config=config,
        cents_to_yuan=cents_to_yuan,
    )
    safe_range = f"{start_date or 'all'}_{end_date or 'all'}".replace("/", "-")
    out = project_path("temp_pdf", f"account_summary_{customer_id}_{safe_range}.pdf")
    HTML(string=html, base_url=str(project_path())).write_pdf(out)
    return out
