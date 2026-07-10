import zipfile

from erp.db import get_db, init_db, integrity_check
from erp.utils.backup import create_backup


def test_init_db_enables_wal_and_schema_version():
    init_db()
    with get_db() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        version = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()[0]
    assert journal_mode == "wal"
    assert version == 1
    assert integrity_check() == "ok"


def test_backup_package_contains_database_config_info_and_integrity():
    init_db()
    backup_path = create_backup("pytest")
    assert backup_path.exists()
    with zipfile.ZipFile(backup_path) as zf:
        names = set(zf.namelist())
    assert {"erp.db", "config.json", "backup_info.txt", "integrity_check.txt"}.issubset(names)
