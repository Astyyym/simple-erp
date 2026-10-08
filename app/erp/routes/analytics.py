from __future__ import annotations

from datetime import date, timedelta
import re

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from erp.db import get_db
from erp.services.analytics import summarize_analytics
from erp.utils.errors import error_response, not_found, RecordNotFound
from erp.utils.money import cents_to_yuan
from erp.utils.quantity import format_quantity_3dp
from erp.services.reconciliation import (
    create_reconciliation_snapshot,
    get_reconciliation_snapshot,
    get_reconciliation_order_detail,
    toggle_reconciliation_selection,
    update_reconciliation_selection,
)
from erp.utils.pdf import generate_reconciliation_pdf, send_pdf_for_preview

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")


def _month_start(today: date) -> str:
    return today.replace(day=1).isoformat()


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _parse_year(value: str, default: int) -> int:
    try:
        year = int(str(value).strip())
        if 1 <= year <= 9999:
            return year
    except (TypeError, ValueError):
        pass
    return default


def _parse_month(value: str, default: int) -> int:
    try:
        month = int(str(value).strip())
        if 1 <= month <= 12:
            return month
    except (TypeError, ValueError):
        pass
    return default


def _parse_month_field(value: str, default_year: int, default_month: int) -> tuple[int, int]:
    """Parse a month field that may be YYYY-MM (<input type="month">) or a bare month."""
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = int(text[:4]), int(text[5:7])
        if 1 <= month <= 12:
            return year, month
    return default_year, _parse_month(text, default_month)


def _customers():
    with get_db() as conn:
        return conn.execute(
            "SELECT id, name FROM customers WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE, id"
        ).fetchall()


