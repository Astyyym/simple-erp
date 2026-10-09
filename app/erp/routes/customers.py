from decimal import InvalidOperation
from uuid import uuid4

from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify
from erp.db import get_db
from erp.services.accounting import create_customer
from erp.services.master_data_import import (
    CUSTOMER_HEADERS,
    build_customer_preview,
    customer_totals,
    ImportPreview,
)
from erp.utils.exporting import (
    customer_export_headers,
    customer_export_rows,
    export_filename,
    workbook_download,
)
from erp.utils.errors import error_response, not_found
from erp.utils.importing import ImportFileError, ImportResult, excel_template, normalized_name, read_upload_detailed
from erp.utils.money import yuan_to_cents, cents_to_yuan
from erp.utils.backup import list_backups

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")


def _backup_hint() -> dict:
    """A-6/A-7：迁移前提醒与「本库尚无任何备份」告警。"""
    return {"has_backup": bool(list_backups())}


def _requested_opening_balance_cents() -> int:
    """解析期初余额；非法输入抛出 ValueError（供调用方转成中文错误页）。"""
    try:
        return yuan_to_cents(request.form.get("opening_balance", "0") or "0")
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("期初余额不是有效数字，请填写例如 0.00 的金额") from None

@customers_bp.get("/import/template")
def download_import_template():
    return excel_template(list(CUSTOMER_HEADERS[1]), "客户导入模板.xlsx")


@customers_bp.get("/export.xlsx")
def export_customers_excel():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        customers, _suggestions, _truncated, _todo = _customer_list_context(conn, q)
    return workbook_download(
        customer_export_headers(),
        customer_export_rows(customers),
        sheet_title="客户导出",
        filename=export_filename("客户导出"),
    )


def _customer_list_context(conn, q: str = "", quality: str = ""):
    todo_sql = (
        " AND origin='order' AND TRIM(COALESCE(phone, ''))='' AND TRIM(COALESCE(address, ''))=''"
        if quality == "todo"
        else ""
    )
    if q:
        customers = conn.execute(
            "SELECT * FROM customers WHERE deleted_at IS NULL AND (name LIKE ? OR phone LIKE ? OR address LIKE ?)"
            + todo_sql
            + " ORDER BY name COLLATE NOCASE ASC, id ASC LIMIT 200",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        customers = conn.execute(
            "SELECT * FROM customers WHERE deleted_at IS NULL"
            + todo_sql
            + " ORDER BY name COLLATE NOCASE ASC, id ASC LIMIT 200"
        ).fetchall()
    # E4：到达上限即提示「已截断，可用搜索缩小范围」。
    truncated = len(customers) >= 200
    suggestions = conn.execute(
        "SELECT DISTINCT name FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name LIMIT 20",
        (f"%{q}%" if q else "%",),
    ).fetchall()
    todo_count = conn.execute(
        "SELECT COUNT(*) AS c FROM customers WHERE deleted_at IS NULL"
        " AND origin='order' AND TRIM(COALESCE(phone, ''))='' AND TRIM(COALESCE(address, ''))=''"
    ).fetchone()["c"]
    return customers, suggestions, truncated, todo_count


@customers_bp.post("/import")
def import_customers():
    """B-4：第一段——只解析出预览，不落库。"""
    try:
        headers, rows = read_upload_detailed(request.files.get("file"), CUSTOMER_HEADERS)
    except (ImportFileError, AttributeError) as exc:
        message = str(exc) if str(exc) else "请选择要导入的文件"
        with get_db() as conn:
            customers, suggestions, truncated, todo_count = _customer_list_context(conn)
        return render_template(
            "customers/list.html",
            customers=customers,
            suggestions=suggestions,
            truncated=truncated,
            todo_count=todo_count,
            quality="",
            cents_to_yuan=cents_to_yuan,
            q="",
            import_error=message,
            **_backup_hint(),
        ), 400

    with get_db() as conn:
        existing_names = {row["name"] for row in conn.execute("SELECT name FROM customers WHERE deleted_at IS NULL")}
        soft_deleted = {row["name"] for row in conn.execute("SELECT name FROM customers WHERE deleted_at IS NOT NULL")}
    same_name = request.form.get("same_name", "skip")
    if same_name not in ("skip", "update"):
        same_name = "skip"
    preview = build_customer_preview(headers, rows, existing_names, soft_deleted, same_name=same_name)
    preview.batch_id = uuid4().hex
    return render_template(
        "import_preview.html",
        kind="customers",
        title="客户导入预览",
        action_url=url_for("customers.import_customers_confirm"),
        back_url=url_for("customers.list_customers"),
        preview=preview,
        totals=customer_totals(preview),
        cents_to_yuan=cents_to_yuan,
        **_backup_hint(),
    )


@customers_bp.post("/import/confirm")
def import_customers_confirm():
    """B-4：第二段——服务端二次校验后落库。"""
    raw = request.form.get("preview_json", "")
    same_name = request.form.get("same_name", "skip")
    try:
        preview = ImportPreview.from_json(raw)
    except Exception:  # noqa: BLE001 — 预览态损坏一律拒绝，不猜测
        return error_response("导入预览已失效，请重新上传文件。", title="导入未执行",
                              back_url=url_for("customers.list_customers"))
    if preview.kind != "customers":
        return error_response("导入预览类型不匹配，请重新上传。", title="导入未执行",
                              back_url=url_for("customers.list_customers"))

    result = ImportResult()
    with get_db() as conn:
        existing_names = {row["name"] for row in conn.execute("SELECT name FROM customers WHERE deleted_at IS NULL")}
        soft_deleted = {row["name"] for row in conn.execute("SELECT name FROM customers WHERE deleted_at IS NOT NULL")}
        for row in preview.rows:
            if row.action == "error":
                result.add_error(f"第{row.line_number}行：{row.reason}")
                continue
            if row.action == "skip":
                result.skipped += 1
                continue
            name = row.payload.get("name", "")
            # 二次校验：预览后库可能已变化。
            if name in soft_deleted:
                result.add_error(f"第{row.line_number}行：该名称已被回收站记录占用")
                continue
            if row.action == "add":
                if name in existing_names:
                    result.skipped += 1
                    continue
                conn.execute(
                    "INSERT INTO customers(name, phone, address, opening_balance_cents, origin) VALUES (?, ?, ?, ?, 'import')",
                    (name, row.payload.get("phone", ""), row.payload.get("address", ""),
                     int(row.payload.get("opening_balance_cents", 0))),
                )
                existing_names.add(name)
                result.added += 1
            elif row.action == "update":
                if name not in existing_names:
                    result.add_error(f"第{row.line_number}行：同名客户已不存在，无法更新")
                    continue
                # 只覆盖可空档案字段，不动业务流水。
                conn.execute(
                    "UPDATE customers SET phone=?, address=?, opening_balance_cents=?, updated_at=CURRENT_TIMESTAMP WHERE name=? AND deleted_at IS NULL",
                    (row.payload.get("phone", ""), row.payload.get("address", ""),
                     int(row.payload.get("opening_balance_cents", 0)), name),
                )
                result.added += 1
        customers, suggestions, truncated, todo_count = _customer_list_context(conn)
    return render_template(
        "customers/list.html",
        customers=customers,
        suggestions=suggestions,
        truncated=truncated,
        todo_count=todo_count,
        quality="",
        cents_to_yuan=cents_to_yuan,
        q="",
        import_result=result,
        **_backup_hint(),
    )


@customers_bp.get("/")
def list_customers():
    q = request.args.get("q", "").strip()
    quality = request.args.get("quality", "").strip()
    with get_db() as conn:
        customers, suggestions, truncated, todo_count = _customer_list_context(conn, q, quality)
    return render_template(
        "customers/list.html",
        customers=customers,
        suggestions=suggestions,
        truncated=truncated,
        todo_count=todo_count,
        quality=quality,
        cents_to_yuan=cents_to_yuan,
        q=q,
        **_backup_hint(),
    )

@customers_bp.get("/api/suggestions")
def customer_suggestions():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT name FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name LIMIT 20",
            (f"%{q}%",),
        ).fetchall()
    return jsonify([row["name"] for row in rows])


