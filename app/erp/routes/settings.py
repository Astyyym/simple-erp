from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, Response

from erp.config import (
    UI_FONT_WEIGHT_CHOICES,
    UI_FONT_WEIGHT_LABELS,
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
from erp.utils.backup import (
    RestoreError,
    backup_dir,
    copy_backup_to,
    create_backup,
    inspect_backup,
    list_backups,
    restore_backup,
)
from erp.utils.money import cents_to_yuan
from erp.utils.legacy_archive import archive_dir, list_archives, save_archive
from erp.utils.pdf import (
    generate_settings_print_preview_pdf,
    send_pdf_for_preview,
    settings_print_preview_context,
)
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
        ui_font_weight_choices=UI_FONT_WEIGHT_CHOICES,
        ui_font_weight_labels=UI_FONT_WEIGHT_LABELS,
        is_desktop=is_desktop,
        backups=list_backups(),
        backup_retention_days=config.get("backup_retention_days", 30),
        archives=list_archives(),
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
            "ui_font_weight": (form.get("ui_font_weight") or "standard").strip().lower(),
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


@settings_bp.post("/print-info/dismiss")
def dismiss_print_info_prompt():
    """E-5：用户明确「不再提示」后，工作台引导卡不再出现。

    只记一个布尔开关，不改任何打印字段；用户以后想再看到它，可从设置页的
    「打印单信息」卡片里重新打开提示。
    """
    try:
        save_config({"print_info_prompt_dismissed": True})
        _reload_app_config()
        flash("已关闭打印信息提示。补全后可到「打印单信息」重新打开。", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"操作失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#printInfoSection")


@settings_bp.post("/print-info/restore")
def restore_print_info_prompt():
    """E-5：把已关闭的提示重新打开（与 dismiss 对称，避免用户关掉后找不回来）。"""
    try:
        save_config({"print_info_prompt_dismissed": False})
        _reload_app_config()
        flash("已重新打开打印信息提示。", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"操作失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#printInfoSection")


@settings_bp.get("/print-preview")
def print_preview():
    """Sample print HTML — does not read or write business orders."""
    order, items, config = settings_print_preview_context()
    html = render_template(
        "orders/print_template.html",
        order=order,
        items=items,
        config=config,
        cents_to_yuan=cents_to_yuan,
    )
    return Response(html, mimetype="text/html; charset=utf-8")


@settings_bp.get("/print-preview.pdf")
def print_preview_pdf():
    """Render the Settings sample through the normal PDF preview/save workflow."""
    path = generate_settings_print_preview_pdf()
    return send_pdf_for_preview(path, "打印样张.pdf", "打印样张 PDF")


# ---------------------------------------------------------------------------
# Batch F：旧账留底存档（只存文件，不进业务库）
# ---------------------------------------------------------------------------


@settings_bp.post("/legacy-archive/upload")
def legacy_archive_upload():
    try:
        entry = save_archive(request.files.get("file"), note=request.form.get("note", ""))
        flash(f"已存档（仅留底，不参与账款计算）：{entry['name']}", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"存档失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#legacyArchiveSection")


@settings_bp.get("/legacy-archive/<path:name>")
def legacy_archive_download(name: str):
    from flask import send_file

    target = (archive_dir() / name).resolve()
    try:
        target.relative_to(archive_dir().resolve())
    except ValueError:
        return error_response("存档文件不存在。", 404, title="文件不存在")
    if not target.is_file():
        return error_response("存档文件不存在。", 404, title="文件不存在")
    return send_file(target, as_attachment=True, download_name=target.name)


# ---------------------------------------------------------------------------
# Batch A：备份与恢复
# ---------------------------------------------------------------------------


def _backup_path_from_form(field: str = "backup_name") -> Path | None:
    """Resolve a posted backup file name inside the backups dir (no traversal)."""
    name = (request.form.get(field) or "").strip()
    if not name:
        return None
    candidate = (backup_dir() / name).resolve()
    try:
        candidate.relative_to(backup_dir().resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


@settings_bp.post("/backup/create")
def backup_create():
    try:
        path = create_backup("manual")
        flash(f"已创建备份：{path.name}", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"备份失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#backupSection")


@settings_bp.post("/backup/copy")
def backup_copy():
    source = _backup_path_from_form()
    destination = (request.form.get("destination_dir") or "").strip()
    if source is None:
        flash("找不到要另存的备份文件", "danger")
        return redirect(url_for("settings.settings_page") + "#backupSection")
    try:
        target = copy_backup_to(source, destination)
        flash(f"备份已另存到：{target}", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"另存失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#backupSection")


@settings_bp.post("/backup/restore")
def backup_restore():
    source = _backup_path_from_form()
    if source is None:
        flash("找不到要恢复的备份文件", "danger")
        return redirect(url_for("settings.settings_page") + "#backupSection")
    # A-4：二次确认词 + 明确告知将覆盖的备份时间。
    confirm_text = (request.form.get("confirm_text") or "").strip()
    if confirm_text != "恢复":
        flash("恢复已取消：确认词必须输入「恢复」两个字", "danger")
        return redirect(url_for("settings.settings_page") + "#backupSection")
    include_config = request.form.get("include_config") == "1"
    try:
        info = inspect_backup(source)
        result = restore_backup(source, include_config=include_config)
        stamp = info.get("created_at_display", source.name)
        note = "（含配置文件）" if result["config_restored"] else "（未改动配置文件）"
        flash(
            f"已从备份恢复{note}：{stamp}。恢复前已自动备份当前数据库，"
            "请完全退出程序后重新打开，确保所有页面读取新数据。",
            "success",
        )
    except RestoreError as exc:
        flash(f"恢复失败：{exc}", "danger")
    except Exception as exc:  # noqa: BLE001
        flash(f"恢复失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#backupSection")