def _products():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, name, COALESCE(spec, '') AS spec
            FROM products
            WHERE deleted_at IS NULL
            ORDER BY name COLLATE NOCASE, COALESCE(spec, ''), id
            """
        ).fetchall()


def _month_calendars(cells):
    """Group the in-scope days into Monday-first month calendars.

    Only the whole applied scope is rendered: the calendar never has its own
    year selector, so every month inside the filter range appears at once.
    """
    grouped: dict[str, list] = {}
    for cell in cells:
        grouped.setdefault(cell["date"][:7], []).append(cell)
    calendars = []
    for month in sorted(grouped):
        day_cells = sorted(grouped[month], key=lambda cell: cell["date"])
        first = date.fromisoformat(day_cells[0]["date"])
        calendars.append({
            "month": month,
            "label": f"{first.year} 年 {first.month} 月",
            "leading": first.weekday(),
            "cells": [dict(cell) | {"day": int(cell["date"][8:10])} for cell in day_cells],
        })
    return calendars


RANK_BAR_MAX_HEIGHT_PX = 190


def _rank_bar_rows(key, group_rows, size):
    """Turn one ranking group into bar-chart rows (value label + relative size).

    Bars are scaled to the largest absolute value inside the same chart so a
    chart never mixes scales; every row still carries its exact label, and
    quantity labels keep their unit because one chart now spans all products.
    """
    shown = group_rows if size is None else group_rows[:size]
    def magnitude(row):
        if key in ("hot_sales", "low_sales", "net_returns"):
            return abs(int(row["net_sales_quantity_3dp"]))
        if key == "gross_profit":
            return abs(int(row["gross_profit_cents"] or 0))
        if key == "inventory_alert":
            return abs(int(row["current_quantity_3dp"] or 0))
        return 0
    peak = max((magnitude(row) for row in shown), default=0)
    rows = []
    for row in shown:
        value = magnitude(row)
        if key in ("hot_sales", "low_sales", "net_returns"):
            # 全品类同一张榜，数量必须带单位，否则「1 米」和「1 个」无法区分。
            label = f'{format_quantity_3dp(row["net_sales_quantity_3dp"], row["unit"])} {row["unit"] or "未填写单位"}'
            negative = int(row["net_sales_quantity_3dp"]) < 0
        elif key == "gross_profit":
            label = f"¥{cents_to_yuan(row['gross_profit_cents'])}"
            negative = int(row["gross_profit_cents"] or 0) < 0
        elif key == "inventory_alert":
            label = f"{format_quantity_3dp(row['current_quantity_3dp'], row['unit'])} · {row['inventory_status']}"
            negative = False
        else:
            label = row["inventory_status"]
            negative = False
        rows.append({
            "product_id": row["product_id"],
            "label": f"{row['product_name']} · {row['spec'] or '未填写型号'}",
            "value_text": label,
            "bar_percent": round(value * 100 / peak, 1) if peak else 0,
            # 柱高直接用像素：最高柱 = RANK_BAR_MAX_HEIGHT_PX，其余按同榜比例，标注贴在各自柱顶。
            "bar_height_px": round(value * RANK_BAR_MAX_HEIGHT_PX / peak, 1) if peak else 0,
            "is_negative": negative,
        })
    return rows


def _cost_diagnostics(result, limit=10):
    """Visualisation data for the 成本诊断 tab.

    Three layers, in order of decision value:
    1. cost coverage — how much net sales has a historical cost behind it;
    2. profit composition — net sales minus historical cost equals known profit;
    3. which products contribute the uncovered net sales, so the gap is actionable.
    """
    summary = result["summary"]
    known = int(summary["known_net_sales_cents"] or 0)
    unknown = int(summary["unknown_net_sales_cents"] or 0)
    total = known + unknown
    net_cost = int(summary["net_cost_cents"] or 0)
    profit = int(summary["known_gross_profit_cents"] or 0)
    composition_total = known or 1
    missing = []
    for row in result["product_rows"]:
        gap = int(row["unknown_net_sales_cents"] or 0)
        if gap > 0:
            missing.append({
                "product_id": row["product_id"],
                "label": f"{row['product_name']} · {row['spec'] or '未填写型号'}",
                "value_text": f"¥{cents_to_yuan(gap)} · 缺 {row['unknown_line_count']} 行",
                "amount_cents": gap,
            })
    missing.sort(key=lambda item: (-item["amount_cents"], item["product_id"]))
    missing = missing[:limit]
    peak = max((item["amount_cents"] for item in missing), default=0)
    for item in missing:
        item["bar_percent"] = round(item["amount_cents"] * 100 / peak, 1) if peak else 0
    return {
        "has_sales": total > 0,
        "known_cents": known,
        "unknown_cents": unknown,
        "known_percent": round(known * 100 / total, 1) if total else 0,
        "unknown_percent": round(unknown * 100 / total, 1) if total else 0,
        "net_cost_cents": net_cost,
        "profit_cents": profit,
        "cost_percent": round(net_cost * 100 / composition_total, 1) if known else 0,
        "profit_percent": round(max(profit, 0) * 100 / composition_total, 1) if known else 0,
        "margin_percent": round(profit * 100 / known, 1) if known > 0 else None,
        "missing": missing,
        "missing_total_count": sum(1 for row in result["product_rows"] if int(row["unknown_net_sales_cents"] or 0) > 0),
    }


def _analysis_presentation(result):
    """Display state never changes the aggregation's historical filter scope."""
    sort = request.args.get("sort", "amount")
    tab = request.args.get("tab", "heat")
    if sort not in {"amount", "profit", "stock"} or tab not in {"heat", "rank", "health"}:
        raise ValueError("分析展示条件不合法")
    rows = list(result["product_rows"])
    if sort == "profit":
        rows.sort(key=lambda row: (not row["cost_complete"], -(row["net_sales_amount_cents"] * 10000 - row["net_cost_micro"]) if row["cost_complete"] else 0, row["product_id"]))
    elif sort == "stock":
        rows.sort(key=lambda row: ({"缺货": 0, "库存告急": 1, "正常": 2, "未知": 3, "未启用": 4}[row["inventory_status"]], row["product_id"]))
    selected_day = request.args.get("day", "")
    detail = next((cell for cell in result["heatmap"] if cell["date"] == selected_day), None)
    if selected_day and detail is None:
        raise ValueError("所选日期不在当前筛选范围内")
    rank_key = request.args.get("rank_key", "hot_sales")
    raw_size = request.args.get("rank_size", "10")
    if rank_key not in result["ranking_groups"] or raw_size not in {"10", "20", "50", "all"}:
        raise ValueError("排行展示条件不合法")
    size = None if raw_size == "all" else int(raw_size)
    rank_groups = {}
    for key, source_groups in result["ranking_groups"].items():
        rank_groups[key] = []
        for group in source_groups:
            shown = group["rows"] if size is None else group["rows"][:size]
            rank_groups[key].append({
                "unit": group["unit"], "unit_key": group["unit"] or "",
                "rows": _rank_bar_rows(key, group["rows"], size),
                "total_count": len(group["rows"]), "shown_count": len(shown),
            })
    return {"sort": sort, "tab": tab,
            "product_rows": rows, "heatmap": result["heatmap"],
            "calendars": _month_calendars(result["heatmap"]),
            "day": selected_day, "day_detail": detail, "rank_groups": rank_groups,
            "rank_key": rank_key, "rank_size": raw_size,
            "cost": _cost_diagnostics(result)}


