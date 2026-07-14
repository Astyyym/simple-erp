from flask import Blueprint, render_template, request, redirect, url_for, send_file, jsonify, Response
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from erp.db import get_db
from erp.services.accounting import create_order_from_typed_rows, update_order_from_typed_rows, mark_order_printed, void_order
from erp.utils.exporting import (
    export_filename,
    order_line_export_headers,
    order_line_export_rows,
    workbook_download,
)
from erp.utils.money import cents_to_yuan
from erp.utils.pdf import generate_order_pdf

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")

def next_order_no(conn, order_date: str) -> str:
    """Generate MD + yyyymmdd + 4-digit daily sequence. Sale/return share the same sequence."""
    day = order_date.replace("-", "")
    if len(day) != 8 or not day.isdigit():
        day = date.today().strftime("%Y%m%d")
    prefix = f"MD{day}"
    rows = conn.execute(
        "SELECT order_no FROM orders WHERE order_no LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    max_seq = 0
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{4}})$")
    for row in rows:
        match = pattern.fullmatch(row["order_no"])
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return f"{prefix}{max_seq + 1:04d}"


def bump_order_no_if_exists(conn, order_no: str) -> str:
    """Keep for rare collision recovery; prefer next_order_no for new documents."""
    if conn.execute("SELECT 1 FROM orders WHERE order_no=?", (order_no,)).fetchone() is None:
        return order_no
    match = re.fullmatch(r"(MD\d{8})(\d{4})", order_no)
    if match:
        prefix, digits = match.groups()
        width = 4
        start = int(digits) + 1
        base = prefix
    else:
        match_old = re.fullmatch(r"(.+?)(\d+)$", order_no)
        if not match_old:
            base = f"{order_no}-"
            width = 4
            start = 1
        else:
            base, digits = match_old.groups()
            width = max(len(digits), 4)
            start = int(digits) + 1
    seq = start
    while True:
        candidate = f"{base}{seq:0{width}d}"
        if conn.execute("SELECT 1 FROM orders WHERE order_no=?", (candidate,)).fetchone() is None:
            return candidate
        seq += 1


def _safe_quantity(value) -> float:
    try:
        return float(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError, TypeError):
        return 0.0


def _valid_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _parse_year(value: str, default: int | None = None) -> int:
    try:
        year = int(str(value).strip())
        if 1 <= year <= 9999:
            return year
    except (TypeError, ValueError):
        pass
    return default if default is not None else date.today().year


def _parse_month(value: str, default: int = 1) -> int:
    try:
        month = int(str(value).strip())
        if 1 <= month <= 12:
            return month
    except (TypeError, ValueError):
        pass
    return default


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _resolve_date_range(
    date_mode: str,
    year_raw: str,
    month_raw: str,
    start_date: str,
    end_date: str,
    fallback_start: str | None,
    fallback_end: str | None,
    start_year_raw: str = "",
    start_month_raw: str = "",
    end_year_raw: str = "",
    end_month_raw: str = "",
):
    today = date.today()
    mode = date_mode if date_mode in {"year", "month", "range"} else "range"
    year = _parse_year(year_raw, today.year)
    month = _parse_month(month_raw, today.month)
    start_year = _parse_year(start_year_raw or year_raw, today.year)
    start_month = _parse_month(start_month_raw or month_raw, today.month)
    end_year = _parse_year(end_year_raw or year_raw, start_year)
    end_month = _parse_month(end_month_raw or month_raw, start_month)

    if mode == "year":
        start_day = date(year, 1, 1)
        end_day = date(year, 12, 31)
    elif mode == "month":
        start_day = date(start_year, start_month, 1)
        end_day = _month_end(end_year, end_month)
        if start_day > end_day:
            start_day, end_day = date(end_year, end_month, 1), _month_end(start_year, start_month)
            start_year, end_year = end_year, start_year
            start_month, end_month = end_month, start_month
        year = start_year
        month = start_month
    else:
        parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date() if _valid_iso_date(start_date) else None
        parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date() if _valid_iso_date(end_date) else None
        if parsed_start is None and fallback_start and _valid_iso_date(fallback_start):
            parsed_start = datetime.strptime(fallback_start, "%Y-%m-%d").date()
        if parsed_end is None and fallback_end and _valid_iso_date(fallback_end):
            parsed_end = datetime.strptime(fallback_end, "%Y-%m-%d").date()
        if parsed_start is None and parsed_end is None:
            start_day = today
            end_day = today
        elif parsed_start is None:
            start_day = parsed_end if parsed_end is not None else today
            end_day = start_day
        elif parsed_end is None:
            start_day = parsed_start
            end_day = start_day
        else:
            start_day = parsed_start
            end_day = parsed_end
        if start_day > end_day:
            start_day, end_day = end_day, start_day
        # Guard absurd ranges while still allowing a full natural year.
        max_span_days = 366
        if (end_day - start_day).days >= max_span_days:
            start_day = end_day - timedelta(days=max_span_days - 1)

    return (
        mode,
        year,
        month,
        start_day.isoformat(),
        end_day.isoformat(),
        start_day,
        end_day,
        start_year,
        start_month,
        end_year,
        end_month,
    )


