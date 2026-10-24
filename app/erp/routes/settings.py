from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for, current_app

from erp.config import (
    UI_FONT_WEIGHT_CHOICES,
    UI_FONT_WEIGHT_LABELS,
    UI_SCALE_CHOICES,
    UI_THEME_CHOICES,
    data_root,
    snooze_first_run_prompt,
    default_data_root_display,
    ensure_runtime_dirs,
    load_config,
    location_file,
    migrate_data_root,
    normalize_slip_size,
    save_config,
)
from erp.db import CURRENT_SCHEMA_VERSION, init_db
from erp.utils.backup import (
    RestoreError,
    backup_dir,
    copy_backup_to,
    create_backup,
    inspect_backup,
    list_backups,
    read_backup_schema_version,
    restore_backup,
)
from erp.utils.quantity import format_quantity
from erp.utils.legacy_archive import archive_dir, list_archives, save_archive
from erp.mobile_server import mobile_url, port_in_use, start_mobile_listener, status as mobile_status, stop_mobile_listener
from erp.services import mobile_pairing
from erp.utils.mobile_format import timestamp_label
from erp.utils.mobile_qr import qr_png_data_uri
from erp.utils.pdf import (
    generate_settings_print_preview_pdf,
    generate_settings_reconciliation_preview_pdf,
    generate_settings_summary_preview_pdf,
    render_desktop_preview,
    send_pdf_for_preview,
    wants_desktop_preview,
)
from typing import Any

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


def _reload_app_config() -> dict:
    cfg = load_config()
    current_app.config["ERP_CONFIG"] = cfg
    return cfg


def _settings_template_context(**extra) -> dict:
    """设置页模板上下文。`extra` 用来就地渲染一次性的结果（如手机配对码）。

    `mobile_notice` 走 session 一次性传递，而不是 flash：手机访问这几个按钮在页面**下半部**，
    flash 渲染在页面顶部，用户点了按钮只会看到「页面刷新了、什么也没出现」。
    """
    config = load_config()
    context = {
        "config": config,
        "data_root_path": str(data_root()),
        "default_data_root_path": default_data_root_display(),
        "location_file_path": str(location_file()),
        "ui_scale_choices": UI_SCALE_CHOICES,
        "ui_theme_choices": UI_THEME_CHOICES,
        "ui_font_weight_choices": UI_FONT_WEIGHT_CHOICES,
        "ui_font_weight_labels": UI_FONT_WEIGHT_LABELS,
        "is_desktop": bool(os.environ.get("ERP_DESKTOP") or os.environ.get("ERP_DESKTOP_SHELL")),
        "backups": list_backups(),
        "backup_retention_days": config.get("backup_retention_days", 30),
        "archives": list_archives(),
        "mobile": mobile_panel_context(),
        "mobile_notice": session.pop("mobile_notice", None),
        "mobile_code": "",
        "mobile_code_seconds": 0,
    }
    context.update(extra)
    return context


@settings_bp.get("/")
def settings_page():
    return render_template("settings/index.html", **_settings_template_context())


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
        # 界面说明文字开关：复选框未勾选时浏览器根本不提交该字段，所以不能像
        # print_offset_* 那样只在「字段在场」时更新——这里按「在场即开、缺席即关」解析。
        updates["ui_show_hints"] = form.get("ui_show_hints") in ("1", "on", "true")
        # Only touch calibration when the settings form actually posts these fields.
        # Older POSTs / partial tests must not wipe live printer offsets back to 0.
        if "print_offset_x_mm" in form:
            updates["print_offset_x_mm"] = _parse_print_mm(form.get("print_offset_x_mm"), "横向偏移")
        if "print_offset_y_mm" in form:
            updates["print_offset_y_mm"] = _parse_print_mm(form.get("print_offset_y_mm"), "纵向偏移")
        if "print_scale" in form:
            updates["print_scale"] = _parse_print_scale(form.get("print_scale"))
        # P6：每联打印行数（6/8/10/12）。非法或越界一律回落默认 10，不抛错、不写坏配置。
        if "slip_size" in form:
            updates["slip_size"] = normalize_slip_size(form.get("slip_size"))
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


