from __future__ import annotations

from datetime import date, timedelta
import re

from flask import Blueprint, render_template, request, url_for

from erp.db import get_db
from erp.services.analytics import summarize_analytics
from erp.utils.filter_chips import analytics_filter_chips
from erp.utils.money import cents_to_yuan
from erp.utils.quantity import format_quantity_3dp

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


def _customer_name(customer_id: str) -> str:
    """客户 chip 展示用名称；找不到或未选时返回空串。"""
    if not customer_id:
        return ""
    try:
        key = int(customer_id)
    except (TypeError, ValueError):
        return ""
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM customers WHERE id=? AND deleted_at IS NULL", (key,)
        ).fetchone()
    return row["name"] if row else ""


def _products():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, name
            FROM products
            WHERE deleted_at IS NULL
            ORDER BY name COLLATE NOCASE, id
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
    v15（决策 ②/③）：毛利润榜与库存告急榜已随成本/库存下线删除，这里只剩数量榜。
    """
    shown = group_rows if size is None else group_rows[:size]

    def magnitude(row):
        return abs(int(row["net_sales_quantity_3dp"]))

    peak = max((magnitude(row) for row in shown), default=0)
    rows = []
    for row in shown:
        value = magnitude(row)
        # 全品类同一张榜，数量必须带单位，否则「1 米」和「1 个」无法区分。
        label = f'{format_quantity_3dp(row["net_sales_quantity_3dp"], row["unit"])} {row["unit"] or "未填写单位"}'
        rows.append({
            "product_id": row["product_id"],
            "label": row["product_name"],
            "value_text": label,
            "bar_percent": round(value * 100 / peak, 1) if peak else 0,
            # 柱高直接用像素：最高柱 = RANK_BAR_MAX_HEIGHT_PX，其余按同榜比例，标注贴在各自柱顶。
            "bar_height_px": round(value * RANK_BAR_MAX_HEIGHT_PX / peak, 1) if peak else 0,
            "is_negative": int(row["net_sales_quantity_3dp"]) < 0,
        })
    return rows


def _analysis_presentation(result):
    """Display state never changes the aggregation's historical filter scope."""
    sort = request.args.get("sort", "amount")
    tab = request.args.get("tab", "heat")
    # v15（决策 ②/③）：`profit` / `stock` 排序与 `health` 页签随成本/库存下线；
    # 排序白名单收窄为 `{amount}`，页签收窄为 `{heat, rank}`。
    if sort not in {"amount"} or tab not in {"heat", "rank"}:
        raise ValueError("分析展示条件不合法")
    rows = list(result["product_rows"])
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
            "rank_key": rank_key, "rank_size": raw_size}


@analytics_bp.get("/")
def analytics_center():
    today = date.today()
    date_mode = request.args.get("date_mode", "range").strip()
    if date_mode not in {"year", "month", "range"}:
        date_mode = "range"
    document_types = [item for item in request.args.getlist("document_type") if item.strip()]
    product_name = request.args.get("product_name", "").strip()
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
                   customer_id=customer_raw, product_name=product_name, metric=metric,
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
        filter_chips=analytics_filter_chips(
            customer_id=customer_raw,
            customer_name=_customer_name(customer_raw),
            product_name=product_name,
            document_types=document_types,
        ),
    ), (400 if error else 200)