def _resolve_customer_scope(conn, customer_q: str):
    """Return (scope, customer_row|None, notice|None, matched_names)."""
    if not customer_q:
        return "all", None, None, []

    exact = conn.execute(
        "SELECT id, name FROM customers WHERE deleted_at IS NULL AND name=? LIMIT 1",
        (customer_q,),
    ).fetchone()
    if exact is not None:
        return "customer", exact, None, [exact["name"]]

    fuzzy = conn.execute(
        "SELECT id, name FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name LIMIT 20",
        (f"%{customer_q}%",),
    ).fetchall()
    names = [row["name"] for row in fuzzy]
    if len(fuzzy) == 1:
        return "customer", fuzzy[0], None, names
    if len(fuzzy) > 1:
        return "none", None, "匹配到多个客户，请把客户名称再写精确一些后再筛选。当前不显示采购统计。", names
    return "none", None, "未匹配到客户，请确认名称或清空客户后查看全店统计。", []


def _purchase_dashboard(conn, scope: str, customer_row, start_date: str, end_date: str, start_day: date, end_day: date, date_mode: str, year: int, month: int, order_type: str, start_year: int, start_month: int, end_year: int, end_month: int):
    params = [start_date, end_date]
    customer_sql = ""
    if scope == "customer":
        customer_sql = " AND o.customer_id=?"
        params.append(customer_row["id"])

    rows = conn.execute(
        f"""SELECT o.id AS order_id, o.order_date, o.order_type,
                  oi.product_name, oi.quantity, oi.subtotal_cents
           FROM orders o
           JOIN order_items oi ON oi.order_id=o.id
           WHERE o.deleted_at IS NULL
             AND o.order_date>=? AND o.order_date<=?
             AND o.order_type='sale'
             AND o.status IN ('saved', 'printed')
             {customer_sql}
           ORDER BY o.order_date, o.id, oi.id""",
        params,
    ).fetchall()

    daily = {}
    ranking = {}
    for row in rows:
        day = daily.setdefault(row["order_date"], {"amount_cents": 0, "orders": set(), "products": []})
        day["amount_cents"] += int(row["subtotal_cents"] or 0)
        day["orders"].add(int(row["order_id"]))
        if row["product_name"] not in day["products"]:
            day["products"].append(row["product_name"])
        product = ranking.setdefault(row["product_name"], {"quantity": 0.0, "amount_cents": 0})
        product["quantity"] += _safe_quantity(row["quantity"])
        product["amount_cents"] += int(row["subtotal_cents"] or 0)

    days = []
    cursor = start_day
    last = end_day
    while cursor <= last:
        key = cursor.isoformat()
        item = daily.get(key)
        days.append({
            "date": key,
            "amount_cents": item["amount_cents"] if item else 0,
            "order_count": len(item["orders"]) if item else 0,
            "products": item["products"] if item else [],
        })
        if cursor == last:
            break
        cursor += timedelta(days=1)

    ranked = sorted(ranking.items(), key=lambda item: (-item[1]["quantity"], -item[1]["amount_cents"], item[0]))
    return {
        "scope": scope,
        "customer": customer_row["name"] if customer_row is not None else "",
        "start_date": start_date,
        "end_date": end_date,
        "date_mode": date_mode,
        "year": year,
        "month": month,
        "start_year": start_year,
        "start_month": start_month,
        "end_year": end_year,
        "end_month": end_month,
        "order_type": order_type,
        "days": days,
        "ranking": [
            {"name": name, "quantity": values["quantity"], "amount_cents": values["amount_cents"]}
            for name, values in ranked[:10]
        ],
    }