@settings_bp.post("/first-run/save")
def save_first_run():
    """首次使用引导弹窗的「保存并开始使用」。

    **不能复用 `/settings/save`**：那个路由把表单里缺失的字段一律写成空串
    （`print_main_business`、`print_legal_note`、`ui_theme` …），
    而弹窗只提交公司名称与两项打印信息 —— 走通用保存会把主营业务、
    法律句、主题、缩放等配置**全部清空**。这里只写自己负责的三个键。

    公司名称填成出厂默认值时不算完成引导：弹窗会继续按「稍后再说」的
    冷却规则出现，避免用户点一下保存却等于什么都没设。
    """
    form = request.form
    shop_name = (form.get("shop_name") or "").strip()
    if not shop_name:
        flash("公司名称不能为空。", "danger")
        return redirect(url_for("index"))
    try:
        save_config({
            "shop_name": shop_name,
            "print_order_phone": (form.get("print_order_phone") or "").strip(),
            "print_order_address": (form.get("print_order_address") or "").strip(),
        })
        _reload_app_config()
        flash("已保存，打印单会带上这些信息。", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"保存失败：{exc}", "danger")
    return redirect(url_for("index"))


@settings_bp.post("/first-run/snooze")
def snooze_first_run():
    """首次使用引导弹窗的「稍后再说」：推迟 7 天，下次启动不再立刻弹。

    2026-10-21 用户要求：允许稍后再说，但要冷却，否则公司名一直没改的话
    每次开程序都弹一次，反而变成骚扰。
    """
    try:
        save_config({"first_run_prompt_snoozed_until": snooze_first_run_prompt()})
        _reload_app_config()
        flash("好的，7 天后如果公司名称还没设置，会再提醒一次。", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"操作失败：{exc}", "danger")
    # 弹窗从工作台（应用根路由 index）发起，回跳到那里继续使用。
    return redirect(url_for("index"))


@settings_bp.post("/first-run/dismiss")
def dismiss_first_run():
    """首次使用引导弹窗的「不再提示」：彻底关掉，不再自动出现。

    用户以后想重新看到引导，可在设置页的「打印单信息」卡片里手动重新打开。
    """
    try:
        save_config({"first_run_prompt_dismissed": True})
        _reload_app_config()
        flash("已关闭首次使用引导。以后可在「系统设置 → 打印单信息」重新打开。", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"操作失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#printInfoSection")


@settings_bp.get("/print-preview.pdf")
def print_preview_pdf():
    """Render the Settings sample through the normal PDF preview/save workflow."""
    if wants_desktop_preview():
        # 样张不依赖任何业务数据，预览页无需先生成 PDF（避免一次点击渲染两遍）。
        return render_desktop_preview("打印样张.pdf", "打印样张 PDF")
    path = generate_settings_print_preview_pdf()
    return send_pdf_for_preview(path, "打印样张.pdf", "打印样张 PDF")


# 另外两个版面的样张（2026-10-15）：同样是固定示例数据，不读业务库。
# 桌面壳里「预览 PDF」走统一预览页（返回/保存），浏览器直接返回真实 PDF。

@settings_bp.get("/print-preview/summary.pdf")
def summary_print_preview_pdf():
    if wants_desktop_preview():
        return render_desktop_preview("货款汇总样张.pdf", "货款汇总样张 PDF")
    path = generate_settings_summary_preview_pdf()
    return send_pdf_for_preview(path, "货款汇总样张.pdf", "货款汇总样张 PDF")


@settings_bp.get("/print-preview/reconciliation.pdf")
def reconciliation_print_preview_pdf():
    if wants_desktop_preview():
        return render_desktop_preview("对账单样张.pdf", "对账单样张 PDF")
    path = generate_settings_reconciliation_preview_pdf()
    return send_pdf_for_preview(path, "对账单样张.pdf", "对账单样张 PDF")


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


