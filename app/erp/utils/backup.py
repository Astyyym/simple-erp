"""备份与恢复（Batch A）。

约定（见 开发短计划/2026-10-08_旧数据迁移承接与备份恢复_执行计划.md）：
- 备份是整库快照 zip：erp.db + config.json + product_images/ + legacy_archive/。
- 排除 temp_pdf/、logs/、imports/（imports/ 经核实从未被任何代码写入）。
- 恢复默认只恢复数据库与数据文件（商品图片、旧账存档），**不动 config.json**；
  配置恢复必须显式勾选。恢复是破坏性操作，先自动备份当前库。
"""

from __future__ import annotations

import logging
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
_DATA_DIR_NAMES = ("product_images", "legacy_archive", "documents")


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
        # 备份包里的库结构版本：恢复旧版备份会触发不可逆升级，界面必须提前提示。
        schema_version = read_backup_schema_version(path)
        entries.append(
            {
                "name": path.name,
                "path": str(path),
                "reason": reason,
                "created_at": created_at,
                "created_at_display": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "size_bytes": size_bytes,
                "size_display": _human_size(size_bytes),
                "schema_version": schema_version,
                "schema_version_display": f"v{schema_version}" if schema_version else "未知",
                "is_legacy": schema_version is None or schema_version < db_module.CURRENT_SCHEMA_VERSION,
            }
        )
    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries


def _database_schema_version(db_file: Path) -> int | None:
    """Read `MAX(version)` from a database file read-only; None when absent/unreadable."""
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.Error:
        # 老库没有 schema_version 表（或库损坏）——按「未知/旧版」处理，由界面提示。
        return None
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else None


def read_backup_schema_version(zip_path: Path | str) -> int | None:
    """备份包内 `erp.db` 的 schema 版本；读不出返回 None（过老或损坏）。

    恢复界面靠它判断「这份备份是旧版结构」：恢复旧版库后，程序下次启动会执行
    **不可逆**的 v15 升级（物理删表删列），必须提前告知用户。
    """
    path = Path(zip_path)
    try:
        with zipfile.ZipFile(path) as zf:
            if "erp.db" not in zf.namelist():
                return None
            with tempfile.TemporaryDirectory(prefix="backup_schema_") as tmp:
                staged = Path(tmp) / "erp.db"
                with zf.open("erp.db") as source, staged.open("wb") as target:
                    shutil.copyfileobj(source, target)
                return _database_schema_version(staged)
    except (zipfile.BadZipFile, OSError, KeyError):
        return None


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


# ---------------------------------------------------------------------------
# 1-A：从文件导入外部备份（backups/ 之外的 zip）
# ---------------------------------------------------------------------------


def _probe_backup_database(zip_path: Path | str) -> tuple[int | None, str]:
    """读取备份包内 `erp.db` 的 (schema 版本, 完整性结果)。

    - 版本 None 表示库内没有 `schema_version` 表（很老的备份）或读不出；
    - 完整性必须为 "ok"，否则视为损坏、拒绝导入。
    """
    path = Path(zip_path)
    with zipfile.ZipFile(path) as zf:
        if "erp.db" not in zf.namelist():
            raise RestoreError("备份缺少 erp.db，无法导入")
        with tempfile.TemporaryDirectory(prefix="backup_import_probe_") as tmp:
            staged = Path(tmp) / "erp.db"
            with zf.open("erp.db") as source, staged.open("wb") as target:
                shutil.copyfileobj(source, target)
            try:
                conn = sqlite3.connect(f"file:{staged}?mode=ro", uri=True)
            except sqlite3.Error as exc:  # 不是可读的 SQLite 文件
                raise RestoreError(f"备份内的数据库无法打开：{exc}") from exc
            try:
                try:
                    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
                    version = int(row[0]) if row and row[0] is not None else None
                except sqlite3.Error:
                    version = None
                try:
                    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
                except sqlite3.Error as exc:
                    raise RestoreError(f"备份内的数据库无法读取：{exc}") from exc
            finally:
                conn.close()
    return version, integrity


def import_backup(source_path: Path | str) -> Path:
    """校验一份外部备份 zip 并复制进 `backups/`（不覆盖既有文件、不动当前数据）。

    - 只校验 + 落盘，**不做恢复**；
    - 备份库版本高于当前程序 → 拒绝（db 的降级守卫会在恢复后重开时直接抛错）；
    - 损坏/缺少 `erp.db` → 明确报错，且不留下任何脏文件。
    """
    path = Path(source_path)
    inspect_backup(path)  # 校验 zip / erp.db / 含非法路径即抛 RestoreError
    version, integrity = _probe_backup_database(path)
    if version is not None and version > db_module.CURRENT_SCHEMA_VERSION:
        raise RestoreError(
            f"这份备份的数据库版本是 v{version}，高于当前程序（v{db_module.CURRENT_SCHEMA_VERSION}），"
            "请升级程序后再导入。"
        )
    if integrity != "ok":
        raise RestoreError(f"备份内数据库完整性检查失败：{integrity}")

    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = target_dir / f"backup_{stamp}_imported.zip"
    target = base
    suffix = 2
    while target.exists():
        target = target_dir / f"backup_{stamp}_imported_{suffix}.zip"
        suffix += 1
    shutil.copy2(path, target)
    return target


# ---------------------------------------------------------------------------
# 1-B：自动备份（启动 1 次 + 正常退出 1 次，带防抖）
# ---------------------------------------------------------------------------

# 防抖间隔（分钟）：距上次 auto* 备份不足该间隔就跳过，避免频繁启停刷出一堆备份。
AUTO_BACKUP_INTERVAL_MINUTES = 10


def _auto_interval_minutes() -> int:
    try:
        value = int(config_module.load_config().get("auto_backup_interval_minutes", AUTO_BACKUP_INTERVAL_MINUTES))
    except (OSError, ValueError, TypeError):
        return AUTO_BACKUP_INTERVAL_MINUTES
    return value if value >= 0 else AUTO_BACKUP_INTERVAL_MINUTES


def last_auto_backup() -> dict | None:
    """最近一份自动备份（reason 以 `auto` 开头），没有返回 None。"""
    for entry in list_backups():  # list_backups 已按时间倒序
        if str(entry.get("reason") or "").startswith("auto"):
            return entry
    return None


def auto_backup_if_due(reason: str = "auto") -> Path | None:
    """到期才自动备份；未到期或失败都安静返回 None，绝不抛出影响启动/退出。"""
    try:
        interval = _auto_interval_minutes()
        last = last_auto_backup()
        if last is not None and last.get("created_at") is not None:
            elapsed = datetime.now() - last["created_at"]
            if elapsed.total_seconds() < interval * 60:
                return None
        return create_backup(reason)
    except Exception:  # noqa: BLE001 — 自动备份失败只记日志
        logging.getLogger(__name__).warning("自动备份失败（已忽略）：%s", reason, exc_info=True)
        return None


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
