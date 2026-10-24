from decimal import InvalidOperation
from uuid import uuid4

from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify
from erp.db import get_db
from erp.services.accounting import create_customer
from erp.services.master_data_import import (
    CUSTOMER_HEADERS,
    CUSTOMER_TEMPLATE_NOTES,
    build_customer_preview,
    customer_totals,
    ImportPreview,
)
from erp.services.master_data_quality import COMPLETION_ORIGINS_SQL
from erp.utils.exporting import (
    customer_export_headers,
    customer_export_rows,
    export_filename,
    workbook_download,
)
from erp.utils.errors import error_response, not_found
from erp.utils.importing import ImportFileError, ImportResult, excel_template, normalized_name, read_upload_detailed
from erp.utils.money import yuan_to_cents, cents_to_yuan
from erp.utils.pinyin import name_like_pattern, name_match_clause, name_match_params, pinyin_fields as build_pinyin_fields
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
    return excel_template(list(CUSTOMER_HEADERS[1]), "客户导入模板.xlsx", CUSTOMER_TEMPLATE_NOTES)


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


def _customer_balance_map(conn, customer_ids: list[int]) -> dict[int, int]:
    """一次算出这批客户的**当前欠款**（分），口径与账款管理页 / 手机端完全一致。

    正数 = 客户欠我（应收），负数 = 我欠客户（多付/预付）。

    为什么必须与 `services/accounting.py:get_customer_account_ledger()` 逐项同源：
    客户列表这一列现在显示「欠款」而不是「期初」，如果这里换一套加减法，
    同一个客户在客户管理页和账款管理页会给出两个数 —— 那比不显示更糟。
    加减项：期初 + 销售/退货（saved/printed 且未删）+ 调整（active）
    − 收款（active）− 拿货（saved 且未删）+ 退拿货（saved 且未删）。
    """
    ids = sorted({int(value) for value in customer_ids})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT c.id AS customer_id,
               COALESCE(c.opening_balance_cents, 0)
             + COALESCE((SELECT SUM(o.total_amount_cents) FROM orders o
                          WHERE o.customer_id = c.id AND o.status IN ('saved','printed')
                            AND o.deleted_at IS NULL), 0)
             + COALESCE((SELECT SUM(a.amount_cents) FROM adjustments a
                          WHERE a.customer_id = c.id AND a.status='active'), 0)
             - COALESCE((SELECT SUM(p.amount_cents) FROM payments p
                          WHERE p.customer_id = c.id AND p.status='active'), 0)
             - COALESCE((SELECT SUM(po.total_amount_cents) FROM purchase_orders po
                          WHERE po.customer_id = c.id AND po.status='saved'
                            AND po.deleted_at IS NULL), 0)
             + COALESCE((SELECT SUM(pr.total_amount_cents) FROM purchase_return_orders pr
                          WHERE pr.customer_id = c.id AND pr.status='saved'
                            AND pr.deleted_at IS NULL), 0) AS balance_cents
          FROM customers c
         WHERE c.id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    return {int(row["customer_id"]): int(row["balance_cents"] or 0) for row in rows}