def _filter_args_from_request():
    return {
        "customer_q": request.args.get("customer", "").strip(),
        "date_mode_raw": request.args.get("date_mode", "range").strip(),
        "year_raw": request.args.get("year", "").strip(),
        "month_raw": request.args.get("month", "").strip(),
        "start_year_raw": request.args.get("start_year", "").strip(),
        "start_month_raw": request.args.get("start_month", "").strip(),
        "end_year_raw": request.args.get("end_year", "").strip(),
        "end_month_raw": request.args.get("end_month", "").strip(),
        "start_date_raw": request.args.get("start_date", "").strip(),
        "end_date_raw": request.args.get("end_date", "").strip(),
        "order_type": request.args.get("order_type", "").strip(),
    }


@orders_bp.get("/")
def list_orders():
    args = _filter_args_from_request()
    customer_q = args["customer_q"]
    order_type = args["order_type"] if args["order_type"] in {"sale", "return"} else ""

    with get_db() as conn:
        scope, customer_row, dashboard_notice, matched_names = _resolve_customer_scope(conn, customer_q)
        if scope == "customer":
            bounds = conn.execute(
                """SELECT MIN(order_date) AS first_date, MAX(order_date) AS last_date
                   FROM orders WHERE customer_id=? AND deleted_at IS NULL""",
                (customer_row["id"],),
            ).fetchone()
        else:
            bounds = conn.execute(
                """SELECT MIN(order_date) AS first_date, MAX(order_date) AS last_date
                   FROM orders WHERE deleted_at IS NULL"""
            ).fetchone()

        (
            date_mode,
            year,
            month,
            start_date,
            end_date,
            start_day,
            end_day,
            start_year,
            start_month,
            end_year,
            end_month,
        ) = _resolve_date_range(
            args["date_mode_raw"],
            args["year_raw"],
            args["month_raw"],
            args["start_date_raw"],
            args["end_date_raw"],
            bounds["first_date"] if bounds else None,
            bounds["last_date"] if bounds else None,
            args["start_year_raw"],
            args["start_month_raw"],
            args["end_year_raw"],
            args["end_month_raw"],
        )

        conditions = ["o.deleted_at IS NULL"]
        params = []
        if customer_q:
            conditions.append("c.name LIKE ?")
            params.append(f"%{customer_q}%")
        conditions.append("o.order_date >= ?")
        params.append(start_date)
        conditions.append("o.order_date <= ?")
        params.append(end_date)
        if order_type:
            conditions.append("o.order_type = ?")
            params.append(order_type)
        where_clause = " AND ".join(conditions)
        orders = conn.execute(
            f"SELECT o.*, c.name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE {where_clause} ORDER BY o.id DESC LIMIT 200",
            params,
        ).fetchall()

        purchase_dashboard = None
        if scope in {"customer", "all"}:
            purchase_dashboard = _purchase_dashboard(
                conn,
                scope,
                customer_row,
                start_date,
                end_date,
                start_day,
                end_day,
                date_mode,
                year,
                month,
                order_type,
                start_year,
                start_month,
                end_year,
                end_month,
            )

        customer_suggestions = conn.execute(
            "SELECT DISTINCT name FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name LIMIT 20",
            (f"%{customer_q}%" if customer_q else "%",),
        ).fetchall()

    unique_customer = scope == "customer"
    summary_hint = None
    if not unique_customer:
        summary_hint = "当前未锁定唯一客户：导出汇总表将按筛选范围内有单据的客户分段打印（销售单与退货单都会进入汇总，与订单类型筛选无关）。"

    return render_template(
        "orders/list.html",
        orders=orders,
        customer_q=customer_q,
        start_date=start_date if date_mode == "range" else args["start_date_raw"],
        end_date=end_date if date_mode == "range" else args["end_date_raw"],
        filter_start_date=start_date,
        filter_end_date=end_date,
        order_type=order_type,
        date_mode=date_mode,
        filter_year=year,
        filter_month=month,
        filter_start_year=start_year,
        filter_start_month=start_month,
        filter_end_year=end_year,
        filter_end_month=end_month,
        purchase_dashboard=purchase_dashboard,
        dashboard_notice=dashboard_notice,
        matched_customer_names=matched_names,
        customer_suggestions=customer_suggestions,
        unique_customer=unique_customer,
        summary_hint=summary_hint,
        cents_to_yuan=cents_to_yuan,
    )


