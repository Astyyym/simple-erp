"""Batch E-1b（MD 取号上限）+ Batch F（旧账留底存档）。"""

from __future__ import annotations

import io

from erp import create_app
from erp.db import get_db, init_db
from erp.routes.orders import next_order_no
from erp.utils.legacy_archive import archive_dir, list_archives, save_archive


# ---- E-1b ----

def test_md_order_number_raises_at_9999():
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO customers(name) VALUES ('上限客户')"
        )
        cid = conn.execute("SELECT id FROM customers WHERE name='上限客户'").fetchone()["id"]
        conn.execute(
            "INSERT INTO orders(order_no, customer_id, order_date, status) VALUES ('MD202610099999', ?, '2026-10-09', 'saved')",
            (cid,),
        )
        try:
            next_order_no(conn, "2026-10-09")
            assert False, "达到 9999 必须报错"
        except ValueError as exc:
            assert "9999" in str(exc)


def test_md_order_number_normal_sequence_unaffected():
    init_db()
    with get_db() as conn:
        assert next_order_no(conn, "2026-10-09") == "MD202610090001"


# ---- Batch F ----

class _Upload:
    def __init__(self, filename, content):
        self.filename = filename
        self.stream = io.BytesIO(content)


def test_save_archive_writes_file_into_data_root_and_index():
    init_db()
    entry = save_archive(_Upload("旧欠款.csv", "客户,金额\n甲,500\n乙,300\n".encode("utf-8-sig")), note="截至10-01")
    stored = archive_dir() / entry["name"]
    assert stored.exists()
    assert entry["line_count"] == 3  # 表头 + 2 行
    assert entry["note"] == "截至10-01"
    assert any(a["name"] == entry["name"] for a in list_archives())


def test_archive_does_not_touch_business_tables():
    init_db()
    with get_db() as conn:
        before = {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
                  for t in ("customers", "products", "orders", "payments", "adjustments")}
    save_archive(_Upload("旧欠款.csv", "a,b\n1,2\n".encode("utf-8-sig")))
    with get_db() as conn:
        after = {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
                 for t in ("customers", "products", "orders", "payments", "adjustments")}
    assert after == before


def test_archive_rejects_bad_type_and_oversize_and_empty():
    init_db()
    for filename, content, expected in (
        ("bad.exe", b"x", "仅支持"),
        ("big.csv", b"a" * (20 * 1024 * 1024 + 1), "不能超过20MB"),
        ("empty.csv", b"", "文件为空"),
    ):
        try:
            save_archive(_Upload(filename, content))
            assert False, f"{filename} 应被拒绝"
        except ValueError as exc:
            assert expected in str(exc)


def test_settings_page_exposes_legacy_archive_section():
    init_db()
    html = create_app().test_client().get("/settings/").get_data(as_text=True)
    assert "旧账留底存档" in html
    assert "/settings/legacy-archive/upload" in html
    assert "不参与账款计算" in html


def test_legacy_archive_upload_endpoint_and_download():
    init_db()
    client = create_app().test_client()
    resp = client.post(
        "/settings/legacy-archive/upload",
        data={"file": (io.BytesIO("客户,金额\n甲,500\n".encode("utf-8-sig")), "旧欠款.csv"), "note": "留底"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "已存档" in resp.get_data(as_text=True)
    entries = list_archives()
    assert entries
    download = client.get(f"/settings/legacy-archive/{entries[0]['name']}")
    assert download.status_code == 200
    assert download.data.startswith("客户".encode("utf-8-sig")) or b"500" in download.data


def test_archive_appears_inside_backup_zip():
    import zipfile

    from erp.utils.backup import create_backup

    init_db()
    save_archive(_Upload("旧欠款.csv", "a,b\n1,2\n".encode("utf-8-sig")))
    backup = create_backup("pytest")
    with zipfile.ZipFile(backup) as zf:
        names = zf.namelist()
    assert any(name.startswith("legacy_archive/") for name in names)