def _customer_list_context(conn, q: str = "", quality: str = "", sort: str = "", order: str = ""):
    # 待补全口径（2026-10-14 第二轮）：来源 = 开单攒出 + 批量导入，且电话与地址都为空。
    # 必须与 services/master_data_quality 同源，别在这里另写一套条件。
    todo_sql = (
        f" AND origin IN {COMPLETION_ORIGINS_SQL}"
        " AND TRIM(COALESCE(phone, ''))='' AND TRIM(COALESCE(address, ''))=''"
        if quality == "todo"
        else ""
    )
    # 排序（2026-10-24 用户要求，与手机端客户页同构）：
    #   sort=""（默认）/ "name" = 按名称（NOCASE），维持历史行为；
    #   sort="pinyin" = 按拼音 A→Z，空拼音始终垫底（不跟方向翻转）；
    #   sort="debt"   = 按当前欠款（多→少为正序），需要先算余额再在 Python 里排。
    # order="desc" 才反转；默认 asc —— 与手机端「只在倒序时传 order」同一约定。
    descending = order == "desc"
    direction = "DESC" if descending else "ASC"
    if sort == "pinyin":
        order_by = (
            "ORDER BY (CASE WHEN COALESCE(pinyin_full, '') = '' THEN 1 ELSE 0 END), "
            f"pinyin_full COLLATE NOCASE {direction}, name COLLATE NOCASE ASC, id ASC"
        )
    else:
        order_by = "ORDER BY name COLLATE NOCASE ASC, id ASC"
    # 按欠款排序时不能靠 SQL 的 LIMIT 截断（顺序还没算出来），先取全量再排再截。
    fetch_limit = "" if sort == "debt" else " LIMIT 200"
    if q:
        customers = conn.execute(
            f"SELECT * FROM customers WHERE deleted_at IS NULL AND ({name_match_clause()} OR phone LIKE ? OR address LIKE ?)"
            + todo_sql
            + f" {order_by}{fetch_limit}",
            (*name_match_params(q), name_like_pattern(q), name_like_pattern(q)),
        ).fetchall()
    else:
        customers = conn.execute(
            "SELECT * FROM customers WHERE deleted_at IS NULL"
            + todo_sql
            + f" {order_by}{fetch_limit}"
        ).fetchall()
    # 2026-10-24：列表把「期初」换成「欠款」（用户要求，同手机端）。
    # 一次批量 SQL 算好，模板只负责上色（正数绿=别人欠我，负数红=我欠别人）。
    balances = _customer_balance_map(conn, [row["id"] for row in customers])
    records = []
    for row in customers:
        record = dict(row)
        record["balance_cents"] = balances.get(int(row["id"]), 0)
        records.append(record)
    if sort == "debt":
        # 欠款正序 = 多→少（柜台最常问「谁欠得多」）；倒序 = 少→多。
        records.sort(key=lambda item: (-item["balance_cents"], item["name"]), reverse=descending)
        truncated = len(records) > 200
        records = records[:200]
    else:
        truncated = len(records) >= 200
    customers = records
    suggestions = conn.execute(
        f"SELECT DISTINCT name FROM customers WHERE deleted_at IS NULL AND {name_match_clause()} ORDER BY name LIMIT 20",
        (*name_match_params(q),),
    ).fetchall()
    todo_count = conn.execute(
        "SELECT COUNT(*) AS c FROM customers WHERE deleted_at IS NULL"
        f" AND origin IN {COMPLETION_ORIGINS_SQL}"
        " AND TRIM(COALESCE(phone, ''))='' AND TRIM(COALESCE(address, ''))=''"
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
            sort="",
            order="",
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
                initials, full = build_pinyin_fields(name)
                conn.execute(
                    "INSERT INTO customers(name, phone, address, opening_balance_cents, pinyin_initials, pinyin_full, origin) VALUES (?, ?, ?, ?, ?, ?, 'import')",
                    (name, row.payload.get("phone", ""), row.payload.get("address", ""),
                     int(row.payload.get("opening_balance_cents", 0)), initials, full),
                )
                existing_names.add(name)
                result.added += 1
                if row.completion:
                    # 字段不完整的新增行：列表里已经带上「待补全」徽标，摘要里如实报数。
                    result.pending_completion += 1
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
        sort="",
        order="",
        cents_to_yuan=cents_to_yuan,
        q="",
        import_result=result,
        **_backup_hint(),
    )


@customers_bp.get("/")
def list_customers():
    q = request.args.get("q", "").strip()
    # 档案筛选与排序都只接受白名单取值（不把任意串带进查询）。
    quality = request.args.get("quality", "").strip()
    if quality not in ("", "todo"):
        quality = ""
    sort = request.args.get("sort", "").strip()
    if sort not in ("", "pinyin", "debt"):
        sort = ""
    # 正倒序：只有显式 desc 才反转，默认保持各排序的历史方向（与手机端同一约定）。
    order = request.args.get("order", "").strip()
    if order not in ("", "asc", "desc"):
        order = ""
    with get_db() as conn:
        customers, suggestions, truncated, todo_count = _customer_list_context(conn, q, quality, sort, order)
    return render_template(
        "customers/list.html",
        customers=customers,
        suggestions=suggestions,
        truncated=truncated,
        todo_count=todo_count,
        quality=quality,
        sort=sort,
        order=order,
        cents_to_yuan=cents_to_yuan,
        q=q,
        **_backup_hint(),
    )

@customers_bp.get("/api/suggestions")
def customer_suggestions():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT name FROM customers WHERE deleted_at IS NULL AND {name_match_clause()} ORDER BY name LIMIT 20",
            (*name_match_params(q),),
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
        initials, full = build_pinyin_fields(request.form["name"])
        conn.execute("UPDATE customers SET name=?, phone=?, address=?, opening_balance_cents=?, pinyin_initials=?, pinyin_full=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (request.form["name"], request.form.get("phone", ""), request.form.get("address", ""), opening_balance_cents, initials, full, customer_id))
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