@orders_bp.get("/summary_pdf")
def orders_summary_pdf():
    """Printable payment summary for current document filters. Ignores order_type filter."""
    from erp.utils.pdf import generate_account_summary_pdf_for_customers, resolve_summary_customer_ids

    args = _filter_args_from_request()
    customer_q = args["customer_q"]
    with get_db() as conn:
        scope, customer_row, _, _ = _resolve_customer_scope(conn, customer_q)
        if scope == "customer":
            bounds = conn.execute(
                """SELECT MIN(order_date) AS first_date, MAX(order_date) AS last_date
                   FROM orders WHERE customer_id=? AND deleted_at IS NULL""",
                (customer_row["id"],),
            ).fetchone()
        else:
            bounds = conn.execute(
                """SELECT MIN(order_date) AS first_date, MAX(order_date) AS last_date
                   FROM orders WHERE deleted_at IS NULL"""
            ).fetchone()
        (
            _date_mode,
            _year,
            _month,
            start_date,
            end_date,
            _start_day,
            _end_day,
            _sy,
            _sm,
            _ey,
            _em,
        ) = _resolve_date_range(
            args["date_mode_raw"],
            args["year_raw"],
            args["month_raw"],
            args["start_date_raw"],
            args["end_date_raw"],
            bounds["first_date"] if bounds else None,
            bounds["last_date"] if bounds else None,
            args["start_year_raw"],
            args["start_month_raw"],
            args["end_year_raw"],
            args["end_month_raw"],
        )

    customer_ids = resolve_summary_customer_ids(customer_q, start_date, end_date)
    if not customer_ids:
        return "当前筛选范围内没有可导出的客户单据", 400
    try:
        path = generate_account_summary_pdf_for_customers(customer_ids, start_date, end_date)
    except ValueError as exc:
        return str(exc), 400
    return send_file(path, as_attachment=False)


def _order_export_filter_context(fixed_order_type: str):
    """Resolve list filters for sales/return Excel export. fixed_order_type is sale|return."""
    args = _filter_args_from_request()
    customer_q = args["customer_q"]
    with get_db() as conn:
        scope, customer_row, _, _ = _resolve_customer_scope(conn, customer_q)
        if scope == "customer":
            bounds = conn.execute(
                """SELECT MIN(order_date) AS first_date, MAX(order_date) AS last_date
                   FROM orders WHERE customer_id=? AND deleted_at IS NULL""",
                (customer_row["id"],),
            ).fetchone()
        else:
            bounds = conn.execute(
                """SELECT MIN(order_date) AS first_date, MAX(order_date) AS last_date
                   FROM orders WHERE deleted_at IS NULL"""
            ).fetchone()
        (
            _date_mode,
            _year,
            _month,
            start_date,
            end_date,
            _start_day,
            _end_day,
            _sy,
            _sm,
            _ey,
            _em,
        ) = _resolve_date_range(
            args["date_mode_raw"],
            args["year_raw"],
            args["month_raw"],
            args["start_date_raw"],
            args["end_date_raw"],
            bounds["first_date"] if bounds else None,
            bounds["last_date"] if bounds else None,
            args["start_year_raw"],
            args["start_month_raw"],
            args["end_year_raw"],
            args["end_month_raw"],
        )

        conditions = ["o.deleted_at IS NULL", "o.order_type = ?"]
        params: list = [fixed_order_type]
        if customer_q:
            conditions.append("c.name LIKE ?")
            params.append(f"%{customer_q}%")
        conditions.append("o.order_date >= ?")
        params.append(start_date)
        conditions.append("o.order_date <= ?")
        params.append(end_date)
        where_clause = " AND ".join(conditions)
        lines = conn.execute(
            f"""
            SELECT o.order_no, o.order_date, o.order_type, o.status, o.notes, o.total_amount_cents,
                   c.name AS customer_name,
                   oi.product_name, oi.spec, oi.unit, oi.quantity, oi.unit_price_cents, oi.subtotal_cents
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            JOIN order_items oi ON oi.order_id = o.id
            WHERE {where_clause}
            ORDER BY o.order_date ASC, o.id ASC, oi.id ASC
            """,
            params,
        ).fetchall()
    return lines