@customers_bp.post("/create")
def create_customer_view():
    try:
        opening_balance_cents = _requested_opening_balance_cents()
    except ValueError as exc:
        return error_response(str(exc), title="客户未保存", back_url=url_for("customers.list_customers"))
    create_customer(request.form["name"], request.form.get("phone", ""), request.form.get("address", ""), opening_balance_cents)
    return redirect(url_for("customers.list_customers"))

@customers_bp.get("/<int:customer_id>/edit")
def edit_customer(customer_id: int):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=? AND deleted_at IS NULL", (customer_id,)).fetchone()
    if customer is None:
        return not_found("客户不存在，可能已被删除或从未存在。", back_url=url_for("customers.list_customers"))
    return render_template("customers/edit.html", customer=customer, cents_to_yuan=cents_to_yuan)

@customers_bp.post("/<int:customer_id>/edit")
def update_customer(customer_id: int):
    try:
        opening_balance_cents = _requested_opening_balance_cents()
    except ValueError as exc:
        return error_response(str(exc), title="客户未保存", back_url=url_for("customers.edit_customer", customer_id=customer_id))
    with get_db() as conn:
        existing = conn.execute("SELECT 1 FROM customers WHERE id=? AND deleted_at IS NULL", (customer_id,)).fetchone()
        if existing is None:
            return not_found("客户不存在，可能已被删除或从未存在。", back_url=url_for("customers.list_customers"))
        conn.execute("UPDATE customers SET name=?, phone=?, address=?, opening_balance_cents=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (request.form["name"], request.form.get("phone", ""), request.form.get("address", ""), opening_balance_cents, customer_id))
    return redirect(url_for("customers.list_customers"))

@customers_bp.post("/bulk_delete")
def bulk_delete_customers():
    ids = [int(x) for x in request.form.getlist("ids")]
    with get_db() as conn:
        conn.executemany("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP, delete_reason='批量删除' WHERE id=?", [(i,) for i in ids])
    return redirect(url_for("customers.list_customers"))


@customers_bp.post("/<int:customer_id>/delete")
def delete_customer(customer_id: int):
    with get_db() as conn:
        conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP, delete_reason='用户删除' WHERE id=?", (customer_id,))
    if request.headers.get("X-Requested-With") or "fetch" in request.headers.get("Sec-Fetch-Mode", ""):
        return Response(status=204)
    return redirect(url_for("customers.list_customers"))
