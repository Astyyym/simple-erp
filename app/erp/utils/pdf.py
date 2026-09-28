import re
from pathlib import Path
from urllib.parse import urlencode
from flask import render_template, request, send_file
from weasyprint import HTML

from erp.config import load_config, project_path, runtime_root
from erp.db import get_db
from erp.services.accounting import get_customer_account_ledger
from erp.utils.money import cents_to_yuan


def settings_print_preview_context() -> tuple[dict, list[dict], dict]:
    """Return the fixed sample data and current configuration used by Settings previews."""
    config = load_config()
    order = {
        "order_no": "MD202607120001",
        "order_date": "2026-07-12",
        "customer_name": "预览客户（示例）",
        "total_amount_cents": 15000,
        "notes": "",
    }
    items = [
        {
            "product_name": "示例闸阀",
            "spec": "DN50",
            "unit": "只",
            "quantity": "2",
            "unit_price_cents": 5000,
            "subtotal_cents": 10000,
        },
        {
            "product_name": "示例蝶阀",
            "spec": "DN80",
            "unit": "只",
            "quantity": "1",
            "unit_price_cents": 5000,
            "subtotal_cents": 5000,
        },
    ]
    return order, items, config


def generate_settings_print_preview_pdf() -> Path:
    """Render Settings' sample with the same order-print template as real orders."""
    order, items, config = settings_print_preview_context()
    html = render_template(
        "orders/print_template.html",
        order=order,
        items=items,
        config=config,
        cents_to_yuan=cents_to_yuan,
    )
    out = project_path("temp_pdf", "settings_print_preview.pdf")
    HTML(string=html, base_url=str(runtime_root())).write_pdf(out)
    return out


def send_pdf_for_preview(path: Path, filename: str, title: str):
    """Keep desktop PDFs in-session while exposing a native save action."""
    if request.args.get("desktop_preview") == "1":
        args = request.args.to_dict(flat=True)
        args.pop("desktop_preview", None)
        pdf_url = request.path + (f"?{urlencode(args)}" if args else "")
        return render_template(
            "desktop_pdf_preview.html",
            pdf_url=pdf_url,
            filename=filename,
            title=title,
        )
    return send_file(path, as_attachment=False, download_name=filename)


