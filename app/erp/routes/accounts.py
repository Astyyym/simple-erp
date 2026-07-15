from flask import Blueprint, render_template, request, redirect, url_for, send_file
from erp.db import get_db
from erp.services.accounting import add_payment, add_adjustment
from erp.utils.money import yuan_to_cents, cents_to_yuan
from erp.utils.pdf import generate_account_summary_pdf, send_pdf_for_preview

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")

@accounts_bp.get("/")
def accounts():
    customer_id = request.args.get("customer_id", type=int)
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    with get_db() as conn:
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY name").fetchall()
        balance_rows = conn.execute(
            """
            SELECT
                c.id,
                COALESCE(c.opening_balance_cents, 0)
                + COALESCE(o.order_total, 0)
                + COALESCE(a.adjustment_total, 0)
                - COALESCE(p.payment_total, 0) AS balance_cents
            FROM customers c
            LEFT JOIN (
                SELECT customer_id, SUM(total_amount_cents) AS order_total
                FROM orders
                WHERE status IN ('saved','printed') AND deleted_at IS NULL
                GROUP BY customer_id
            ) o ON o.customer_id = c.id
            LEFT JOIN (
                SELECT customer_id, SUM(amount_cents) AS payment_total
                FROM payments
                WHERE status='active'
                GROUP BY customer_id
            ) p ON p.customer_id = c.id
            LEFT JOIN (
                SELECT customer_id, SUM(amount_cents) AS adjustment_total
                FROM adjustments
                WHERE status='active'
                GROUP BY customer_id
            ) a ON a.customer_id = c.id
            WHERE c.deleted_at IS NULL
            """
        ).fetchall()
        balances = {row["id"]: int(row["balance_cents"] or 0) for row in balance_rows}
        selected_customer = None
        orders = []
        if customer_id:
            selected_customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
            conditions = ["customer_id=?", "deleted_at IS NULL"]
            params = [customer_id]
            if start_date:
                conditions.append("order_date >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("order_date <= ?")
                params.append(end_date)
            orders = conn.execute(f"SELECT * FROM orders WHERE {' AND '.join(conditions)} ORDER BY order_date ASC, id ASC", params).fetchall()
    return render_template("accounts/index.html", customers=customers, balances=balances, selected_customer=selected_customer, orders=orders, start_date=start_date, end_date=end_date, cents_to_yuan=cents_to_yuan)

@accounts_bp.get("/summary_pdf")
def summary_pdf():
    customer_id = request.args.get("customer_id", type=int)
    if not customer_id:
        return "必须先选择客户", 400
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    path = generate_account_summary_pdf(customer_id, start_date, end_date)
    return send_pdf_for_preview(path, "客户货款汇总表.pdf", "客户货款汇总表")


@accounts_bp.post("/bulk_delete_orders")
def bulk_delete_orders():
    ids = [int(x) for x in request.form.getlist("ids")]
    customer_id = request.form.get("customer_id", "")
    with get_db() as conn:
        conn.executemany("UPDATE orders SET deleted_at=CURRENT_TIMESTAMP, delete_reason='账款页批量删除' WHERE id=?", [(i,) for i in ids])
    if customer_id:
        return redirect(url_for("accounts.accounts", customer_id=customer_id))
    return redirect(url_for("accounts.accounts"))

@accounts_bp.post("/payment")
def payment():
    add_payment(int(request.form["customer_id"]), yuan_to_cents(request.form["amount"]), method=request.form.get("method", "现金"), notes=request.form.get("notes", ""))
    return redirect(url_for("accounts.accounts"))

@accounts_bp.post("/adjustment")
def adjustment():
    add_adjustment(int(request.form["customer_id"]), yuan_to_cents(request.form["amount"]), request.form["reason"], request.form.get("adjustment_type", "other"))
    return redirect(url_for("accounts.accounts"))
