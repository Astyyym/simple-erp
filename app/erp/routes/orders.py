from flask import Blueprint, render_template, request, redirect, url_for, send_file, jsonify, Response
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import uuid
from erp.db import get_db
from erp.services.accounting import create_order_from_typed_rows, create_return_order_from_source, delete_order, mark_order_printed, update_return_draft_from_source, update_order_from_typed_rows, void_order
from erp.utils.exporting import (
    export_filename,
    order_line_export_headers,
    order_line_export_rows,
    workbook_download,
)
from erp.utils.errors import error_response, not_found
from erp.utils.audit import log_action
from erp.utils.filter_chips import order_filter_chips
from erp.utils.money import cents_to_yuan, line_subtotal_cents, micro_to_yuan, yuan_to_cents
from erp.utils.pdf import generate_order_pdf, send_pdf_for_preview

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")
MAX_SQLITE_INTEGER = (1 << 63) - 1

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
    # E-1b：补 9999 上限校验，避免溢出成 5 位破坏单号格式（对齐拿货侧 order_numbering）。
    if max_seq >= 9999:
        raise ValueError(f"{day} 的单号流水已达到9999，无法继续开单")
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


def _parse_month_field(value: str, default_year: int, default_month: int) -> tuple[int, int]:
    """解析「YYYY-MM」（<input type="month">）、裸月份或裸年份；其余回退默认年月。"""
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = int(text[:4]), int(text[5:7])
        if 1 <= year <= 9999 and 1 <= month <= 12:
            return year, month
    if re.fullmatch(r"\d{1,2}", text):          # 旧参数：只有月份
        return default_year, _parse_month(text, default_month)
    if re.fullmatch(r"\d{4}", text):            # 兼容：只有年份
        return _parse_year(text, default_year), default_month
    return default_year, default_month


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
    start_year, start_month = _parse_month_field(start_month_raw, _parse_year(start_year_raw or year_raw, today.year), _parse_month(month_raw, today.month))
    end_year, end_month = _parse_month_field(end_month_raw, _parse_year(end_year_raw or year_raw, start_year), start_month if start_month_raw or end_month_raw else _parse_month(month_raw, start_month))

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


def _filter_args_from_request():
    return {
        "customer_q": request.args.get("customer", "").strip(),
        "date_mode_raw": request.args.get("date_mode", "range").strip(),
        "year_raw": request.args.get("year", "").strip(),
        "month_raw": request.args.get("month", "").strip(),
        "start_year_raw": request.args.get("start_year", "").strip(),
        "start_month_raw": request.args.get("start_month_raw", request.args.get("start_month", "")).strip(),
        "end_year_raw": request.args.get("end_year", "").strip(),
        "end_month_raw": request.args.get("end_month_raw", request.args.get("end_month", "")).strip(),
        "start_date_raw": request.args.get("start_date", "").strip(),
        "end_date_raw": request.args.get("end_date", "").strip(),
        "order_type": request.args.get("order_type", "").strip(),
    }


