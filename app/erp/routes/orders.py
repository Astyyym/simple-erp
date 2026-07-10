from flask import Blueprint, render_template, request, redirect, url_for, send_file, jsonify, Response
from datetime import date
import re
from erp.db import get_db
from erp.services.accounting import create_order_from_typed_rows, update_order_from_typed_rows, mark_order_printed, void_order
from erp.utils.money import cents_to_yuan
from erp.utils.pdf import generate_order_pdf

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")

def next_order_no(conn, order_date: str) -> str:
    prefix = order_date.replace("-", "")
    rows = conn.execute("SELECT order_no FROM orders WHERE order_no LIKE ?", (f"{prefix}-%",)).fetchall()
    max_seq = 0
    for row in rows:
        match = re.fullmatch(rf"{prefix}-(\d+)", row["order_no"])
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return f"{prefix}-{max_seq + 1:03d}"


def bump_order_no_if_exists(conn, order_no: str) -> str:
    if conn.execute("SELECT 1 FROM orders WHERE order_no=?", (order_no,)).fetchone() is None:
        return order_no
    match = re.fullmatch(r"(.+?-)(\d+)", order_no)
    if not match:
        base = f"{order_no}-"
        width = 3
        start = 1
    else:
        base, digits = match.groups()
        width = len(digits)
        start = int(digits) + 1
    seq = start
    while True:
        candidate = f"{base}{seq:0{width}d}"
        if conn.execute("SELECT 1 FROM orders WHERE order_no=?", (candidate,)).fetchone() is None:
            return candidate
        seq += 1


@orders_bp.get("/")
def list_orders():
    customer_q = request.args.get("customer", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    conditions = ["o.deleted_at IS NULL"]
    params = []
    if customer_q:
        conditions.append("c.name LIKE ?")
        params.append(f"%{customer_q}%")
    if start_date:
        conditions.append("o.order_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("o.order_date <= ?")
        params.append(end_date)
    where_clause = " AND ".join(conditions)
    with get_db() as conn:
        orders = conn.execute(f"SELECT o.*, c.name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE {where_clause} ORDER BY o.id DESC LIMIT 200", params).fetchall()
    return render_template("orders/list.html", orders=orders, customer_q=customer_q, start_date=start_date, end_date=end_date, cents_to_yuan=cents_to_yuan)

@orders_bp.get("/new")
def new_order():
    today = request.args.get("date") or date.today().isoformat()
    with get_db() as conn:
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY name").fetchall()
        products = conn.execute("SELECT * FROM products WHERE deleted_at IS NULL ORDER BY usage_count DESC, name").fetchall()
        order_no = next_order_no(conn, today)
    return render_template("orders/new.html", customers=customers, products=products, today=today, order_no=order_no, cents_to_yuan=cents_to_yuan)

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


@orders_bp.post("/create")
def create_order_view():
    customer_id = _resolve_customer_id()
    rows = _typed_rows_from_form()
    with get_db() as conn:
        order_no = bump_order_no_if_exists(conn, request.form["order_no"])
    create_order_from_typed_rows(customer_id, order_no, rows, status=request.form.get("status", "saved"), order_date=request.form.get("order_date"), notes=request.form.get("notes", ""))
    with get_db() as conn:
        created = conn.execute("SELECT id FROM orders WHERE order_no=?", (order_no,)).fetchone()
        order_id = int(created["id"])
    if request.form.get("save_action") == "save_print":
        return redirect(url_for("orders.order_pdf", order_id=order_id))
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
    return render_template("orders/new.html", order=order, items=items, today=date.today().isoformat(), cents_to_yuan=cents_to_yuan)


@orders_bp.post("/<int:order_id>/edit")
def update_order_view(order_id: int):
    customer_id = _resolve_customer_id()
    rows = _typed_rows_from_form()
    update_order_from_typed_rows(order_id, customer_id, request.form["order_no"], rows, status=request.form.get("status", "saved"), order_date=request.form.get("order_date"), notes=request.form.get("notes", ""))
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
