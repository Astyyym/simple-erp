"""备份与恢复（Batch A）。

约定（见 开发短计划/2026-10-08_旧数据迁移承接与备份恢复_执行计划.md）：
- 备份是整库快照 zip：erp.db + config.json + product_images/ + legacy_archive/。
- 排除 temp_pdf/、logs/、imports/（imports/ 经核实从未被任何代码写入）。
- 恢复默认只恢复数据库与数据文件（商品图片、旧账存档），**不动 config.json**；
  配置恢复必须显式勾选。恢复是破坏性操作，先自动备份当前库。
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from erp import config as config_module
from erp import db as db_module

# 备份文件名：backup_<YYYYmmdd_HHMMSS>_<reason>.zip
_BACKUP_NAME_RE = re.compile(r"^backup_(\d{8}_\d{6})_(.+)\.zip$")

# 需要随备份一起走的数据根目录（存在才收）。
_DATA_DIR_NAMES = ("product_images", "legacy_archive")


def backup_dir() -> Path:
    return config_module.project_path("backups")


def create_backup(reason: str = "manual") -> Path:
    """Create a zip snapshot of the business database and data folders.

    Returns the zip path. A pre-existing backup set older than
    ``backup_retention_days`` is pruned afterwards (never the new one).
    """
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = target_dir / f"backup_{timestamp}_{reason}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    backup_db = work_dir / "erp.db"
    source = sqlite3.connect(db_module.db_path())
    target = sqlite3.connect(backup_db)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    integrity = db_module.integrity_check()
    (work_dir / "integrity_check.txt").write_text(integrity, encoding="utf-8")
    # A-0：真实换行，不再写字面 "\n"。
    (work_dir / "backup_info.txt").write_text(
        f"reason={reason}\ncreated_at={datetime.now().isoformat()}\nintegrity={integrity}\n",
        encoding="utf-8",
    )

    config_path = config_module.writable_config_path()
    if config_path.exists():
        (work_dir / "config.json").write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

    # A-1：覆盖真正有内容的数据根目录。
    for dirname in _DATA_DIR_NAMES:
        source_dir = config_module.data_root() / dirname
        if not source_dir.exists():
            continue
        for item in source_dir.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(config_module.data_root())
            destination = work_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item.read_bytes())

    zip_path = target_dir / f"backup_{timestamp}_{reason}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(work_dir.rglob("*")):
                if file.is_file():
                    zf.write(file, arcname=file.relative_to(work_dir))
    finally:
        # 不留下一份完整的库副本；成功打包后清理工作目录。
        if zip_path.exists():
            shutil.rmtree(work_dir, ignore_errors=True)

    prune_old_backups()
    return zip_path


def list_backups() -> list[dict]:
    """Return existing backup zips, newest first, with parsed metadata."""
    directory = backup_dir()
    if not directory.exists():
        return []
    entries: list[dict] = []
    for path in directory.glob("backup_*.zip"):
        if not path.is_file():
            continue
        match = _BACKUP_NAME_RE.match(path.name)
        if not match:
            continue
        stamp, reason = match.group(1), match.group(2)
        try:
            created_at = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        size_bytes = path.stat().st_size
        entries.append(
            {
                "name": path.name,
                "path": str(path),
                "reason": reason,
                "created_at": created_at,
                "created_at_display": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "size_bytes": size_bytes,
                "size_display": _human_size(size_bytes),
            }
        )
    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def retention_days() -> int:
    try:
        value = int(config_module.load_config().get("backup_retention_days", 30))
    except (OSError, ValueError, TypeError):
        return 30
    return value if value > 0 else 30


def prune_old_backups(reference: datetime | None = None) -> list[str]:
    """Delete backup zips (and stale work dirs) older than the retention window."""
    directory = backup_dir()
    if not directory.exists():
        return []
    days = retention_days()
    now = reference or datetime.now()
    cutoff = now - timedelta(days=days)
    removed: list[str] = []
    for entry in list_backups():
        if entry["created_at"] < cutoff:
            try:
                Path(entry["path"]).unlink()
                removed.append(entry["name"])
            except OSError:
                continue
    # 清理残留的工作目录（历史版本遗留）。
    for work in directory.glob("backup_*"):
        if not work.is_dir():
            continue
        match = _BACKUP_NAME_RE.match(work.name + ".zip")
        stamp = match.group(1) if match else None
        stale = True
        if stamp:
            try:
                stale = datetime.strptime(stamp, "%Y%m%d_%H%M%S") < cutoff
            except ValueError:
                stale = True
        if stale:
            shutil.rmtree(work, ignore_errors=True)
    return removed


def copy_backup_to(zip_path: Path | str, destination_dir: Path | str) -> Path:
    """A-5：把一份备份另存到用户指定目录（异地/移动硬盘）。"""
    source = Path(zip_path)
    if not source.exists():
        raise FileNotFoundError(f"备份文件不存在：{source}")
    dest_text = str(destination_dir or "").strip()
    if not dest_text:
        raise ValueError("请填写要另存的目录")
    dest_dir = Path(dest_text).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = dest_dir / source.name
    if destination.resolve() == source.resolve():
        raise ValueError("另存目录与当前备份目录相同，请选择其他位置")
    shutil.copy2(source, destination)
    return destination


class RestoreError(ValueError):
    """Raised when a backup zip cannot be safely restored."""


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or name.startswith("\\"):
            raise RestoreError(f"备份包含非法绝对路径：{name}")
        parts = Path(name).parts
        if any(part == ".." for part in parts):
            raise RestoreError(f"备份包含非法上级路径：{name}")


def inspect_backup(zip_path: Path | str) -> dict:
    """Validate a backup zip and report what it holds, without touching live data."""
    path = Path(zip_path)
    if not path.exists():
        raise RestoreError(f"备份文件不存在：{path.name}")
    if not zipfile.is_zipfile(path):
        raise RestoreError("该文件不是有效的备份压缩包")
    with zipfile.ZipFile(path) as zf:
        _validate_zip_members(zf)
        names = set(zf.namelist())
        if "erp.db" not in names:
            raise RestoreError("备份缺少 erp.db，无法恢复")
    info: dict = {"name": path.name, "path": str(path), "has_config": "config.json" in names}
    match = _BACKUP_NAME_RE.match(path.name)
    if match:
        try:
            info["created_at_display"] = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            pass
    return info


def restore_backup(zip_path: Path | str, *, include_config: bool = False) -> dict:
    """Restore the business database (and data folders) from a backup zip.

    Destructive: a pre-restore snapshot of the current database is always taken
    first. ``config.json`` is left untouched unless ``include_config`` is True.
    """
    path = Path(zip_path)
    inspect_backup(path)  # raises RestoreError when unsafe

    data_root = config_module.data_root()
    db_path = db_module.db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pre_restore = create_backup("pre_restore")

    staging = Path(tempfile.mkdtemp(prefix="restore_", dir=str(backup_dir())))
    try:
        with zipfile.ZipFile(path) as zf:
            _validate_zip_members(zf)
            zf.extractall(staging)

        staged_db = staging / "erp.db"
        if not staged_db.exists():
            raise RestoreError("备份缺少 erp.db，无法恢复")
        # 先校验来源库完整性，避免把坏库盖到好库上。
        check = sqlite3.connect(f"file:{staged_db}?mode=ro", uri=True)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if result != "ok":
            raise RestoreError(f"备份内数据库完整性检查失败：{result}")

        # 关闭 WAL、清理旁挂文件，保证替换后不残留旧事务。
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

        staged_db.replace(db_path)

        restored_dirs: list[str] = []
        for dirname in _DATA_DIR_NAMES:
            staged_dir = staging / dirname
            if not staged_dir.exists():
                continue
            target_dir = data_root / dirname
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            shutil.copytree(staged_dir, target_dir)
            restored_dirs.append(dirname)

        config_restored = False
        if include_config and (staging / "config.json").exists():
            shutil.copy2(staging / "config.json", config_module.writable_config_path())
            config_restored = True

        integrity = db_module.integrity_check()
        if integrity != "ok":
            raise RestoreError(f"恢复后数据库完整性检查失败：{integrity}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "pre_restore_backup": str(pre_restore),
        "restored_dirs": restored_dirs,
        "config_restored": config_restored,
        "integrity": integrity,
    }