@orders_bp.get("/export/sales.xlsx")
def export_sales_orders_excel():
    lines = _order_export_filter_context("sale")
    return workbook_download(
        order_line_export_headers(),
        order_line_export_rows(lines),
        sheet_title="销售单导出",
        filename=export_filename("销售单导出"),
    )


@orders_bp.get("/export/returns.xlsx")
def export_return_orders_excel():
    lines = _order_export_filter_context("return")
    return workbook_download(
        order_line_export_headers(),
        order_line_export_rows(lines),
        sheet_title="退货单导出",
        filename=export_filename("退货单导出"),
    )


@orders_bp.get("/new")
def new_order():
    return _new_order_form("sale")


@orders_bp.get("/return/new")
def new_return_order():
    return _new_order_form("return")


def _new_order_form(order_type: str):
    today = date.today().isoformat()
    with get_db() as conn:
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY name").fetchall()
        products = conn.execute("SELECT * FROM products WHERE deleted_at IS NULL ORDER BY usage_count DESC, name").fetchall()
        order_no = next_order_no(conn, today)
    return render_template("orders/new.html", customers=customers, products=products, today=today, order_no=order_no, order_type=order_type, cents_to_yuan=cents_to_yuan)


def _resolve_customer_id() -> int:
    customer_name = request.form.get("customer_name", "").strip()
    customer_id_text = request.form.get("customer_id", "").strip()
    with get_db() as conn:
        if customer_id_text:
            return int(customer_id_text)
        existing = conn.execute("SELECT id FROM customers WHERE name=?", (customer_name,)).fetchone()
        if existing:
            return int(existing["id"])
        cur = conn.execute("INSERT INTO customers(name) VALUES (?)", (customer_name,))
        return int(cur.lastrowid)


def _typed_rows_from_form() -> list[dict]:
    product_names = request.form.getlist("product_name")
    units = request.form.getlist("unit")
    unit_prices = request.form.getlist("unit_price")
    quantities = request.form.getlist("quantity")
    return [
        {"product_name": name, "unit": unit, "unit_price_yuan": price, "quantity": qty}
        for name, unit, price, qty in zip(product_names, units, unit_prices, quantities)
        if name.strip() and qty.strip()
    ]


def _resolve_create_order_date() -> str:
    """New documents may choose a business date; invalid/empty falls back to today."""
    raw = request.form.get("order_date", "").strip()
    if _valid_iso_date(raw):
        return raw
    return date.today().isoformat()


@orders_bp.post("/create")
def create_order_view():
    return _create_order_view("sale")


@orders_bp.post("/return/create")
def create_return_order_view():
    return _create_order_view("return")


def _create_order_view(order_type: str):
    customer_id = _resolve_customer_id()
    rows = _typed_rows_from_form()
    order_date = _resolve_create_order_date()
    with get_db() as conn:
        # Never trust client-supplied order_no; number follows chosen business date.
        order_no = next_order_no(conn, order_date)
    create_order_from_typed_rows(
        customer_id,
        order_no,
        rows,
        status=request.form.get("status", "saved"),
        order_date=order_date,
        notes=request.form.get("notes", ""),
        order_type=order_type,
    )
    with get_db() as conn:
        created = conn.execute("SELECT id FROM orders WHERE order_no=?", (order_no,)).fetchone()
        order_id = int(created["id"])
    if request.form.get("save_action") == "save_print":
        pdf_path = url_for("orders.order_pdf", order_id=order_id)
        if request.headers.get("X-Requested-With") == "fetch":
            next_url = url_for("orders.new_return_order" if order_type == "return" else "orders.new_order")
            return jsonify({
                "ok": True,
                "order_id": order_id,
                "order_no": order_no,
                "pdf_url": request.host_url.rstrip("/") + pdf_path,
                "detail_url": url_for("orders.view_order", order_id=order_id),
                "next_url": next_url,
            })
        return redirect(pdf_path, code=303)
    return redirect(url_for("orders.list_orders"))