def generate_order_pdf(order_id: int) -> Path:
    with get_db() as conn:
        order = conn.execute("SELECT o.*, c.name AS customer_name, c.phone, c.address FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone()
        items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    if order is None:
        raise ValueError(f"订单不存在: {order_id}")
    config = load_config()
    html = render_template("orders/print_template.html", order=order, items=items, config=config, cents_to_yuan=cents_to_yuan)
    out = project_path("temp_pdf", f"order_{order_id}.pdf")
    HTML(string=html, base_url=str(runtime_root())).write_pdf(out)
    return out


def _account_summary_display_dates(rows, start_date: str, end_date: str) -> tuple[str, str]:
    """Return the concrete date range shown on the printable customer summary."""
    if not rows:
        return start_date, end_date
    row_dates = [row["order_date"] for row in rows]
    return start_date or min(row_dates), end_date or max(row_dates)


def _account_summary_table_rows(rows) -> list[dict]:
    """Add rowspan metadata so repeated dates/order numbers/totals print as merged cells."""
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
        row["is_return"] = row.get("order_type") == "return"
        row["show_date"] = order_date not in seen_dates
        row["date_rowspan"] = date_counts[order_date]
        row["show_order_no"] = order_no not in seen_orders
        row["order_no_rowspan"] = order_counts[order_no]
        row["show_order_total"] = order_no not in seen_orders
        row["order_total_rowspan"] = order_counts[order_no]
        seen_dates.add(order_date)
        seen_orders.add(order_no)
    return prepared


def _account_summary_totals(rows) -> dict[str, int]:
    """Summarize one total per order, then split sales/returns for the printed formula."""
    seen_orders: set[str] = set()
    sale_total = 0
    return_total = 0
    for row in rows:
        order_no = row["order_no"]
        if order_no in seen_orders:
            continue
        seen_orders.add(order_no)
        order_total = int(row.get("order_total_cents", row.get("subtotal_cents", 0)) or 0)
        if row.get("order_type") == "return":
            return_total += abs(order_total)
        else:
            sale_total += order_total
    return {
        "sale_total_cents": sale_total,
        "return_total_cents": return_total,
        "net_total_cents": sale_total - return_total,
    }


def generate_account_summary_pdf(customer_id: int, start_date: str = "", end_date: str = "") -> Path:
    return generate_account_summary_pdf_for_customers([int(customer_id)], start_date, end_date)


def _load_account_summary_section(conn, customer_id: int, start_date: str, end_date: str) -> dict | None:
    customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if customer is None:
        return None
    conditions = ["o.customer_id=?", "o.deleted_at IS NULL", "o.status IN ('saved','printed')"]
    params: list = [customer_id]
    if start_date:
        conditions.append("o.order_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("o.order_date <= ?")
        params.append(end_date)
    where_clause = " AND ".join(conditions)
    rows = conn.execute(
        f"""
        SELECT o.order_no, o.order_date, o.order_type, o.total_amount_cents AS order_total_cents,
               oi.product_name, oi.spec, oi.unit,
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
    prepared_rows = _account_summary_table_rows(rows)
    totals = _account_summary_totals(prepared_rows)
    display_start_date, display_end_date = _account_summary_display_dates(rows, start_date, end_date)
    return {
        "customer": customer,
        "rows": prepared_rows,
        "start_date": display_start_date,
        "end_date": display_end_date,
        "total_cents": int(total_row["total"] or 0),
        "totals": totals,
    }


def generate_account_summary_pdf_for_customers(customer_ids: list[int], start_date: str = "", end_date: str = "") -> Path:
    if not customer_ids:
        raise ValueError("没有可导出的客户")
    sections = []
    with get_db() as conn:
        for customer_id in customer_ids:
            section = _load_account_summary_section(conn, int(customer_id), start_date, end_date)
            if section is not None:
                sections.append(section)
    if not sections:
        raise ValueError("没有可导出的客户")
    config = load_config()
    html = render_template(
        "accounts/summary_pdf.html",
        sections=sections,
        config=config,
        cents_to_yuan=cents_to_yuan,
    )
    safe_range = f"{start_date or 'all'}_{end_date or 'all'}".replace("/", "-")
    ids_part = "-".join(str(i) for i in customer_ids[:8])
    if len(customer_ids) > 8:
        ids_part += f"_n{len(customer_ids)}"
    out = project_path("temp_pdf", f"account_summary_{ids_part}_{safe_range}.pdf")
    HTML(string=html, base_url=str(runtime_root())).write_pdf(out)
    return out


def resolve_summary_customer_ids(customer_q: str, start_date: str, end_date: str) -> list[int]:
    """Customers for summary export: unique match, or multi/all with orders in range."""
    customer_q = (customer_q or "").strip()
    with get_db() as conn:
        if customer_q:
            exact = conn.execute(
                "SELECT id FROM customers WHERE deleted_at IS NULL AND name=? LIMIT 1",
                (customer_q,),
            ).fetchone()
            if exact is not None:
                return [int(exact["id"])]
            fuzzy = conn.execute(
                "SELECT id, name FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name",
                (f"%{customer_q}%",),
            ).fetchall()
            if len(fuzzy) == 1:
                return [int(fuzzy[0]["id"])]
            candidate_ids = [int(row["id"]) for row in fuzzy]
            if not candidate_ids:
                return []
        else:
            candidate_ids = None

        conditions = ["o.deleted_at IS NULL", "o.status IN ('saved','printed')"]
        params: list = []
        if start_date:
            conditions.append("o.order_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("o.order_date <= ?")
            params.append(end_date)
        if candidate_ids is not None:
            placeholders = ",".join("?" for _ in candidate_ids)
            conditions.append(f"o.customer_id IN ({placeholders})")
            params.extend(candidate_ids)
        rows = conn.execute(
            f"""
            SELECT DISTINCT c.id
            FROM customers c
            JOIN orders o ON o.customer_id=c.id
            WHERE c.deleted_at IS NULL AND {' AND '.join(conditions)}
            ORDER BY c.name COLLATE NOCASE ASC, c.id ASC
            """,
            params,
        ).fetchall()
        return [int(row["id"]) for row in rows]


def generate_account_ledger_pdf(customer_id: int, start_date: str = "", end_date: str = "") -> Path:
    """Render the same current-balance and period-ledger model used by the account page."""
    ledger = get_customer_account_ledger(customer_id, start_date, end_date)
    config = load_config()
    html = render_template(
        "accounts/ledger_pdf.html",
        ledger=ledger,
        config=config,
        cents_to_yuan=cents_to_yuan,
    )
    safe_range = "_".join(
        re.sub(r"[^0-9A-Za-z_-]+", "-", value or "all")[:40]
        for value in (start_date, end_date)
    )
    out = project_path("temp_pdf", f"account_ledger_{int(customer_id)}_{safe_range}.pdf")
    HTML(string=html, base_url=str(runtime_root())).write_pdf(out)
    return out
