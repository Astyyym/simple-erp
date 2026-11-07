"""资料文档库（2-A）：文件存储、格式校验、预览与打开/打印。

定位：**纯文件柜**，不关联客户/商品；存检验报告 / 合格证 / 资质证书等。

存储约定：
- 原件落 `data_root/documents/`，预览图落 `data_root/documents/previews/`；
- `stored_name` = 时间戳 + 随机码 + 规范化扩展名（绝不用原名落盘，防穿越/重名）；
- `original_name` 只存库，用于显示与下载名；
- 上传流式写临时文件，成功后 `os.replace` 到正式名；中途失败清理半成品，不留孤儿文件。
"""
from __future__ import annotations

import mimetypes
import os
import secrets
from datetime import datetime
from pathlib import Path

from erp import config as config_module
from erp.db import get_db


class DocumentError(ValueError):
    """资料文档库的业务错误（界面直接展示中文原因）。"""


IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "avif", "tif", "tiff", "heic", "heif"}
DOCUMENT_EXTS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "rtf"}
# 明确拒收：SVG 可携带脚本；可执行/脚本一律不收。
FORBIDDEN_EXTS = {
    "svg", "exe", "bat", "cmd", "ps1", "js", "vbs", "vbe", "jse", "wsf", "wsh",
    "html", "htm", "xhtml", "dll", "msi", "jar", "scr", "com", "pif", "cpl",
    "gadget", "application", "hta", "lnk", "reg", "msc",
}
ALLOWED_EXTS = IMAGE_EXTS | DOCUMENT_EXTS

_MIME_OVERRIDES = {
    "heic": "image/heic",
    "heif": "image/heif",
    "avif": "image/avif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "webp": "image/webp",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "csv": "text/csv",
    "rtf": "application/rtf",
}


def documents_root() -> Path:
    return config_module.data_root() / "documents"


def previews_root() -> Path:
    return documents_root() / "previews"


def _ensure_dirs() -> None:
    documents_root().mkdir(parents=True, exist_ok=True)
    previews_root().mkdir(parents=True, exist_ok=True)


def max_size_bytes() -> int:
    try:
        mb = int(config_module.load_config().get("document_max_size_mb", 30))
    except (OSError, TypeError, ValueError):
        mb = 30
    if mb <= 0:
        mb = 30
    return mb * 1024 * 1024


def max_size_mb() -> int:
    return max_size_bytes() // (1024 * 1024)


def split_ext(filename: str) -> str:
    return Path(str(filename or "")).suffix.lower().lstrip(".")


def validate_extension(ext: str) -> str:
    ext = (ext or "").lower()
    if not ext:
        raise DocumentError("文件没有扩展名，无法识别类型")
    if ext in FORBIDDEN_EXTS:
        raise DocumentError(f"不允许上传 .{ext} 文件")
    if ext not in ALLOWED_EXTS:
        raise DocumentError(f"不支持的文件类型：.{ext}")
    return ext


def mime_for(ext: str) -> str:
    return _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(f"x.{ext}")[0] or "application/octet-stream"


def is_image(ext: str) -> bool:
    return (ext or "").lower() in IMAGE_EXTS


def _register_heif() -> bool:
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        return True
    except Exception:  # noqa: BLE001 — 缺包时 HEIC 不可用，但不是崩溃
        return False


def _verify_image(path: Path, ext: str) -> None:
    """Pillow 真打开一次（防改后缀伪装），失败即拒。"""
    from PIL import Image

    if ext in {"heic", "heif"} and not _register_heif():
        raise DocumentError("当前环境缺少 HEIC 支持，无法读取该图片（pillow-heif 未安装）")
    try:
        with Image.open(path) as image:
            image.verify()
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("图片文件无法打开或已损坏（可能是改过后缀的伪装文件）") from exc


def _make_preview(path: Path, ext: str, stored_name: str) -> str:
    """生成预览图（仅图片）；失败只是没有预览，不影响上传成功。"""
    from PIL import Image

    try:
        with Image.open(path) as image:
            if ext in {"heic", "heif"}:
                _register_heif()
            rgb = image.convert("RGB")
            rgb.thumbnail((480, 480))
            preview_name = f"{Path(stored_name).stem}.jpg"
            rgb.save(previews_root() / preview_name, "JPEG", quality=85)
            return preview_name
    except Exception:  # noqa: BLE001
        return ""