@analytics_bp.get("/")
def analytics_center():
    today = date.today()
    date_mode = request.args.get("date_mode", "range").strip()
    if date_mode not in {"year", "month", "range"}:
        date_mode = "range"
    document_types = [item for item in request.args.getlist("document_type") if item.strip()]
    product_name = request.args.get("product_name", "").strip()
    spec = request.args.get("spec", "").strip()
    metric = request.args.get("metric", "amount").strip() or "amount"
    customer_raw = request.args.get("customer_id", "").strip()
    year_raw = request.args.get("year", "").strip()
    start_year_raw = request.args.get("start_year", "").strip()
    # 模板用 <input type="month" name="start_month_raw"> 提交 YYYY-MM；
    # 同时兼容 start_month 这个只带月份的旧参数。
    start_month_raw = (request.args.get("start_month_raw", "") or request.args.get("start_month", "")).strip()
    end_year_raw = request.args.get("end_year", "").strip()
    end_month_raw = (request.args.get("end_month_raw", "") or request.args.get("end_month", "")).strip()
    raw_start = request.args.get("start_date", "").strip()
    raw_end = request.args.get("end_date", "").strip()
    # 缺省（参数缺失）才回落到本月；显式空日期表示不设边界。
    opened = "start_date" in request.args or "end_date" in request.args
    if not opened and not year_raw and not start_year_raw and date_mode == "range":
        requested_start, requested_end = _month_start(today), today.isoformat()
    else:
        requested_start, requested_end = raw_start, raw_end
    if date_mode == "year":
        year = _parse_year(year_raw, today.year)
        requested_start, requested_end = f"{year:04d}-01-01", f"{year:04d}-12-31"
    elif date_mode == "month":
        start_year, start_month = _parse_month_field(start_month_raw or year_raw, _parse_year(start_year_raw or year_raw, today.year), today.month)
        end_year, end_month = _parse_month_field(end_month_raw or start_month_raw or year_raw, start_year, start_month)
        start_day = date(start_year, start_month, 1)
        end_day = _month_end(end_year, end_month)
        if start_day > end_day:
            start_day, end_day = date(end_year, end_month, 1), _month_end(start_year, start_month)
            start_year, start_month, end_year, end_month = end_year, end_month, start_year, start_month
        requested_start, requested_end = start_day.isoformat(), end_day.isoformat()
    error = ""
    result = None
    view = None
    filters = dict(start_date=requested_start, end_date=requested_end,
                   customer_id=customer_raw, product_name=product_name, spec=spec, metric=metric,
                   document_types=document_types)
    try:
        result = summarize_analytics(**filters)
        filters = result["filters"]
        view = _analysis_presentation(result)
    except (TypeError, ValueError) as exc:
        error = str(exc)
        result = None
    def view_url(**updates):
        state = {**filters, **({key: view[key] for key in ("sort", "tab", "day", "rank_key", "rank_size")} if view else {})}
        state.update(updates)
        return url_for("analytics.analytics_center", **state)
    # 时间模式面板的回显值（按年/按月/按日各自一套）
    month_start_year, month_start_month = _parse_month_field(
        start_month_raw or year_raw,
        _parse_year(start_year_raw or year_raw, today.year),
        today.month,
    )
    month_end_year, month_end_month = _parse_month_field(
        end_month_raw or start_month_raw or year_raw, month_start_year, month_start_month
    )
    return render_template(
        "analytics/index.html",
        customers=_customers(),
        products=[dict(row) for row in _products()],
        filters=filters,
        result=result,
        error=error,
        view=view,
        view_url=view_url,
        cents_to_yuan=cents_to_yuan,
        date_mode=date_mode,
        year_value=_parse_year(year_raw, today.year),
        start_year_value=month_start_year,
        start_month_value=month_start_month,
        end_year_value=month_end_year,
        end_month_value=month_end_month,
        selected_document_types=set(document_types),
    ), (400 if error else 200)