@orders_bp.get("/<int:order_id>")
def view_order(order_id: int):
    with get_db() as conn:
        order = conn.execute("SELECT o.*, c.name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone()
        items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    return render_template("orders/detail.html", order=order, items=items, cents_to_yuan=cents_to_yuan)


@orders_bp.get("/<int:order_id>/edit")
def edit_order(order_id: int):
    with get_db() as conn:
        order = conn.execute("SELECT o.*, c.name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone()
        items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    return render_template("orders/new.html", order=order, items=items, today=date.today().isoformat(), order_type=order["order_type"] if "order_type" in order.keys() else "sale", cents_to_yuan=cents_to_yuan)


@orders_bp.post("/<int:order_id>/edit")
def update_order_view(order_id: int):
    customer_id = _resolve_customer_id()
    rows = _typed_rows_from_form()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT order_no, order_date FROM orders WHERE id=? AND deleted_at IS NULL",
            (order_id,),
        ).fetchone()
    if existing is None:
        return "订单不存在", 404
    # Re-edit keeps original order number and date; only business fields change.
    update_order_from_typed_rows(
        order_id,
        customer_id,
        existing["order_no"],
        rows,
        status=request.form.get("status", "saved"),
        order_date=existing["order_date"],
        notes=request.form.get("notes", ""),
    )
    return redirect(url_for("orders.view_order", order_id=order_id))


@orders_bp.get("/api/customers")
def api_customers():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, phone, address FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name LIMIT 20",
            (f"%{q}%",),
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@orders_bp.get("/api/products")
def api_products():
    q = request.args.get("q", "").strip()
    customer_id = request.args.get("customer_id", type=int) or 0
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.spec, p.unit,
                   COALESCE(cp.price_cents, p.default_price_cents) AS effective_price_cents,
                   p.default_price_cents,
                   cp.price_cents AS customer_price_cents
            FROM products p
            LEFT JOIN customer_prices cp ON cp.product_id=p.id AND cp.customer_id=?
            WHERE p.deleted_at IS NULL AND (p.name LIKE ? OR p.spec LIKE ? OR p.pinyin_initials LIKE ?)
            ORDER BY CASE WHEN cp.price_cents IS NULL THEN 1 ELSE 0 END, p.usage_count DESC, p.name
            LIMIT 20
            """,
            (customer_id, f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    return jsonify([
        {"id": row["id"], "name": row["name"], "spec": row["spec"], "unit": row["unit"], "unit_price": cents_to_yuan(row["effective_price_cents"]), "regular_price": cents_to_yuan(row["default_price_cents"]), "customer_price": cents_to_yuan(row["customer_price_cents"]) if row["customer_price_cents"] is not None else None, "is_customer_price": row["customer_price_cents"] is not None}
        for row in rows
    ])


@orders_bp.get("/api/next_order_no")
def api_next_order_no():
    """Preview next MD order number for a chosen business date (create flow only)."""
    raw = request.args.get("date", "").strip()
    order_date = raw if _valid_iso_date(raw) else date.today().isoformat()
    with get_db() as conn:
        order_no = next_order_no(conn, order_date)
    return jsonify({"order_date": order_date, "order_no": order_no})


@orders_bp.get("/<int:order_id>/pdf")
def order_pdf(order_id: int):
    path = generate_order_pdf(order_id)
    return send_file(path, as_attachment=False)

@orders_bp.post("/<int:order_id>/confirm_print")
def confirm_print(order_id: int):
    mark_order_printed(order_id); return redirect(url_for("orders.list_orders"))

@orders_bp.post("/bulk_delete")
def bulk_delete_orders():
    ids = [int(x) for x in request.form.getlist("ids")]
    with get_db() as conn:
        conn.executemany("UPDATE orders SET deleted_at=CURRENT_TIMESTAMP, delete_reason='批量删除' WHERE id=?", [(i,) for i in ids])
    return redirect(url_for("orders.list_orders"))


@orders_bp.post("/<int:order_id>/delete")
def delete_order_view(order_id: int):
    with get_db() as conn:
        conn.execute("UPDATE orders SET deleted_at=CURRENT_TIMESTAMP, delete_reason='用户删除' WHERE id=?", (order_id,))
    if request.headers.get("X-Requested-With") or "fetch" in request.headers.get("Sec-Fetch-Mode", ""):
        return Response(status=204)
    return redirect(url_for("orders.list_orders"))
