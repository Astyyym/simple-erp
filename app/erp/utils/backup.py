import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from erp import config as config_module
from erp import db as db_module


def create_backup(reason: str = "manual") -> Path:
    backup_dir = config_module.project_path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = backup_dir / f"backup_{timestamp}_{reason}"
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
    (work_dir / "backup_info.txt").write_text(
        f"reason={reason}\\ncreated_at={datetime.now().isoformat()}\\nintegrity={integrity}\\n",
        encoding="utf-8",
    )
    config_path = config_module.writable_config_path()
    if config_path.exists():
        (work_dir / "config.json").write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
    image_dir = config_module.data_root() / "product_images"
    if image_dir.exists():
        for image in image_dir.rglob("*"):
            if image.is_file():
                relative = image.relative_to(config_module.data_root())
                destination = work_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(image.read_bytes())
    zip_path = backup_dir / f"backup_{timestamp}_{reason}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in work_dir.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(work_dir))
    return zip_path