@settings_bp.post("/purge-all-data")
def purge_all_data_view():
    """清空所有数据（保留 backups/ 与 legacy_archive/），需要确认词「清空」。

    顺序固定：先自动备份（安全网）→ 校验确认词 → 单事务清库 → 回出厂配置 →
    清理运行目录 → 写留痕日志。备份失败或确认词不对都不执行清空。
    """
    from erp.utils.reset import PurgeError, purge_all_data, write_purge_trace

    confirm_text = (request.form.get("confirm_text") or "").strip()
    if confirm_text != "清空":
        flash("清空已取消：确认词必须输入「清空」两个字。", "danger")
        return redirect(url_for("settings.settings_page") + "#purgeSection")

    try:
        # 安全网：清空前强制自动备份（复用现成实现，含完整性检查）。
        backup_path = create_backup("pre_purge")
        result = purge_all_data()
        write_purge_trace(result)
        cleared_rows = sum(result["cleared_counts"].values())
        warnings = result.get("directory_warnings") or []
        message = (
            f"已清空所有业务数据（共 {cleared_rows} 行），并恢复出厂设置。"
            f"清空前的数据已备份为 {backup_path.name}，可在「备份与恢复」中找回；"
            "备份包与旧账留底存档未删除。请完全退出程序后重新打开。"
        )
        if warnings:
            message += "（部分文件被占用：" + "；".join(warnings) + "）"
        flash(message, "success")
    except PurgeError as exc:
        flash(f"清空失败（数据未被改动）：{exc}", "danger")
    except Exception as exc:  # noqa: BLE001 — surface as Chinese UI error
        flash(f"清空失败（数据未被改动）：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#purgeSection")


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


@settings_bp.post("/backup/delete")
def backup_delete():
    """删除一份备份 zip（2026-10-24 用户要求：另存 / 恢复 / 删除三个独立按钮）。

    与「清空数据」「恢复」不同，这里**不自动再备份一份**：删除的对象就是备份本身，
    再备份一份等于什么都没删。所以只做两件事：校验文件名在 backups/ 内、删掉它。
    删除不可撤销，界面上的按钮必须自己带二次确认（模板里已有 confirm）。
    """
    source = _backup_path_from_form()
    if source is None:
        flash("找不到要删除的备份文件", "danger")
        return redirect(url_for("settings.settings_page") + "#backupSection")
    name = source.name
    try:
        source.unlink()
        flash(f"已删除备份：{name}", "success")
    except OSError as exc:
        flash(f"删除备份失败：{exc}", "danger")
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
        message = (
            f"已从备份恢复{note}：{stamp}。恢复前已自动备份当前数据库，"
            "请完全退出程序后重新打开，确保所有页面读取新数据。"
        )
        # 旧版结构备份：重开后 init_db 会执行不可逆升级（v15 物理删表删列），必须说清。
        backup_version = read_backup_schema_version(source)
        if backup_version is None or backup_version < CURRENT_SCHEMA_VERSION:
            shown = f"v{backup_version}" if backup_version else "更早版本"
            message += (
                f" 注意：这份备份是 {shown} 旧版结构，重新打开程序时会自动升级到 "
                f"v{CURRENT_SCHEMA_VERSION}，库存与成本数据将被永久删除、无法回退。"
            )
        flash(message, "success")
    except RestoreError as exc:
        flash(f"恢复失败：{exc}", "danger")
    except Exception as exc:  # noqa: BLE001
        flash(f"恢复失败：{exc}", "danger")
    return redirect(url_for("settings.settings_page") + "#backupSection")


# ---------------------------------------------------------------------------
# 手机只读端（第二监听 5001）：开关 / 配对码 / 撤销设备 / 诊断
# ---------------------------------------------------------------------------
# 三条不可动口径：
#   1) 主应用 5000 永远只绑 127.0.0.1；手机端走**独立 app**（erp/mobile_app.py）。
#   2) 端口固定 5001：被占就明确报错并把开关回弹到关，**不自动漂移**
#      ——端口是配对 base_url 的一部分，漂移的失败是静默的。
#   3) 手机端没有任何写端：解绑只能在这里做。


