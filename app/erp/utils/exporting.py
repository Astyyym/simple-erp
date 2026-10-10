from __future__ import annotations

import io
from copy import copy
from datetime import date
from typing import Iterable, Sequence

from flask import send_file

from erp.utils.money import cents_to_yuan

# Deferred openpyxl seam: resolved on the first Excel export so app boot does not
# pay openpyxl's import cost. Kept as a module-level name (None) so existing
# callers/tests can still override `exporting.Workbook`.
Workbook = None


def _workbook():
    global Workbook
    if Workbook is None:
        from openpyxl import Workbook as _Workbook
        Workbook = _Workbook
    return Workbook

ORDER_TYPE_LABELS = {
    "sale": "销售单",
    "return": "退货单",
}

ORDER_STATUS_LABELS = {
    "draft": "草稿",
    "saved": "已保存",
    "printed": "已打印",
    "void": "作废",
}


def export_filename(prefix: str, day: date | None = None) -> str:
    stamp = (day or date.today()).strftime("%Y%m%d")
    return f"{prefix}_{stamp}.xlsx"


def workbook_download(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    sheet_title: str,
    filename: str,
):
    workbook = _workbook()()
    sheet = workbook.active
    sheet.title = sheet_title[:31] or "导出"
    sheet.append(list(headers))
    for cell in sheet[1]:
        font = copy(cell.font)
        font.bold = True
        cell.font = font
    for row in rows:
        sheet.append(list(row))
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        max_age=0,
    )


def customer_export_headers() -> list[str]:
    return ["客户名称", "电话", "地址", "期初余额（元）", "备注", "创建时间", "更新时间"]


def customer_export_rows(customers: Iterable) -> list[list[object]]:
    rows: list[list[object]] = []
    for customer in customers:
        rows.append(
            [
                customer["name"] or "",
                customer["phone"] or "",
                customer["address"] or "",
                cents_to_yuan(int(customer["opening_balance_cents"] or 0)),
                customer["notes"] or "",
                customer["created_at"] or "",
                customer["updated_at"] or "",
            ]
        )
    return rows


def product_export_headers() -> list[str]:
    return ["商品名称", "型号", "品牌", "单位", "默认价（元）", "备注", "使用次数", "创建时间", "更新时间"]


def product_export_rows(products: Iterable) -> list[list[object]]:
    rows: list[list[object]] = []
    for product in products:
        rows.append(
            [
                product["name"] or "",
                product["spec"] or "",
                (product["brand"] if "brand" in product.keys() else "") or "",
                product["unit"] or "",
                cents_to_yuan(int(product["default_price_cents"] or 0)),
                product["notes"] or "",
                int(product["usage_count"] or 0),
                product["created_at"] or "",
                product["updated_at"] or "",
            ]
        )
    return rows


def order_line_export_headers() -> list[str]:
    return [
        "单号",
        "日期",
        "客户",
        "单据类型",
        "状态",
        "品名",
        "规格",
        "单位",
        "数量",
        "单价（元）",
        "金额（元）",
        "单据备注",
        "单据合计（元）",
    ]


def order_line_export_rows(lines: Iterable) -> list[list[object]]:
    """One Excel row per order_item; header/total fields repeat on each detail line."""
    rows: list[list[object]] = []
    for line in lines:
        order_type = line["order_type"] if "order_type" in line.keys() else "sale"
        status = line["status"] if "status" in line.keys() else ""
        rows.append(
            [
                line["order_no"] or "",
                line["order_date"] or "",
                line["customer_name"] or "",
                ORDER_TYPE_LABELS.get(order_type, order_type or ""),
                ORDER_STATUS_LABELS.get(status, status or ""),
                line["product_name"] or "",
                line["spec"] or "",
                line["unit"] or "",
                line["quantity"] or "",
                cents_to_yuan(int(line["unit_price_cents"] or 0)),
                cents_to_yuan(int(line["subtotal_cents"] or 0)),
                line["notes"] or "",
                cents_to_yuan(int(line["total_amount_cents"] or 0)),
            ]
        )
    return rows