def _stored_name(ext: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    token = secrets.token_hex(4)
    return f"{stamp}_{token}.{ext}"


def save_upload(file_storage, *, category_id: int | None = None, note: str = "") -> dict:
    """把一个上传文件校验后落盘并登记。返回 id / title / stored_name / preview。"""
    filename = str(getattr(file_storage, "filename", "") or "").strip()
    if not filename:
        raise DocumentError("上传文件缺少文件名")
    ext = validate_extension(split_ext(filename))
    _ensure_dirs()
    limit = max_size_bytes()
    tmp = documents_root() / f".pending-{secrets.token_hex(6)}.{ext}"
    size = 0
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = file_storage.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise DocumentError(f"文件超过大小上限 {max_size_mb()}MB")
                out.write(chunk)
        if size == 0:
            raise DocumentError("文件为空")
        if is_image(ext):
            _verify_image(tmp, ext)
        stored = _stored_name(ext)
        final = documents_root() / stored
        os.replace(tmp, final)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    preview = _make_preview(final, ext, stored) if is_image(ext) else ""
    title = Path(filename).stem or filename
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO document_files
               (title, category_id, stored_name, original_name, ext, size_bytes, mime, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, category_id, stored, filename, ext, size, mime_for(ext), note),
        )
        doc_id = cursor.lastrowid
    return {"id": doc_id, "title": title, "stored_name": stored, "preview": preview}


def get_document(doc_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM document_files WHERE id=? AND deleted_at IS NULL", (doc_id,)
        ).fetchone()
    if row is None:
        raise DocumentError("文档不存在或已删除")
    return dict(row)


def document_path(doc: dict) -> Path:
    root = documents_root().resolve()
    path = (root / str(doc["stored_name"])).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise DocumentError("文件路径非法") from exc
    if not path.is_file():
        raise DocumentError("文件已丢失，可能被手工删除")
    return path


def preview_path(doc: dict) -> Path | None:
    name = f"{Path(str(doc['stored_name'])).stem}.jpg"
    path = previews_root() / name
    return path if path.is_file() else None


def is_inline_viewable(ext: str) -> bool:
    return is_image(ext) or (ext or "").lower() == "pdf"


def open_externally(path: Path) -> None:
    """用本机关联程序打开（Windows 专有，符合本项目单机定位）。"""
    if not hasattr(os, "startfile"):
        raise DocumentError("当前系统不支持「直接打开」，请下载后打开")
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError as exc:
        raise DocumentError(f"无法调用本机程序打开：{exc}") from exc


def print_externally(path: Path) -> None:
    """调系统「打印」动词；关联程序不支持时会抛 OSError（由界面提示兜底）。"""
    if not hasattr(os, "startfile"):
        raise DocumentError("当前系统不支持「直接打印」，请打开后打印")
    try:
        os.startfile(str(path), "print")  # type: ignore[attr-defined]
    except OSError as exc:
        raise DocumentError("该文件类型没有可用的打印动作，请先打开后打印") from exc


def delete_document(doc_id: int) -> dict:
    """物理删除一条文档 + 磁盘原件 + 预览图（不接回收站，一期口径）。"""
    doc = get_document(doc_id)
    with get_db() as conn:
        conn.execute("DELETE FROM document_files WHERE id=?", (doc_id,))
    (documents_root() / str(doc["stored_name"])).unlink(missing_ok=True)
    preview = previews_root() / f"{Path(str(doc['stored_name'])).stem}.jpg"
    preview.unlink(missing_ok=True)
    return doc


def update_document(doc_id: int, *, title: str, category_id: int | None, note: str) -> None:
    get_document(doc_id)  # 不存在直接抛
    title = (title or "").strip()
    if not title:
        raise DocumentError("标题不能为空")
    with get_db() as conn:
        conn.execute(
            "UPDATE document_files SET title=?, category_id=?, note=? WHERE id=?",
            (title, category_id, note or "", doc_id),
        )


def add_category(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise DocumentError("分类名称不能为空")
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM document_categories WHERE name=?", (name,)).fetchone()
        if existing is not None:
            raise DocumentError("同名的分类已存在")
        # 自定义分类排在所有内置/已有分类之后。
        row = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM document_categories").fetchone()
        cursor = conn.execute(
            "INSERT INTO document_categories(name, sort_order, is_builtin) VALUES (?, ?, 0)",
            (name, int(row["next_order"])),
        )
        return {"id": cursor.lastrowid, "name": name}
