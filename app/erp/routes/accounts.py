from decimal import InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, send_file, jsonify
import sqlite3

from erp.db import get_db
from erp.services.accounting import (
    add_payment,
    add_adjustment,
    get_customer_account_ledger,
    get_ledger_document_items,
    void_payment as void_payment_record,
    void_adjustment as void_adjustment_record,
)
from erp.services.reconciliation import (
    create_reconciliation_snapshot,
    get_reconciliation_order_detail,
    get_reconciliation_print_context,
    get_reconciliation_snapshot,
    toggle_reconciliation_selection,
    update_reconciliation_selection,
)
from erp.utils.errors import error_response, not_found, RecordNotFound
from erp.utils.money import yuan_to_cents, cents_to_yuan
from erp.utils.pdf import (
    ensure_account_summary_printable,
    generate_account_summary_pdf,
    generate_reconciliation_pdf,
    render_desktop_preview,
    send_pdf_for_preview,
    wants_desktop_preview,
)

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


@accounts_bp.get("/")
def accounts():
    customer_id = request.args.get("customer_id", type=int)
    # 客户字段 2026-10-17 由原生下拉改「输入即模糊匹配 + 候选点选」：
    # 可见输入框只负责回显名称，真正定位客户仍靠 hidden `customer_id`
    # （页面下方的收款 / 调整 / 作废 / 对账入口都按 id 工作）。
    # 只输入名称、没点候选时不猜是谁（半截名可能命中别的客户），直接提示去候选里选。
    customer_query = request.args.get("customer", "").strip()
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
    customer_error = ""
    if selected_row is not None:
        ledger = get_customer_account_ledger(customer_id, start_date, end_date, type_filter)
        selected_customer = ledger["customer"]
    elif customer_id:
        customer_error = "该客户不存在或已删除，请重新从候选里选择。"
    elif customer_query:
        customer_error = "请从下拉候选里选择客户后再查询流水。"

    return render_template(
        "accounts/index.html",
        customers=customers,
        balances=balances,
        selected_customer=selected_customer,
        ledger=ledger,
        customer_query=customer_query,
        customer_error=customer_error,
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
    if wants_desktop_preview():
        # 预览页只渲染工具栏；PDF 由 iframe 再请求一次（一次点击只渲染一次）。
        try:
            ensure_account_summary_printable(customer_id)
        except RecordNotFound as error:
            return not_found(str(error), back_url=url_for("accounts.accounts"))
        except ValueError as error:
            return error_response(str(error), title="客户货款汇总表未能生成", back_url=url_for("accounts.accounts"))
        return render_desktop_preview("客户货款汇总表.pdf", "客户货款汇总表")
    try:
        path = generate_account_summary_pdf(customer_id, start_date, end_date)
    except RecordNotFound as error:
        return not_found(str(error), back_url=url_for("accounts.accounts"))
    except ValueError as error:
        return error_response(str(error), title="客户货款汇总表未能生成", back_url=url_for("accounts.accounts"))
    return send_pdf_for_preview(path, "客户货款汇总表.pdf", "客户货款汇总表")


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


@accounts_bp.get("/api/ledger_items")
def api_ledger_items():
    """流水行就地展开用：某条单据的商品明细（收款/调整没有明细 → 400）。

    只在用户点「展开」时按需取，不塞进列表页 HTML：流水可能有几百行，
    明细全量渲染会白白放大页面。
    """
    source = request.args.get("source", "").strip()
    document_id = request.args.get("id", type=int)
    if not document_id:
        return jsonify({"error": "缺少单据 ID"}), 400
    try:
        payload = get_ledger_document_items(source, document_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(payload)


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


# ── 往来对账（2026-10-14 起归属账款管理）──────────────────────────────────────
# 原路由在 /analytics/reconciliation：页面由账款管理进入，侧栏高亮与返回按钮却
# 归数据分析，`from=accounts` 覆盖机制有三个漏点（勾选跳转、全选 redirect、
# 打印预览 return_to），已实测复现。现整体并入 accounts 模块：endpoint 即
# owner，归属与返回恒定「账款管理」，不再需要来源参数。

@accounts_bp.get("/reconciliation")
def reconciliation():
    with get_db() as conn:
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY name").fetchall()
    customer_id = request.args.get("customer_id", type=int)
    # 往来对象字段 2026-10-17 由原生下拉改「输入即模糊匹配 + 候选点选」，与账款管理同源；
    # 真正定位对象仍靠 hidden `customer_id`（快照、返回链接、分页都按 id 工作）。
    customer_query = request.args.get("customer", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    order_type_filter = request.args.get("order_type", "all").strip() or "all"
    snapshot_id = request.args.get("snapshot_id", type=int)
    page = max(request.args.get("page", 1, type=int), 1)
    page_size = 50
    error = ""
    snapshot = None
    if snapshot_id:
        try:
            snapshot = get_reconciliation_snapshot(snapshot_id)
            customer_id = snapshot["customer_id"]
            start_date = snapshot["start_date"]
            end_date = snapshot["end_date"]
            order_type_filter = snapshot["order_type_filter"]
        except ValueError as exc:
            error = str(exc)
    elif customer_id:
        try:
            snapshot_id = create_reconciliation_snapshot(customer_id, start_date, end_date, order_type_filter)
            snapshot = get_reconciliation_snapshot(snapshot_id)
        except ValueError as exc:
            error = str(exc)
    elif customer_query:
        error = "请从下拉候选里选择往来对象后再查询。"
    display_rows = []
    total_pages = 0
    if snapshot:
        total_pages = max((snapshot["scope_count"] + page_size - 1) // page_size, 1)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        display_rows = snapshot["rows"][start : start + page_size]
    customer_name = next((row["name"] for row in customers if row["id"] == customer_id), "")
    return render_template(
        "accounts/reconciliation.html",
        customers=customers,
        snapshot=snapshot,
        error=error,
        customer_id=customer_id,
        customer_name=customer_name,
        start_date=start_date,
        end_date=end_date,
        order_type_filter=order_type_filter,
        cents_to_yuan=cents_to_yuan,
        display_rows=display_rows,
        page=page,
        total_pages=total_pages,
        recon_back_url=url_for(
            "accounts.accounts",
            customer_id=customer_id or None,
            start_date=start_date or None,
            end_date=end_date or None,
        ),
        recon_back_label="返回账款管理",
        return_url=(
            request.full_path.removesuffix("?")
            if request.args.get("snapshot_id", type=int) == snapshot_id and request.args.get("page", 1, type=int) == page
            else url_for("accounts.reconciliation", snapshot_id=snapshot_id, page=page)
        ) if snapshot else "",
    ), (400 if error else 200)


@accounts_bp.post("/reconciliation/select/<int:snapshot_id>")
def reconciliation_select(snapshot_id: int):
    try:
        update_reconciliation_selection(
            snapshot_id,
            mode=request.form.get("mode", "explicit"),
            selected_ids=request.form.getlist("selected_ids"),
            excluded_ids=request.form.getlist("excluded_ids"),
        )
        snapshot = get_reconciliation_snapshot(snapshot_id)
    except (TypeError, ValueError) as exc:
        return error_response(str(exc), title="对账选择未保存", back_url=url_for("accounts.reconciliation"))
    return redirect(url_for(
        "accounts.reconciliation",
        customer_id=snapshot["customer_id"],
        start_date=snapshot["start_date"] or None,
        end_date=snapshot["end_date"] or None,
        order_type=snapshot["order_type_filter"],
        snapshot_id=snapshot_id,
        page=max(request.form.get("page", 1, type=int), 1),
    ))


@accounts_bp.post("/reconciliation/toggle/<int:snapshot_id>/<int:order_id>")
def reconciliation_toggle(snapshot_id: int, order_id: int):
    try:
        # Compatibility endpoint: a bare integer always refers to orders.
        snapshot = get_reconciliation_snapshot(snapshot_id)
        toggle_reconciliation_selection(
            snapshot_id,
            order_id if snapshot["scope_version"] == 1 else f"order:{order_id}",
            request.form.get("selected", "0") == "1",
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@accounts_bp.post("/reconciliation/toggle/<int:snapshot_id>/document/<string:document_key>")
def reconciliation_document_toggle(snapshot_id: int, document_key: str):
    try:
        toggle_reconciliation_selection(snapshot_id, document_key, request.form.get("selected", "0") == "1")
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@accounts_bp.get("/reconciliation/<int:snapshot_id>/document/<string:order_id>")
def reconciliation_document_detail(snapshot_id: int, order_id: str):
    return reconciliation_order_detail(snapshot_id, order_id)


@accounts_bp.get("/reconciliation/<int:snapshot_id>/order/<int:order_id>")
def reconciliation_order_detail(snapshot_id: int, order_id: int):
    try:
        detail = get_reconciliation_order_detail(snapshot_id, order_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    if detail is None:
        return jsonify({"error": "单据不在当前对账查询范围内"}), 404
    return jsonify(detail)


@accounts_bp.get("/reconciliation/pdf/<int:snapshot_id>")
def reconciliation_pdf(snapshot_id: int):
    if wants_desktop_preview():
        # 预览页只渲染工具栏；PDF 由 iframe 再请求一次（一次点击只渲染一次）。
        try:
            # 对账单的校验口径在 get_reconciliation_print_context 内（快照非法/未选单据即抛错）。
            get_reconciliation_print_context(snapshot_id)
        except RecordNotFound as exc:
            return not_found(str(exc), title="对账查询不存在", back_url=url_for("accounts.reconciliation"))
        except ValueError as exc:
            return error_response(str(exc), title="对账单未能生成", back_url=url_for("accounts.reconciliation"))
        return render_desktop_preview("往来交易对账单.pdf", "往来交易对账单")
    try:
        path = generate_reconciliation_pdf(snapshot_id)
    except RecordNotFound as exc:
        return not_found(str(exc), title="对账查询不存在", back_url=url_for("accounts.reconciliation"))
    except ValueError as exc:
        return error_response(str(exc), title="对账单未能生成", back_url=url_for("accounts.reconciliation"))
    return send_pdf_for_preview(path, "往来交易对账单.pdf", "往来交易对账单")
