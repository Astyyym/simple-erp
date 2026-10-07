from flask import Blueprint, render_template, request, redirect, url_for, send_file
from erp.db import get_db
from erp.services.accounting import (
    add_payment,
    add_adjustment,
    get_customer_account_ledger,
    void_payment as void_payment_record,
    void_adjustment as void_adjustment_record,
)
from erp.utils.money import yuan_to_cents, cents_to_yuan
from erp.utils.pdf import (
    generate_account_summary_pdf,
    generate_account_ledger_pdf,
    send_pdf_for_preview,
)

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


@accounts_bp.get("/")
def accounts():
    customer_id = request.args.get("customer_id", type=int)
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    type_filter = [t for t in request.args.getlist("type") if t]
    with get_db() as conn:
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY name").fetchall()
        balance_rows = conn.execute(
            """
            SELECT
                c.id,
                COALESCE(c.opening_balance_cents, 0)
                + COALESCE(o.order_total, 0)
                + COALESCE(a.adjustment_total, 0)
                - COALESCE(p.payment_total, 0)
                - COALESCE(po.purchase_total, 0)
                + COALESCE(pr.purchase_return_total, 0) AS balance_cents
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
            LEFT JOIN (
                SELECT customer_id, SUM(total_amount_cents) AS purchase_total
                FROM purchase_orders
                WHERE status='saved' AND deleted_at IS NULL
                GROUP BY customer_id
            ) po ON po.customer_id = c.id
            LEFT JOIN (
                SELECT customer_id, SUM(total_amount_cents) AS purchase_return_total
                FROM purchase_return_orders
                WHERE status='saved' AND deleted_at IS NULL
                GROUP BY customer_id
            ) pr ON pr.customer_id = c.id
            WHERE c.deleted_at IS NULL
            """
        ).fetchall()
        balances = {row["id"]: int(row["balance_cents"] or 0) for row in balance_rows}
        selected_row = None
        if customer_id:
            selected_row = conn.execute(
                "SELECT * FROM customers WHERE id=? AND deleted_at IS NULL",
                (customer_id,),
            ).fetchone()

    ledger = None
    selected_customer = None
    if selected_row is not None:
        ledger = get_customer_account_ledger(customer_id, start_date, end_date, type_filter)
        selected_customer = ledger["customer"]

    return render_template(
        "accounts/index.html",
        customers=customers,
        balances=balances,
        selected_customer=selected_customer,
        ledger=ledger,
        start_date=start_date,
        end_date=end_date,
        type_filter=type_filter,
        cents_to_yuan=cents_to_yuan,
    )

@accounts_bp.get("/summary_pdf")
def summary_pdf():
    customer_id = request.args.get("customer_id", type=int)
    if not customer_id:
        return "必须先选择客户", 400
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    path = generate_account_summary_pdf(customer_id, start_date, end_date)
    return send_pdf_for_preview(path, "客户货款汇总表.pdf", "客户货款汇总表")


@accounts_bp.get("/ledger_pdf")
def ledger_pdf():
    customer_id = request.args.get("customer_id", type=int)
    if not customer_id:
        return "必须先选择客户", 400
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    try:
        path = generate_account_ledger_pdf(customer_id, start_date, end_date)
    except ValueError as error:
        return str(error), 404
    return send_pdf_for_preview(path, "客户账款流水.pdf", "客户账款流水")


def _redirect_to_ledger(customer_id: int | None = None):
    customer_id = customer_id or request.form.get("customer_id", type=int)
    params = {}
    if customer_id:
        params["customer_id"] = customer_id
    for key in ("start_date", "end_date"):
        value = request.form.get(key, "").strip()
        if value:
            params[key] = value
    return redirect(url_for("accounts.accounts", **params))


@accounts_bp.post("/payment/<int:payment_id>/void")
def void_payment(payment_id: int):
    try:
        void_payment_record(payment_id, request.form.get("reason", ""))
    except ValueError as error:
        return str(error), 400
    return _redirect_to_ledger()


@accounts_bp.post("/adjustment/<int:adjustment_id>/void")
def void_adjustment(adjustment_id: int):
    try:
        void_adjustment_record(adjustment_id, request.form.get("reason", ""))
    except ValueError as error:
        return str(error), 400
    return _redirect_to_ledger()


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
    customer_id = int(request.form["customer_id"])
    add_payment(customer_id, yuan_to_cents(request.form["amount"]), method=request.form.get("method", "现金"), notes=request.form.get("notes", ""))
    return _redirect_to_ledger(customer_id)


@accounts_bp.post("/adjustment")
def adjustment():
    customer_id = int(request.form["customer_id"])
    add_adjustment(customer_id, yuan_to_cents(request.form["amount"]), request.form["reason"], request.form.get("adjustment_type", "other"))
    return _redirect_to_ledger(customer_id)