@analytics_bp.get("/reconciliation")
def reconciliation():
    customers = _customers()
    customer_id = request.args.get("customer_id", type=int)
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
    display_rows = []
    total_pages = 0
    if snapshot:
        total_pages = max((snapshot["scope_count"] + page_size - 1) // page_size, 1)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        display_rows = snapshot["rows"][start : start + page_size]
    # D2：返回路径按来源切换——从账款页进入 → 返回账款管理（携带原客户/日期）；默认返回数据分析。
    from_accounts = (request.args.get("from", "").strip() == "accounts")
    if from_accounts and customer_id:
        recon_back_url = url_for("accounts.accounts", customer_id=customer_id, start_date=start_date, end_date=end_date)
        recon_back_label = "返回账款管理"
    else:
        recon_back_url = url_for("analytics.analytics_center")
        recon_back_label = "返回数据分析"
    return render_template(
        "analytics/reconciliation.html",
        customers=customers,
        snapshot=snapshot,
        error=error,
        customer_id=customer_id,
        start_date=start_date,
        end_date=end_date,
        order_type_filter=order_type_filter,
        cents_to_yuan=cents_to_yuan,
        display_rows=display_rows,
        page=page,
        total_pages=total_pages,
        recon_back_url=recon_back_url,
        recon_back_label=recon_back_label,
        recon_source="accounts" if from_accounts else "analytics",
        return_url=(
            request.full_path.removesuffix("?")
            if request.args.get("snapshot_id", type=int) == snapshot_id and request.args.get("page", 1, type=int) == page
            else url_for("analytics.reconciliation", snapshot_id=snapshot_id, page=page)
        ) if snapshot else "",
    ), (400 if error else 200)


@analytics_bp.post("/reconciliation/select/<int:snapshot_id>")
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
        return error_response(str(exc), title="对账选择未保存", back_url=url_for("analytics.reconciliation"))
    return redirect(url_for(
        "analytics.reconciliation",
        customer_id=snapshot["customer_id"],
        start_date=snapshot["start_date"],
        end_date=snapshot["end_date"],
        order_type=snapshot["order_type_filter"],
        snapshot_id=snapshot_id,
        page=max(request.form.get("page", 1, type=int), 1),
    ))


@analytics_bp.post("/reconciliation/toggle/<int:snapshot_id>/<int:order_id>")
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


@analytics_bp.post("/reconciliation/toggle/<int:snapshot_id>/document/<string:document_key>")
def reconciliation_document_toggle(snapshot_id: int, document_key: str):
    try:
        toggle_reconciliation_selection(snapshot_id, document_key, request.form.get("selected", "0") == "1")
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@analytics_bp.get("/reconciliation/<int:snapshot_id>/document/<string:order_id>")
def reconciliation_document_detail(snapshot_id: int, order_id: str):
    return reconciliation_order_detail(snapshot_id, order_id)


@analytics_bp.get("/reconciliation/<int:snapshot_id>/order/<int:order_id>")
def reconciliation_order_detail(snapshot_id: int, order_id: int):
    try:
        detail = get_reconciliation_order_detail(snapshot_id, order_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    if detail is None:
        return jsonify({"error": "单据不在当前对账查询范围内"}), 404
    return jsonify(detail)


@analytics_bp.get("/reconciliation/pdf/<int:snapshot_id>")
def reconciliation_pdf(snapshot_id: int):
    try:
        path = generate_reconciliation_pdf(snapshot_id)
    except RecordNotFound as exc:
        return not_found(str(exc), title="对账查询不存在", back_url=url_for("analytics.reconciliation"))
    except ValueError as exc:
        return error_response(str(exc), title="对账单未能生成", back_url=url_for("analytics.reconciliation"))
    return send_pdf_for_preview(path, "往来交易对账单.pdf", "往来交易对账单")