def mobile_panel_context() -> dict:
    """设置页「手机访问」分区要展示的一切（不含任何业务数据）。"""
    status = mobile_status()
    devices = mobile_pairing.list_devices()
    for device in devices:
        device["paired_label"] = timestamp_label(device.get("paired_at", ""))
        device["last_seen_label"] = timestamp_label(device.get("last_seen_at", ""))
    enabled = bool(load_config().get("mobile_access_enabled"))
    reachable = bool(status["listening"]) and port_in_use(status["port"])
    return {
        "status": status,
        "devices": devices,
        "pending": mobile_pairing.pending_code_info(),
        "enabled": enabled,
        "reachable": reachable,
        "last_success_label": timestamp_label(mobile_pairing.last_success_at()),
        # 二维码只在「真的在监听 + 地址看起来手机能连」时才给：给一个 127 或代理网卡
        # 地址的二维码，等于让店主扫一张永远连不上的图。
        #
        # 二维码内容是 `http://<ip>:<port>/m/`，两种用法都成立：
        # - 手机 App 扫 → 解析出主机地址，直接进配对页（App 侧会去掉路径）
        # - 手机浏览器扫 → 打开配对页，可以走浏览器只读端
        #
        # 2026-10-21 试过改成 `/m/download`（扫码直接下 APK），**实测走不通**：
        # Chrome 对「HTTP 明文 + 可执行文件」有两道强制拦截
        # （File can't be downloaded securely → File might be harmful），
        # 两次都确认后文件仍停在 `.pending-*.apk` 临时态，装不上。
        # 局域网没有 HTTPS，这条路没有绕法，所以退回「传文件安装 + 扫码配对」。
        "qr": (
            qr_png_data_uri(status["url"])
            if status["listening"] and status.get("lan_usable")
            else ""
        ),
        "url": mobile_url(status["port"], ip=status["lan_ip"]),
    }


@settings_bp.post("/mobile/toggle")
def mobile_toggle():
    enable = (request.form.get("enabled") or "") == "1"
    try:
        if enable:
            ok, error = start_mobile_listener()
            if not ok:
                # 起不来就把开关回弹到关，别让配置说「开着」而实际没监听。
                save_config({"mobile_access_enabled": False})
                session["mobile_notice"] = ("danger", error)
            else:
                save_config({"mobile_access_enabled": True})
                session["mobile_notice"] = (
                    "success",
                    "手机访问已打开。用手机相机或 Chrome 扫下面的二维码，再点「生成配对码」。",
                )
        else:
            stop_mobile_listener()
            save_config({"mobile_access_enabled": False})
            session["mobile_notice"] = (
                "success",
                "手机访问已关闭（已配对的设备不会自动撤销，需要时可在下面单独撤销）。",
            )
        _reload_app_config()
    except Exception as exc:  # noqa: BLE001 — 界面给中文原因
        session["mobile_notice"] = ("danger", f"操作失败：{exc}")
    return redirect(url_for("settings.settings_page") + "#mobileAccessSection")


@settings_bp.get("/mobile/pair-code")
def mobile_pair_code_state():
    """当前配对码状态（JSON），给设置页倒计时与「换一个」用。

    为什么要这个接口：配对码是**服务端生成、120 秒过期**的东西，而设置页是
    服务端渲染的静态 HTML。没有它就只能靠整页刷新 —— 用户一刷新码就没了
    （上一版的真实表现），倒计时更是根本不存在（`seconds_left` 只在渲染那一刻算一次，
    之后永远显示同一个数字）。

    返回明文码：这是**本机单用户**的桌面程序，同一个 HTTP 端口只服务本机浏览器，
    明文在这里不构成额外暴露面；磁盘上仍然只有 sha256。
    """
    status = mobile_status()
    if not status["listening"]:
        return jsonify({"ok": True, "listening": False, "code": "", "seconds_left": 0, "ttl": 0})
    code, seconds_left = mobile_pairing.current_plaintext_code()
    return jsonify(
        {
            "ok": True,
            "listening": True,
            "code": code,
            "seconds_left": seconds_left,
            "ttl": mobile_pairing.PAIR_CODE_TTL_SECONDS,
        }
    )