@orders_bp.get("/")
def list_orders():
    args = _filter_args_from_request()
    customer_q = args["customer_q"]
    order_type = args["order_type"] if args["order_type"] in {"sale", "return", "purchase", "purchase_return"} else ""
    # Document-type filter is multi-select (all / some / one); legacy single value still works.
    order_types = [t for t in request.args.getlist("order_type") if t in {"sale", "return", "purchase", "purchase_return"}]
    if not order_types and order_type:
        order_types = [order_type]
    type_filter = set(order_types)
    # E2：状态多选筛选（空=全部）。销售/退货四态，拿货/退拿货三态（无 printed）。
    status_filter = {s for s in request.args.getlist("status") if s in {"draft", "saved", "printed", "void"}}

    with get_db() as conn:
        scope, customer_row, dashboard_notice, matched_names = _resolve_customer_scope(conn, customer_q)
        bound_conditions = ['1=1']
        bound_params = []
        if scope == 'customer':
            bound_conditions.append('customer_id=?')
            bound_params.append(customer_row['id'])
        if len(type_filter) == 1:
            bound_conditions.append('document_type=?')
            bound_params.append(next(iter(type_filter)))
        bounds = conn.execute(f"""SELECT MIN(business_date) AS first_date,MAX(business_date) AS last_date FROM (
            SELECT order_date AS business_date,customer_id,order_type AS document_type FROM orders WHERE deleted_at IS NULL
            UNION ALL SELECT business_date,customer_id,'purchase' FROM purchase_orders WHERE deleted_at IS NULL
            UNION ALL SELECT business_date,customer_id,'purchase_return' FROM purchase_return_orders WHERE deleted_at IS NULL
        ) WHERE {' AND '.join(bound_conditions)}""",bound_params).fetchone()

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

        def type_condition(column, document_type):
            if type_filter and document_type not in type_filter:
                return "1=0"
            return f"{column} IS NOT NULL"

        sale_condition = "1=1" if (not type_filter or "sale" in type_filter) else "1=0"
        return_condition = "1=1" if (not type_filter or "return" in type_filter) else "1=0"
        purchase_condition = "1=1" if (not type_filter or "purchase" in type_filter) else "1=0"
        purchase_return_condition = "1=1" if (not type_filter or "purchase_return" in type_filter) else "1=0"

        # E2：状态筛选按各表 status 直接匹配（销售/退货四态；拿货/退拿货无 printed，勾了也不匹配）。
        if status_filter:
            quoted = ", ".join(f"'{s}'" for s in sorted(status_filter))
            sale_condition += f" AND o.status IN ({quoted})"
            return_condition += f" AND o.status IN ({quoted})"
            purchase_condition += f" AND status IN ({quoted})"
            purchase_return_condition += f" AND status IN ({quoted})"

        def order_side_conditions():
            conditions = ["o.deleted_at IS NULL", "o.order_date >= ?", "o.order_date <= ?"]
            params = [start_date, end_date]
            if scope == "customer":
                conditions.append("o.customer_id=?")
                params.append(customer_row["id"])
            elif customer_q:
                conditions.append("c.name LIKE ?")
                params.append(f"%{customer_q}%")
            return " AND ".join(conditions), params

        def purchase_side_conditions():
            conditions = ["deleted_at IS NULL", "business_date >= ?", "business_date <= ?"]
            params = [start_date, end_date]
            if scope == "customer":
                conditions.append("customer_id=?")
                params.append(customer_row["id"])
            elif customer_q:
                conditions.append("customer_name LIKE ?")
                params.append(f"%{customer_q}%")
            return " AND ".join(conditions), params

        order_where, order_params = order_side_conditions()
        name_where, name_params = purchase_side_conditions()

        # orders stores no name snapshot: join customers for display and filtering.
        union_sql = f"""
            SELECT o.id, o.order_no, o.customer_id, COALESCE(c.name, '') AS customer_name,
                   o.order_date AS business_date, o.order_type, o.total_amount_cents, o.status,
                   {_postings_expr(('sale', 'return'), 'o.id')} AS has_postings
              FROM orders o LEFT JOIN customers c ON c.id = o.customer_id
             WHERE {order_where} AND o.order_type='sale' AND {sale_condition}
            UNION ALL
            SELECT o.id, o.order_no, o.customer_id, COALESCE(c.name, '') AS customer_name,
                   o.order_date AS business_date, o.order_type, o.total_amount_cents, o.status,
                   {_postings_expr(('sale', 'return'), 'o.id')} AS has_postings
              FROM orders o LEFT JOIN customers c ON c.id = o.customer_id
             WHERE {order_where} AND o.order_type='return' AND {return_condition}
            UNION ALL
            SELECT id, order_no, customer_id, customer_name, business_date,
                   'purchase' AS order_type, total_amount_cents, status,
                   {_postings_expr(('purchase',), 'id')} AS has_postings
              FROM purchase_orders WHERE {name_where} AND {purchase_condition}
            UNION ALL
            SELECT id, order_no, customer_id, customer_name, business_date,
                   'purchase_return' AS order_type, total_amount_cents, status,
                   {_postings_expr(('purchase_return',), 'id')} AS has_postings
              FROM purchase_return_orders WHERE {name_where} AND {purchase_return_condition}
        """
        union_params = [*order_params, *order_params, *name_params, *name_params]
        total_order_count = int(conn.execute(
            f"SELECT COUNT(*) AS total_count FROM ({union_sql})", union_params
        ).fetchone()["total_count"])
        page_size = 50
        total_pages = max(1, (total_order_count + page_size - 1) // page_size)
        page = request.args.get("page", 1, type=int) or 1
        page = min(max(page, 1), total_pages)
        offset = (page - 1) * page_size
        documents = conn.execute(
            f"SELECT * FROM ({union_sql}) ORDER BY business_date DESC, order_type, id DESC LIMIT ? OFFSET ?",
            [*union_params, page_size, offset],
        ).fetchall()
        documents = [
            dict(row) | {
                "type_label": {"sale": "销售单", "return": "退货单", "purchase": "拿货单", "purchase_return": "退拿货单"}[row["order_type"]],
                "type_badge": {"sale": "bg-primary", "return": "bg-danger", "purchase": "bg-warning text-dark", "purchase_return": "bg-info text-dark"}[row["order_type"]],
                "status_label": {
                    "draft": "草稿", "saved": "正式保存", "printed": "已打印", "void": "已作废",
                }.get(row["status"], row["status"]),
                "status_badge": {
                    "draft": "bg-secondary", "saved": "bg-success", "printed": "bg-info text-dark", "void": "bg-danger",
                }.get(row["status"], "bg-secondary"),
                # 全部单据都可勾选删除：正式单据删除时由后端先自动冲回（作废）再进回收站。
                "deletable": True,
                # B1：不可编辑的记录也渲染「编辑」按钮，但置灰 + title 说明原因。
                # 直接隐藏会让用户以为功能缺失；禁用态保留了「这里本来能改」的信息。
                "editable": _document_editable(row["order_type"], row["status"], bool(row["has_postings"])),
                "edit_block_reason": _document_edit_block_reason(row["order_type"], row["status"], bool(row["has_postings"])),
                "detail_url": {
                    "sale": f"/orders/{row['id']}", "return": f"/orders/{row['id']}",
                    "purchase": f"/purchases/{row['id']}", "purchase_return": f"/purchases/return/{row['id']}",
                }[row["order_type"]],
                "delete_url": {
                    "sale": f"/orders/{row['id']}/delete", "return": f"/orders/{row['id']}/delete",
                    "purchase": f"/purchases/{row['id']}/delete", "purchase_return": f"/purchases/return/{row['id']}/delete",
                }[row["order_type"]],
                "edit_url": {
                    "sale": f"/orders/{row['id']}/edit", "return": f"/orders/{row['id']}/edit",
                    "purchase": f"/purchases/{row['id']}/edit", "purchase_return": f"/purchases/return/{row['id']}/edit",
                }[row["order_type"]],
                "document_key": f"{row['order_type']}:{row['id']}",
            }
            for row in documents
        ]
        customer_suggestions = conn.execute(
            "SELECT DISTINCT name FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name LIMIT 20",
            (f"%{customer_q}%" if customer_q else "%",),
        ).fetchall()

    # E2 前置修复（复核 #17）：to_dict(flat=True) 对多值参数只保留第一个，
    # 导致多选 order_type/status 翻页时丢参数。改用多值字典逐个传给 url_for。
    pagination_query: dict = {}
    for key in request.args.keys():
        if key == "page":
            continue
        values = request.args.getlist(key)
        pagination_query[key] = values[0] if len(values) == 1 else values

    def page_url(target_page: int) -> str:
        return url_for("orders.list_orders", **{**pagination_query, "page": target_page})

    first_page = max(1, min(page - 1, total_pages - 2))
    last_page = min(total_pages, max(3, page + 1))
    pagination_pages = [
        {"number": page_number, "url": page_url(page_number)}
        for page_number in range(first_page, last_page + 1)
    ]
    previous_page_url = page_url(page - 1) if page > 1 else None
    next_page_url = page_url(page + 1) if page < total_pages else None
    range_start = (page - 1) * page_size + 1 if total_order_count else 0
    range_end = min(page * page_size, total_order_count)

    unique_customer = scope == "customer"
    summary_hint = None
    if not unique_customer:
        summary_hint = "当前未锁定唯一客户：导出汇总表将按筛选范围内有单据的客户分段打印（销售单与退货单都会进入汇总，与订单类型筛选无关）。"

    return render_template(
        "orders/list.html",
        documents=documents,
        total_order_count=total_order_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        range_start=range_start,
        range_end=range_end,
        pagination_pages=pagination_pages,
        previous_page_url=previous_page_url,
        next_page_url=next_page_url,
        customer_q=customer_q,
        start_date=start_date if date_mode == "range" else args["start_date_raw"],
        end_date=end_date if date_mode == "range" else args["end_date_raw"],
        filter_start_date=start_date,
        filter_end_date=end_date,
        order_type=order_type,
        order_types=sorted(type_filter),
        order_statuses=sorted(status_filter),
        date_mode=date_mode,
        filter_year=year,
        filter_month=month,
        filter_start_year=start_year,
        filter_start_month=start_month,
        filter_end_year=end_year,
        filter_end_month=end_month,
        matched_customer_names=matched_names,
        customer_suggestions=customer_suggestions,
        dashboard_notice=dashboard_notice,
        unique_customer=unique_customer,
        summary_hint=summary_hint,
        filter_chips=order_filter_chips(
            customer_q=customer_q,
            order_types=type_filter,
            order_statuses=status_filter,
        ),
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
        return error_response(
            "当前筛选范围内没有可导出的客户单据，请调整客户或日期范围后重试。",
            title="无可导出的汇总", back_url=url_for("orders.list_orders"),
        )
    try:
        path = generate_account_summary_pdf_for_customers(customer_ids, start_date, end_date)
    except ValueError as exc:
        return error_response(str(exc), title="汇总表未能生成", back_url=url_for("orders.list_orders"))
    return send_pdf_for_preview(path, "货款汇总表.pdf", "货款汇总表")


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


def _today_history(conn, today: str) -> list[dict]:
    rows = conn.execute(
        """SELECT o.id, o.order_no, o.order_date, o.order_type, o.status,
                  o.total_amount_cents, COALESCE(c.name, '（未知客户）') AS customer_name,
                  EXISTS(SELECT 1 FROM inventory_postings p WHERE p.source_type IN ('sale','return')
                         AND (p.source_id=CAST(o.id AS TEXT) OR p.source_id LIKE CAST(o.id AS TEXT) || ':%')) AS has_postings
           FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
           WHERE o.order_date=? AND o.deleted_at IS NULL
             AND o.status IN ('saved', 'printed')
             AND o.order_type IN ('sale', 'return')
           ORDER BY o.id DESC""",
        (today,),
    ).fetchall()
    return [
        {**dict(row), "amount_display": cents_to_yuan(row["total_amount_cents"]),
         "editable": _document_editable(row["order_type"], row["status"], bool(row["has_postings"])),
         "edit_block_reason": _document_edit_block_reason(row["order_type"], row["status"], bool(row["has_postings"]))}
        for row in rows
    ]


def _return_source_orders(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id, o.order_no, o.order_date, o.customer_id, c.name AS customer_name,
               o.total_amount_cents
        FROM orders o JOIN customers c ON c.id=o.customer_id
        WHERE o.order_type='sale' AND o.status IN ('saved', 'printed') AND o.deleted_at IS NULL
          AND EXISTS (
              SELECT 1 FROM order_items oi
              WHERE oi.order_id=o.id AND oi.unit_cost_micro IS NOT NULL AND oi.cost_total_micro IS NOT NULL
          )
        ORDER BY o.order_date DESC, o.id DESC
        LIMIT 100
        """
    ).fetchall()
    return [dict(row) for row in rows]


@orders_bp.get("/api/today_history")
def api_today_history():
    today = date.today().isoformat()
    with get_db() as conn:
        orders = _today_history(conn, today)
    return jsonify({"date": today, "orders": orders})


@orders_bp.get("/api/return_sources/<int:source_order_id>")
def api_return_source(source_order_id: int):
    with get_db() as conn:
        source = conn.execute(
            """
            SELECT o.id, o.order_no, o.order_date, o.customer_id, c.name AS customer_name,
                   o.total_amount_cents
            FROM orders o JOIN customers c ON c.id=o.customer_id
            WHERE o.id=? AND o.order_type='sale' AND o.status IN ('saved', 'printed')
              AND o.deleted_at IS NULL
            """,
            (source_order_id,),
        ).fetchone()
        if source is None:
            return jsonify({"message": "退货来源必须是有效销售单"}), 404
        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id=? ORDER BY id",
            (source_order_id,),
        ).fetchall()
        returned_rows = conn.execute(
            """
            SELECT oi.source_item_id, oi.quantity
            FROM order_items oi JOIN orders r ON r.id=oi.order_id
            WHERE r.source_order_id=? AND r.order_type='return'
              AND r.status IN ('saved', 'printed') AND r.deleted_at IS NULL
            """,
            (source_order_id,),
        ).fetchall()
    returned: dict[int, Decimal] = {}
    for row in returned_rows:
        if row["source_item_id"] is None:
            continue
        try:
            returned[int(row["source_item_id"])] = returned.get(int(row["source_item_id"]), Decimal("0")) + Decimal(str(row["quantity"]))
        except (InvalidOperation, TypeError, ValueError):
            continue
    payload_items = []
    for item in items:
        try:
            original_quantity = Decimal(str(item["quantity"]))
            returned_quantity = returned.get(int(item["id"]), Decimal("0"))
            available = max(Decimal("0"), original_quantity - returned_quantity)
        except (InvalidOperation, TypeError, ValueError):
            original_quantity = returned_quantity = available = Decimal("0")
        payload_items.append({
            "source_item_id": int(item["id"]),
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "spec": item["spec"] or "",
            "unit": item["unit"],
            "original_quantity": format(original_quantity, "f"),
            "returned_quantity": format(returned_quantity, "f"),
            "available_quantity": format(available, "f"),
            "unit_price": cents_to_yuan(item["unit_price_cents"]),
            "cost_available": item["unit_cost_micro"] is not None and item["cost_total_micro"] is not None,
            "unit_cost": micro_to_yuan(int((Decimal(int(item["cost_total_micro"])) / original_quantity).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), precision=6) if item["cost_total_micro"] is not None and original_quantity > 0 else None,
        })
    return jsonify({"source": dict(source), "items": payload_items})


def _new_order_form(order_type: str):
    today = date.today().isoformat()
    with get_db() as conn:
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY name").fetchall()
        products = conn.execute("SELECT * FROM products WHERE deleted_at IS NULL ORDER BY usage_count DESC, name").fetchall()
        order_no = next_order_no(conn, today)
        today_history = _today_history(conn, today)
        return_sources = _return_source_orders(conn) if order_type == "return" else []
    return render_template("orders/new.html", customers=customers, products=products, product_catalog=_product_catalog(products), today=today, order_no=order_no, request_key=uuid.uuid4().hex, order_type=order_type, today_history=today_history, return_sources=return_sources, cents_to_yuan=cents_to_yuan)


def _product_catalog(products) -> list[dict]:
    """Compact product list for the shared two-level picker (名称 → 型号)."""
    return [
        {"id": int(p["id"]), "name": p["name"], "spec": p["spec"] or "",
         "unit": p["unit"], "default_price_cents": int(p["default_price_cents"] or 0)}
        for p in products
    ]


def _resolve_customer_id(*, order_no: str = "") -> int:
    customer_name = request.form.get("customer_name", "").strip()
    customer_id_text = request.form.get("customer_id", "").strip()
    with get_db() as conn:
        if customer_id_text:
            try:
                customer_id = int(customer_id_text)
            except (TypeError, ValueError):
                raise ValueError("客户选择无效")
            existing = conn.execute(
                "SELECT id FROM customers WHERE id=? AND deleted_at IS NULL",
                (customer_id,),
            ).fetchone()
            if existing is None:
                raise ValueError("客户不存在，请重新选择客户")
            return customer_id
        if not customer_name:
            raise ValueError("客户名称不能为空")
        existing = conn.execute("SELECT id, deleted_at FROM customers WHERE name=?", (customer_name,)).fetchone()
        if existing:
            customer_id = int(existing["id"])
            if existing["deleted_at"] is not None:
                # G-9：命中回收站客户 → 复用并恢复可见性（与商品侧统一），并留痕。
                conn.execute(
                    "UPDATE customers SET deleted_at=NULL, delete_reason='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (customer_id,),
                )
                log_action(
                    "restore_customer_on_order",
                    "customer",
                    customer_id,
                    f"开单复用回收站客户档案并恢复可见性：{customer_name}",
                    conn=conn,
                )
            return customer_id
        cur = conn.execute(
            "INSERT INTO customers(name, origin) VALUES (?, 'order')",
            (customer_name,),
        )
        new_id = int(cur.lastrowid)
        # G-0b：自动建档必须留痕，摘要写明来源单据号。
        log_action(
            "auto_create_customer",
            "customer",
            new_id,
            f"开单自动建档客户：{customer_name}，来源 {order_no}" if order_no else f"开单自动建档客户：{customer_name}",
            conn=conn,
        )
        return new_id


def _typed_rows_from_form() -> list[dict]:
    product_ids = request.form.getlist("product_id")
    product_names = request.form.getlist("product_name")
    specs = request.form.getlist("spec")
    units = request.form.getlist("unit")
    unit_prices = request.form.getlist("unit_price")
    quantities = request.form.getlist("quantity")
    if len(product_ids) < len(product_names):
        product_ids.extend([""] * (len(product_names) - len(product_ids)))
    # G-3：明细行现在也提交 `spec`；旧调用方（无 spec 字段）补空串保持兼容。
    if len(specs) < len(product_names):
        specs.extend([""] * (len(product_names) - len(specs)))
    return [
        {"product_id": product_id, "product_name": name, "spec": spec, "unit": unit, "unit_price_yuan": price, "quantity": qty}
        for product_id, name, spec, unit, price, qty in zip(product_ids, product_names, specs, units, unit_prices, quantities)
        if name.strip() and qty.strip()
    ]


def _validate_typed_rows(rows: list[dict]) -> str | None:
    """Reject malformed detail values before a new customer can be created."""
    total_cents = 0
    for index, row in enumerate(rows, start=1):
        try:
            quantity = Decimal(str(row.get("quantity", "")))
            unit_price = Decimal(str(row.get("unit_price_yuan", "")))
        except (InvalidOperation, TypeError, ValueError):
            return f"第 {index} 行数量或单价不是有效数字"
        if not quantity.is_finite() or not unit_price.is_finite():
            return f"第 {index} 行数量或单价不是有效数字"
        if quantity <= 0:
            return f"第 {index} 行数量必须大于 0"
        if unit_price < 0:
            return f"第 {index} 行单价不能为负数"
        if quantity > MAX_SQLITE_INTEGER:
            return "金额或数量超出支持范围"
        try:
            unit_price_cents = yuan_to_cents(unit_price)
            subtotal_cents = line_subtotal_cents(quantity, unit_price_cents)
        except (InvalidOperation, OverflowError, TypeError, ValueError):
            return "金额或数量超出支持范围"
        if unit_price_cents > MAX_SQLITE_INTEGER or subtotal_cents > MAX_SQLITE_INTEGER:
            return "金额或数量超出支持范围"
        total_cents += subtotal_cents
        if total_cents > MAX_SQLITE_INTEGER:
            return "金额或数量超出支持范围"
    return None


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
    status = request.form.get("status", "saved").strip()
    if status not in {"draft", "saved"}:
        return error_response("订单状态无效，请选择“草稿”或“正式保存”。", title="订单未保存",
                              back_url=url_for("orders.new_order"))
    source_order_id_text = request.form.get("source_order_id", "").strip() if order_type == "return" else ""
    if source_order_id_text:
        source_item_ids = request.form.getlist("source_item_id")
        quantities = request.form.getlist("quantity")
        rows = [
            {"source_item_id": source_item_id, "quantity": quantity}
            for source_item_id, quantity in zip(source_item_ids, quantities)
            if str(source_item_id).strip() and str(quantity).strip()
        ]
        try:
            source_order_id = int(source_order_id_text)
        except ValueError:
            return error_response("退货来源销售单无效，请重新选择。", title="订单未保存",
                                  back_url=url_for("orders.new_return_order"))
        if not rows:
            return error_response("退货至少选择一行并填写数量。", title="订单未保存",
                                  back_url=url_for("orders.new_return_order"))
    else:
        rows = _typed_rows_from_form()
        detail_error = _validate_typed_rows(rows)
        if detail_error:
            return error_response(detail_error, title="订单未保存", back_url=url_for("orders.new_order"))
    order_date = _resolve_create_order_date()
    with get_db() as conn:
        # Never trust client-supplied order_no; number follows chosen business date.
        order_no = next_order_no(conn, order_date)
    try:
        # G-0b：开单自动建档的客户留痕，摘要带上来源单据号。
        customer_id = _resolve_customer_id(order_no=order_no)
    except ValueError as exc:
        return error_response(str(exc), title="订单未保存", back_url=url_for("orders.new_order"))
    try:
        if order_type == "return" and source_order_id_text:
            created_id = create_return_order_from_source(
                customer_id,
                order_no,
                source_order_id,
                rows,
                status=status,
                order_date=order_date,
                notes=request.form.get("notes", ""),
                request_key=request.form.get("request_key", ""),
            )
        else:
            created_id = create_order_from_typed_rows(
                customer_id,
                order_no,
                rows,
                status=status,
                order_date=order_date,
                notes=request.form.get("notes", ""),
                order_type=order_type,
                request_key=request.form.get("request_key", ""),
            )
    except ValueError as exc:
        return error_response(str(exc), title="订单未保存", back_url=url_for("orders.new_order"))
    order_id = int(created_id)
    # C1/C2：两个保存动作都走同一 fetch 流程；「保存并打印」在非草稿时自动标记已打印。
    save_action = request.form.get("save_action", "save")
    want_print = save_action == "save_print"
    printed = False
    if want_print and status == "saved":
        mark_order_printed(order_id)
        printed = True
    pdf_path = url_for("orders.order_pdf", order_id=order_id)
    if request.headers.get("X-Requested-With") == "fetch":
        next_url = url_for("orders.new_return_order" if order_type == "return" else "orders.new_order")
        return jsonify({
            "ok": True,
            "order_id": order_id,
            "order_no": order_no,
            "printed": printed,
            "pdf_url": request.host_url.rstrip("/") + pdf_path,
            "detail_url": url_for("orders.view_order", order_id=order_id),
            "next_url": next_url,
        })
    if want_print:
        return redirect(pdf_path, code=303)
    return redirect(url_for("orders.list_orders"))

def _load_sale_document(conn, order_id: int):
    """取销售/退货单；回收站中的单据（deleted_at 非空）一律按「不存在」处理。"""
    return conn.execute(
        "SELECT o.*, c.name AS customer_name, source.order_no AS source_order_no "
        "FROM orders o JOIN customers c ON c.id=o.customer_id "
        "LEFT JOIN orders source ON source.id=o.source_order_id "
        "WHERE o.id=? AND o.deleted_at IS NULL",
        (order_id,),
    ).fetchone()


def _postings_expr(source_types: tuple[str, ...], id_ref: str) -> str:
    """生成「该单据是否有库存流水」的 EXISTS 子查询（复用 update_order_from_typed_rows 的 source_id 口径）。"""
    types = ", ".join(f"'{t}'" for t in source_types)
    return (
        f"EXISTS(SELECT 1 FROM inventory_postings p WHERE p.source_type IN ({types}) "
        f"AND (p.source_id=CAST({id_ref} AS TEXT) OR p.source_id LIKE CAST({id_ref} AS TEXT) || ':%'))"
    )


def _document_editable(order_type: str, status: str, has_postings: bool) -> bool:
    """列表/详情/当天历史统一的「可重编辑」口径。

    销售/退货：未作废且无库存流水才可改（与 update_order_from_typed_rows 的过账校验一致；
    无库存商品的无流水单仍可编辑，不要按 status 一刀切）。拿货/退拿货：只有草稿可改。
    """
    if order_type in ("sale", "return"):
        return status != "void" and not has_postings
    return status == "draft"


def _document_edit_block_reason(order_type: str, status: str, has_postings: bool) -> str:
    """不可编辑时的原因文案；可编辑时返回空串。

    与 `_document_editable` 同一口径。列表/详情把「编辑」按钮始终渲染出来，
    不可编辑时显示为禁用态并用这段文案说明为什么点不了——比直接隐藏按钮更好，
    用户不会以为「这个功能没了」。
    """
    if _document_editable(order_type, status, has_postings):
        return ""
    if status == "void":
        return "已作废单据不能编辑，只能查看。"
    if order_type in ("sale", "return"):
        return "该单据库存已过账，经济字段不能再改；如需更正请先作废，再重新开单。"
    return "正式拿货单/退拿货单经济字段已锁定，只能查看。"


def _has_stock_postings(conn, order_id: int) -> bool:
    """销售/退货是否已有库存流水；有流水即不可再改经济字段（与 update_order_from_typed_rows 同口径）。"""
    return conn.execute(
        "SELECT 1 FROM inventory_postings WHERE source_type IN ('sale','return') AND (source_id=? OR source_id LIKE ?) LIMIT 1",
        (str(order_id), f"{order_id}:%"),
    ).fetchone() is not None


@orders_bp.get("/<int:order_id>")
def view_order(order_id: int):
    with get_db() as conn:
        order = _load_sale_document(conn, order_id)
        if order is None:
            return not_found("单据不存在，可能已被移入回收站或从未存在。", back_url=url_for("orders.list_orders"))
        items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
        # 原销售单可能已被删除（删除销售单不再被有效退货阻止），此时只展示单号快照、不做死链接。
        source_order_available = bool(order["source_order_id"]) and conn.execute(
            "SELECT 1 FROM orders WHERE id=? AND deleted_at IS NULL", (order["source_order_id"],)
        ).fetchone() is not None
        editable = _document_editable(order["order_type"], order["status"], _has_stock_postings(conn, order_id))
        edit_block_reason = _document_edit_block_reason(
            order["order_type"], order["status"], _has_stock_postings(conn, order_id)
        ) if not editable else ""
    known_cost = bool(items) and all(item["cost_total_micro"] is not None for item in items)
    if known_cost:
        cost_micro = sum(int(item["cost_total_micro"]) for item in items)
        cost_cents = int((Decimal(cost_micro) / Decimal("10000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        signed_cost_cents = -cost_cents if order["order_type"] == "return" else cost_cents
        profit_cents = int(order["total_amount_cents"]) - signed_cost_cents
    else:
        profit_cents = None
    return render_template("orders/detail.html", order=order, items=items, profit_cents=profit_cents, source_order_available=source_order_available, editable=editable, edit_block_reason=edit_block_reason, cents_to_yuan=cents_to_yuan, micro_to_yuan=micro_to_yuan)


@orders_bp.get("/<int:order_id>/edit")
def edit_order(order_id: int):
    """编辑入口。A1：不存在（含回收站中）→ 中文 404。B3：注定提交失败的记录直接给错误页。"""
    with get_db() as conn:
        order = _load_sale_document(conn, order_id)
        if order is None:
            return not_found("单据不存在，可能已被移入回收站或从未存在。", back_url=url_for("orders.list_orders"))
        items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
        products = conn.execute("SELECT * FROM products WHERE deleted_at IS NULL ORDER BY usage_count DESC, name").fetchall()
        if order["status"] == "void":
            return error_response("已作废单据不能重编辑，只能回看。", title="无法重编辑",
                                  back_url=url_for("orders.view_order", order_id=order_id))
        if _has_stock_postings(conn, order_id):
            return error_response("该单据库存已过账，经济字段不能再改。请用「回看」核对，或作废后重新开单。",
                                  title="无法重编辑", back_url=url_for("orders.view_order", order_id=order_id))
        if order["source_order_id"] is not None and order["status"] != "draft":
            return error_response("正式退货单经济字段已锁定，请在详情页修改备注。", title="无法重编辑",
                                  back_url=url_for("orders.view_order", order_id=order_id))
    return render_template("orders/new.html", order=order, items=items, products=products, product_catalog=_product_catalog(products), today=date.today().isoformat(), order_type=order["order_type"] if "order_type" in order.keys() else "sale", cents_to_yuan=cents_to_yuan, micro_to_yuan=micro_to_yuan)


@orders_bp.post("/<int:order_id>/edit")
def update_order_view(order_id: int):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT order_no, order_date, order_type, status, source_order_id FROM orders WHERE id=? AND deleted_at IS NULL",
            (order_id,),
        ).fetchone()
    if existing is None:
        return not_found("订单不存在，可能已被移入回收站或从未存在。", back_url=url_for("orders.list_orders"))
    if existing["status"] == "void":
        return error_response("已作废订单不能重编辑。", title="无法重编辑",
                              back_url=url_for("orders.view_order", order_id=order_id))
    status = request.form.get("status", "saved").strip()
    if status not in {"draft", "saved", "printed"}:
        return error_response("订单状态无效，请选择“草稿”“正式保存”或“已打印”。", title="无法重编辑",
                              back_url=url_for("orders.view_order", order_id=order_id))
    if existing["source_order_id"] is not None:
        if existing["status"] != "draft":
            return error_response("正式退货单经济字段已锁定，请在详情页修改备注。", title="无法重编辑",
                                  back_url=url_for("orders.view_order", order_id=order_id))
        source_item_ids = request.form.getlist("source_item_id")
        quantities = request.form.getlist("quantity")
        rows = [
            {"source_item_id": source_item_id, "quantity": quantity}
            for source_item_id, quantity in zip(source_item_ids, quantities)
            if str(source_item_id).strip() and str(quantity).strip()
        ]
        if not rows:
            return error_response("退货至少选择一行并填写数量。", title="无法重编辑",
                                  back_url=url_for("orders.view_order", order_id=order_id))
        if status == "printed":
            return error_response("退货单只能保存为草稿或正式保存。", title="无法重编辑",
                                  back_url=url_for("orders.view_order", order_id=order_id))
        try:
            update_return_draft_from_source(
                order_id,
                rows,
                status=status,
                order_date=existing["order_date"],
                notes=request.form.get("notes", ""),
                expected_version=request.form.get("version", type=int),
            )
        except ValueError as exc:
            return error_response(str(exc), title="无法重编辑",
                                  back_url=url_for("orders.view_order", order_id=order_id))
        return redirect(url_for("orders.view_order", order_id=order_id))
    rows = _typed_rows_from_form()
    detail_error = _validate_typed_rows(rows)
    if detail_error:
        return error_response(detail_error, title="无法重编辑",
                              back_url=url_for("orders.view_order", order_id=order_id))
    try:
        customer_id = _resolve_customer_id()
    except ValueError as exc:
        return error_response(str(exc), title="无法重编辑",
                              back_url=url_for("orders.view_order", order_id=order_id))
    # Re-edit keeps original order number/date/type; only business fields change.
    try:
        update_order_from_typed_rows(
            order_id,
            customer_id,
            existing["order_no"],
            rows,
            status=status,
            order_date=existing["order_date"],
            notes=request.form.get("notes", ""),
            order_type=existing["order_type"],
            expected_version=request.form.get("version", type=int),
        )
    except ValueError as exc:
        return error_response(str(exc), title="无法重编辑",
                              back_url=url_for("orders.view_order", order_id=order_id))
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
                   cp.price_cents AS customer_price_cents,
                   s.avg_cost_micro, s.enabled, s.quantity_3dp
            FROM products p
            LEFT JOIN customer_prices cp ON cp.product_id=p.id AND cp.customer_id=?
            LEFT JOIN product_inventory_state s ON s.product_id=p.id
            WHERE p.deleted_at IS NULL AND (p.name LIKE ? OR p.spec LIKE ? OR p.pinyin_initials LIKE ?)
            ORDER BY CASE WHEN cp.price_cents IS NULL THEN 1 ELSE 0 END, p.usage_count DESC, p.name
            LIMIT 20
            """,
            (customer_id, f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    return jsonify([
        {"id": row["id"], "name": row["name"], "spec": row["spec"], "unit": row["unit"], "unit_price": cents_to_yuan(row["effective_price_cents"]), "regular_price": cents_to_yuan(row["default_price_cents"]), "customer_price": cents_to_yuan(row["customer_price_cents"]) if row["customer_price_cents"] is not None else None, "is_customer_price": row["customer_price_cents"] is not None, "cost_available": bool(row["enabled"] and row["avg_cost_micro"] is not None and row["quantity_3dp"]), "unit_cost": micro_to_yuan(row["avg_cost_micro"], precision=6) if row["enabled"] and row["avg_cost_micro"] is not None and row["quantity_3dp"] else None}
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
    try:
        path = generate_order_pdf(order_id)
    except ValueError as exc:
        return not_found(str(exc), back_url=url_for("orders.list_orders"))
    return send_pdf_for_preview(path, f"{order_id}.pdf", "销售单 PDF")


@orders_bp.post("/<int:order_id>/void")
def void_order_view(order_id: int):
    try:
        void_order(order_id, request.form.get("reason", ""), request.form.get("version", type=int))
    except ValueError as exc:
        return error_response(str(exc), title="单据未能作废",
                              back_url=url_for("orders.view_order", order_id=order_id))
    return redirect(url_for("orders.view_order", order_id=order_id))

@orders_bp.post("/bulk_delete")
def bulk_delete_orders():
    """Delete selected documents across all four types.

    Live documents are reversed first (void → recycle bin) so the ledger and stock
    stay conserved; drafts go straight to the recycle bin. A document that cannot be
    reversed (not the last stock posting, has a live dependent return, would drive
    stock negative) is reported back instead of being skipped silently.
    """
    from erp.services.accounting import AUTO_REVERSE_REASON, void_then_delete_order
    from erp.services.inventory import void_then_delete_purchase_order
    from erp.services import purchase_returns as return_service

    raw_ids = request.form.getlist("ids")
    failures: list[str] = []
    for raw in raw_ids:
        kind, _, raw_id = str(raw).partition(":")
        try:
            if kind in {"sale", "return"}:
                void_then_delete_order(int(raw_id), AUTO_REVERSE_REASON)
            elif kind == "purchase":
                void_then_delete_purchase_order(int(raw_id), AUTO_REVERSE_REASON)
            elif kind == "purchase_return":
                with get_db() as conn:
                    order = conn.execute("SELECT version FROM purchase_return_orders WHERE id=? AND deleted_at IS NULL", (int(raw_id),)).fetchone()
                if order is None:
                    failures.append(f"退拿货单 {raw_id} 不存在")
                    continue
                return_service.void_then_delete(int(raw_id), expected_version=order["version"], reason=AUTO_REVERSE_REASON)
            else:
                failures.append(f"未知单据类型：{raw}")
        except ValueError as exc:
            failures.append(f"{raw}：{exc}")
    if failures:
        return error_response(
            "；".join(failures),
            title="部分单据未能删除",
            back_url=url_for("orders.list_orders"),
        )
    return redirect(url_for("orders.list_orders"))


@orders_bp.post("/<int:order_id>/delete")
def delete_order_view(order_id: int):
    """Row-level delete: same auto-reverse semantics as the bulk action."""
    from erp.services.accounting import AUTO_REVERSE_REASON, void_then_delete_order

    try:
        void_then_delete_order(order_id, AUTO_REVERSE_REASON)
    except ValueError as exc:
        return error_response(str(exc), title="单据未能删除", back_url=url_for("orders.list_orders"))
    if request.headers.get("X-Requested-With") == "fetch":
        return Response(status=204)
    return redirect(url_for("orders.list_orders"))
