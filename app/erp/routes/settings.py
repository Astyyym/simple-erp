from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, Response

from erp.config import (
    UI_SCALE_CHOICES,
    UI_THEME_CHOICES,
    data_root,
    default_data_root_display,
    ensure_runtime_dirs,
    load_config,
    location_file,
    migrate_data_root,
    save_config,
)
from erp.db import init_db
from erp.utils.money import cents_to_yuan
from typing import Any

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


def _reload_app_config() -> dict:
    cfg = load_config()
    current_app.config["ERP_CONFIG"] = cfg
    return cfg


@settings_bp.get("/")
def settings_page():
    config = load_config()
    current = str(data_root())
    is_desktop = bool(os.environ.get("ERP_DESKTOP") or os.environ.get("ERP_DESKTOP_SHELL"))
    return render_template(
        "settings/index.html",
        config=config,
        data_root_path=current,
        default_data_root_path=default_data_root_display(),
        location_file_path=str(location_file()),
        ui_scale_choices=UI_SCALE_CHOICES,
        ui_theme_choices=UI_THEME_CHOICES,
        is_desktop=is_desktop,
    )


def _parse_print_mm(raw: str | None, field_label: str) -> float:
    text = (raw or "").strip()
    if text == "":
        return 0.0
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"{field_label}必须是数字（毫米）") from exc
    if value < -50 or value > 50:
        raise ValueError(f"{field_label}建议在 -50～50 毫米之间")
    return value


def _parse_print_scale(raw: str | None) -> float:
    text = (raw or "").strip()
    if text == "":
        return 1.0
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError("打印缩放必须是数字，例如 1.0") from exc
    if value < 0.80 or value > 1.20:
        raise ValueError("打印缩放建议在 0.80～1.20 之间")
    return value


@settings_bp.post("/save")
def save_settings():
    form = request.form
    next_url = (form.get("next") or "").strip()
    try:
        updates: dict[str, Any] = {
            "shop_name": (form.get("shop_name") or "").strip(),
            "ui_theme": (form.get("ui_theme") or "light").strip().lower(),
            "ui_scale": (form.get("ui_scale") or "100").strip().replace("%", ""),
            "print_order_phone": (form.get("print_order_phone") or "").strip(),
            "print_order_address": (form.get("print_order_address") or "").strip(),
            "print_main_business": (form.get("print_main_business") or "").strip(),
            "print_legal_note": (form.get("print_legal_note") or "").strip(),
            "print_maker_name": (form.get("print_maker_name") or "").strip(),
            "print_receiver_label": (form.get("print_receiver_label") or "").strip(),
        }
        # Only touch calibration when the settings form actually posts these fields.
        # Older POSTs / partial tests must not wipe live printer offsets back to 0.
        if "print_offset_x_mm" in form:
            updates["print_offset_x_mm"] = _parse_print_mm(form.get("print_offset_x_mm"), "横向偏移")
        if "print_offset_y_mm" in form:
            updates["print_offset_y_mm"] = _parse_print_mm(form.get("print_offset_y_mm"), "纵向偏移")
        if "print_scale" in form:
            updates["print_scale"] = _parse_print_scale(form.get("print_scale"))
        if not updates["shop_name"]:
            raise ValueError("公司名称不能为空")
        # app_name synced inside save_config
        save_config(updates)
        _reload_app_config()
        flash("设置已保存", "success")
        # Only allow in-app relative redirects after save-and-leave.
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
    except Exception as exc:  # noqa: BLE001 — surface as Chinese UI error
        flash(f"保存失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page"))


@settings_bp.post("/migrate-data")
def migrate_data():
    new_path = (request.form.get("new_data_root") or "").strip()
    if not new_path:
        flash("请填写或选择新的数据存储目录", "danger")
        return redirect(url_for("settings.settings_page"))
    try:
        target = migrate_data_root(new_path)
        ensure_runtime_dirs(target)
        init_db()
        _reload_app_config()
        flash(
            f"数据已迁移并切换到：{target}。旧目录仍保留，可手动删除。"
            "若列表或开单异常，请完全退出程序后重新打开。",
            "success",
        )
    except Exception as exc:  # noqa: BLE001
        flash(f"迁移失败（仍使用原目录）：{exc}", "danger")
    return redirect(url_for("settings.settings_page"))


@settings_bp.get("/print-preview")
def print_preview():
    """Sample print HTML — does not read or write business orders."""
    config = load_config()
    order = {
        "order_no": "MD202607120001",
        "order_date": "2026-07-12",
        "customer_name": "预览客户（示例）",
        "total_amount_cents": 15000,
        "notes": "",
    }
    items = [
        {
            "product_name": "示例闸阀",
            "spec": "DN50",
            "unit": "只",
            "quantity": "2",
            "unit_price_cents": 5000,
            "subtotal_cents": 10000,
        },
        {
            "product_name": "示例蝶阀",
            "spec": "DN80",
            "unit": "只",
            "quantity": "1",
            "unit_price_cents": 5000,
            "subtotal_cents": 5000,
        },
    ]
    html = render_template(
        "orders/print_template.html",
        order=order,
        items=items,
        config=config,
        cents_to_yuan=cents_to_yuan,
    )
    return Response(html, mimetype="text/html; charset=utf-8")