@settings_bp.post("/mobile/pair-code")
def mobile_pair_code():
    """生成配对码。

    两种响应形态，同一个地址：
    - 前端带 `Accept: application/json`（或 `X-Requested-With`）→ 返回 JSON，
      页面不重载，倒计时从 120 秒开始走；
    - 普通表单直提交（禁用了 JS 的极端情况）→ 整页重渲染，码仍在正文里。

    为什么保留后者：这是唯一的兜底路径。JS 挂了还能配对，比「按钮点了没反应」强。
    """
    wants_json = (
        request.headers.get("X-Requested-With") == "fetch"
        or "application/json" in (request.headers.get("Accept") or "")
    )

    def _json(payload: dict, status: int = 200):
        return jsonify(payload), status

    if not mobile_status()["listening"]:
        message = "请先打开「手机访问」开关，再生成配对码。"
        if wants_json:
            return _json({"ok": False, "error": message}, 409)
        session["mobile_notice"] = ("danger", message)
        return redirect(url_for("settings.settings_page") + "#mobileAccessSection")

    code, error = mobile_pairing.generate_pair_code()
    if error:
        if wants_json:
            return _json({"ok": False, "error": error}, 429)
        session["mobile_notice"] = ("danger", error)
        return redirect(url_for("settings.settings_page") + "#mobileAccessSection")

    if wants_json:
        return _json(
            {
                "ok": True,
                "code": code,
                "seconds_left": mobile_pairing.PAIR_CODE_TTL_SECONDS,
                "ttl": mobile_pairing.PAIR_CODE_TTL_SECONDS,
            }
        )
    # 不带 JS 的老路径：就地渲染，不 redirect（码必须出现在用户正在看的这一屏上）。
    return render_template(
        "settings/index.html",
        **_settings_template_context(
            mobile_code=code,
            mobile_code_seconds=mobile_pairing.PAIR_CODE_TTL_SECONDS,
        ),
    )


@settings_bp.post("/mobile/revoke")
def mobile_revoke():
    device_id = (request.form.get("device_id") or "").strip()
    if mobile_pairing.revoke_device(device_id):
        session["mobile_notice"] = (
            "success",
            "已撤销这台手机：它下次请求会回到配对页（阶段一手机端不落本地数据）。",
        )
    else:
        session["mobile_notice"] = ("danger", "没找到这台设备，可能已经被撤销。")
    return redirect(url_for("settings.settings_page") + "#mobileAccessSection")


@settings_bp.get("/mobile/devices")
def mobile_devices():
    """已配对设备列表（JSON），给设置页轮询用。

    为什么需要这个接口：设置页是服务端渲染的，HTML 只在打开时生成一次。
    店主在电脑上开着设置页、拿手机扫码配对 —— 配对成功了，但那个页面不会自己变，
    「已配对设备 0 台」一直挂着，看起来像配对失败（2026-10-22 用户实测报这个）。
    这里返回当前设备列表 + 诊断时间，前端每几秒拉一次，变了就局部更新。
    """
    devices = mobile_pairing.list_devices()
    return jsonify(
        {
            "ok": True,
            "count": len(devices),
            "devices": devices,
            "last_success_label": timestamp_label(mobile_pairing.last_success_at()),
        }
    )


@settings_bp.post("/mobile/revoke-all")
def mobile_revoke_all():
    count = mobile_pairing.revoke_all_devices()
    session["mobile_notice"] = ("success", f"已撤销全部 {count} 台手机。要再连回来需要重新配对。")
    return redirect(url_for("settings.settings_page") + "#mobileAccessSection")
