"""Batch F：旧账留底存档。

只把旧软件导出的欠款清单**原样保存为文件**到数据根 `legacy_archive/`，
**不写入业务库、不影响任何余额与统计**——旧软件口径与本系统不同，硬塞进去只会污染账款。

边界（F-3）：此存档只作留底凭证，不参与账款计算；客户来问「这 5000 怎么来的」时人工翻档核对。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from erp import config as config_module

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024  # 20MB
ALLOWED_SUFFIXES = (".xlsx", ".xls", ".csv", ".txt")
INDEX_NAME = "archive_index.json"


def archive_dir() -> Path:
    return config_module.data_root() / "legacy_archive"


def _load_index() -> list[dict]:
    index_path = archive_dir() / INDEX_NAME
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_index(entries: list[dict]) -> None:
    archive_dir().mkdir(parents=True, exist_ok=True)
    (archive_dir() / INDEX_NAME).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_archives() -> list[dict]:
    return sorted(_load_index(), key=lambda item: item.get("saved_at", ""), reverse=True)


def save_archive(upload, *, note: str = "") -> dict:
    """原样保存旧欠款清单文件，返回登记条目。不做内容解析。"""
    filename = (upload.filename or "").strip()
    if not filename:
        raise ValueError("请选择要存档的文件")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("仅支持 .xlsx / .xls / .csv / .txt 文件")
    content = upload.stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("存档文件不能超过20MB")
    if not content:
        raise ValueError("文件为空")

    target_dir = archive_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stem = Path(filename).stem.replace("/", "_").replace("\\", "_").strip() or "legacy"
    stored_name = f"{stamp}_{safe_stem}{suffix}"
    (target_dir / stored_name).write_bytes(content)

    entry = {
        "name": stored_name,
        "original_name": filename,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "saved_at_display": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "size_bytes": len(content),
        "size_display": _human_size(len(content)),
        "note": (note or "").strip(),
        "line_count": _count_lines(content, suffix),
    }
    entries = _load_index()
    entries.append(entry)
    _save_index(entries)
    return entry


def _count_lines(content: bytes, suffix: str) -> int | None:
    """仅对 CSV/TXT 粗略计数，供用户核对行数；xlsx 不解析。"""
    if suffix not in (".csv", ".txt"):
        return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            return sum(1 for line in text.splitlines() if line.strip())
        except UnicodeDecodeError:
            continue
    return None


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
