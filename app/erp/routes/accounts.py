from decimal import InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, send_file
import sqlite3

from erp.db import get_db
from erp.services.accounting import (
    add_payment,
    add_adjustment,
    get_customer_account_ledger,
    void_payment as void_payment_record,
    void_adjustment as void_adjustment_record,
)
from erp.utils.errors import error_response, not_found, RecordNotFound
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
        return error_response("请先在账款管理页选择客户，再打印客户货款汇总表。", title="未选择客户",
                              back_url=url_for("accounts.accounts"))
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    try:
        path = generate_account_summary_pdf(customer_id, start_date, end_date)
    except RecordNotFound as error:
        return not_found(str(error), back_url=url_for("accounts.accounts"))
    except ValueError as error:
        return error_response(str(error), title="客户货款汇总表未能生成", back_url=url_for("accounts.accounts"))
    return send_pdf_for_preview(path, "客户货款汇总表.pdf", "客户货款汇总表")


@accounts_bp.get("/ledger_pdf")
def ledger_pdf():
    customer_id = request.args.get("customer_id", type=int)
    if not customer_id:
        return error_response("请先在账款管理页选择客户，再打印客户账款流水。", title="未选择客户",
                              back_url=url_for("accounts.accounts"))
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    try:
        path = generate_account_ledger_pdf(customer_id, start_date, end_date)
    except ValueError as error:
        return not_found(str(error), back_url=url_for("accounts.accounts"))
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
        return error_response(str(error), title="收款记录未能作废", back_url=url_for("accounts.accounts"))
    return _redirect_to_ledger()


@accounts_bp.post("/adjustment/<int:adjustment_id>/void")
def void_adjustment(adjustment_id: int):
    try:
        void_adjustment_record(adjustment_id, request.form.get("reason", ""))
    except ValueError as error:
        return error_response(str(error), title="调整记录未能作废", back_url=url_for("accounts.accounts"))
    return _redirect_to_ledger()


@accounts_bp.post("/payment")
def payment():
    back_url = url_for("accounts.accounts", customer_id=request.form.get("customer_id", type=int) or None,
                       start_date=request.form.get("start_date", "").strip(),
                       end_date=request.form.get("end_date", "").strip())
    try:
        customer_id = int(request.form["customer_id"])
    except (KeyError, TypeError, ValueError):
        return error_response("请先选择客户，再登记收款。", title="未能登记收款", back_url=back_url)
    try:
        amount_cents = yuan_to_cents(request.form["amount"])
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return error_response("收款金额不是有效数字，请填写例如 500.00 的金额。", title="未能登记收款", back_url=back_url)
    if amount_cents <= 0:
        return error_response("收款金额必须大于 0。", title="未能登记收款", back_url=back_url)
    try:
        add_payment(customer_id, amount_cents, method=request.form.get("method", "现金"), notes=request.form.get("notes", ""))
    except (ValueError, sqlite3.IntegrityError) as error:
        return error_response(str(error) if isinstance(error, ValueError) else "收款金额不合法或客户不存在。",
                              title="未能登记收款", back_url=back_url)
    return _redirect_to_ledger(customer_id)


@accounts_bp.post("/adjustment")
def adjustment():
    back_url = url_for("accounts.accounts", customer_id=request.form.get("customer_id", type=int) or None,
                       start_date=request.form.get("start_date", "").strip(),
                       end_date=request.form.get("end_date", "").strip())
    try:
        customer_id = int(request.form["customer_id"])
    except (KeyError, TypeError, ValueError):
        return error_response("请先选择客户，再登记账务调整。", title="未能登记调整", back_url=back_url)
    try:
        amount_cents = yuan_to_cents(request.form["amount"])
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return error_response("调整金额不是有效数字，请填写例如 -100.00 的金额（可为负）。", title="未能登记调整", back_url=back_url)
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        return error_response("请填写调整原因，便于后续追溯。", title="未能登记调整", back_url=back_url)
    try:
        add_adjustment(customer_id, amount_cents, reason, request.form.get("adjustment_type", "other"))
    except (ValueError, sqlite3.IntegrityError) as error:
        return error_response(str(error) if isinstance(error, ValueError) else "调整金额不合法或客户不存在。",
                              title="未能登记调整", back_url=back_url)
    return _redirect_to_ledger(customer_id)
