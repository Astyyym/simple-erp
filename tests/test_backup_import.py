"""1-A 备份：从文件导入（外部 zip）。

全部在 pytest 隔离数据根内运行，绝不触碰正式 data/erp.db。
"""
from __future__ import annotations

import io
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from erp import create_app
from erp import db as db_module
from erp.db import CURRENT_SCHEMA_VERSION, init_db
from erp.services.accounting import create_customer
from erp.utils.backup import (
    RestoreError,
    backup_dir,
    create_backup,
    import_backup,
    list_backups,
)


def _write_zip(zip_path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return zip_path


def _db_bytes_with_version(tmp_path: Path, version: int | None = None) -> bytes:
    source = sqlite3.connect(db_module.db_path())
    staged = tmp_path / "staged_erp.db"
    target = sqlite3.connect(staged)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    if version is not None:
        conn = sqlite3.connect(staged)
        try:
            conn.execute("INSERT INTO schema_version(version, notes) VALUES (?, ?)", (version, "test"))
            conn.commit()
        finally:
            conn.close()
    data = staged.read_bytes()
    staged.unlink()
    return data


def test_import_valid_backup_copies_into_backups_and_lists(tmp_path):
    init_db()
    create_customer("导入前客户")
    original = create_backup("manual")
    external = tmp_path / "external.zip"
    shutil.copy2(original, external)

    imported = import_backup(external)

    assert imported.parent == backup_dir()
    assert imported.name.startswith("backup_") and imported.name.endswith("_imported.zip")
    assert imported.is_file()
    names = [entry["name"] for entry in list_backups()]
    assert imported.name in names
    # 原备份仍在，导入的是新增一份
    assert original.name in names


def test_import_rejects_non_zip(tmp_path):
    init_db()
    bogus = tmp_path / "not.zip"
    bogus.write_bytes(b"this is not a zip")
    with pytest.raises(RestoreError):
        import_backup(bogus)


def test_import_rejects_zip_without_erp_db(tmp_path):
    init_db()
    bad = _write_zip(tmp_path / "no-db.zip", {"readme.txt": b"hello"})
    with pytest.raises(RestoreError):
        import_backup(bad)


def test_import_rejects_corrupt_database(tmp_path):
    init_db()
    bad = _write_zip(tmp_path / "corrupt.zip", {"erp.db": b"not a database at all"})
    with pytest.raises(RestoreError):
        import_backup(bad)


def test_import_rejects_newer_schema_version(tmp_path):
    init_db()
    newer = CURRENT_SCHEMA_VERSION + 1
    payload = _db_bytes_with_version(tmp_path, version=newer)
    bad = _write_zip(tmp_path / "newer.zip", {"erp.db": payload})
    with pytest.raises(RestoreError) as caught:
        import_backup(bad)
    assert "高于当前程序" in str(caught.value)


def test_import_does_not_overwrite_same_second(tmp_path):
    init_db()
    original = create_backup("manual")
    external = tmp_path / "external.zip"
    shutil.copy2(original, external)

    first = import_backup(external)
    second = import_backup(external)

    assert first != second
    assert first.is_file() and second.is_file()


def test_import_route_uploads_file(tmp_path):
    init_db()
    original = create_backup("manual")
    data = original.read_bytes()
    app = create_app()
    client = app.test_client()
    response = client.post(
        "/settings/backup/import",
        data={"file": (io.BytesIO(data), "外部备份.zip")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    imported = [entry for entry in list_backups() if entry["reason"].startswith("imported")]
    assert imported, "导入成功后备份列表里应出现 imported 备份"


def test_import_route_rejects_txt_extension():
    init_db()
    app = create_app()
    client = app.test_client()
    response = client.post(
        "/settings/backup/import",
        data={"file": (io.BytesIO(b"hello"), "note.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "只能导入 .zip 备份文件" in response.get_data(as_text=True)
