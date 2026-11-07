"""2-A 资料文档库：上传/格式白名单/大小/伪装文件/多选/删除/迁移备份。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services import documents as doc_service
from erp.utils.backup import create_backup, list_backups


def png_bytes(w=40, h=40) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _client():
    init_db()
    return create_app().test_client()


def _docs():
    with get_db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM document_files ORDER BY id"
        ).fetchall()]


def test_upload_png_success_creates_file_and_preview():
    client = _client()
    response = client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "检验报告.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    docs = _docs()
    assert len(docs) == 1
    doc = docs[0]
    assert doc["original_name"] == "检验报告.png"
    assert doc["title"] == "检验报告"
    assert doc["ext"] == "png"
    assert (doc_service.documents_root() / doc["stored_name"]).is_file()
    assert doc_service.preview_path(doc) is not None


def test_upload_multiple_files_batch():
    client = _client()
    response = client.post(
        "/docs/upload",
        data={
            "files": [
                (io.BytesIO(png_bytes()), "a.png"),
                (io.BytesIO(pdf_bytes()), "b.pdf"),
            ]
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    docs = _docs()
    assert {doc["ext"] for doc in docs} == {"png", "pdf"}
    assert "已上传 2 个文件" in response.get_data(as_text=True)


def test_upload_rejects_svg_and_executable():
    client = _client()
    for name in ("图.svg", "程序.exe", "脚本.ps1"):
        response = client.post(
            "/docs/upload",
            data={"files": (io.BytesIO(b"<svg/>"), name)},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
    assert _docs() == []
    # 不能留下任何半成品文件
    if doc_service.documents_root().exists():
        leftovers = [p for p in doc_service.documents_root().iterdir() if p.name.startswith(".pending-")]
        assert leftovers == []


def test_upload_rejects_disguised_image(monkeypatch):
    client = _client()
    # 后缀是 .png，内容却是文本 —— Pillow 验真必须拦住。
    response = client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(b"this is definitely not a png"), "伪装.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert _docs() == []
    assert "无法打开" in response.get_data(as_text=True)


def test_upload_rejects_oversize(monkeypatch):
    client = _client()
    monkeypatch.setattr(doc_service, "max_size_bytes", lambda: 10)
    response = client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes(200, 200)), "big.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert _docs() == []
    assert "大小上限" in response.get_data(as_text=True)


def test_delete_document_removes_db_and_files():
    client = _client()
    client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "del.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    doc = _docs()[0]
    stored = doc_service.documents_root() / doc["stored_name"]
    preview = doc_service.preview_path(doc)
    assert stored.is_file() and preview is not None

    response = client.post(
        f"/docs/{doc['id']}/delete",
        data={"confirm_text": "删除"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert _docs() == []
    assert not stored.exists()
    assert not preview.exists()


def test_delete_requires_confirm_word():
    client = _client()
    client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "keep.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    doc = _docs()[0]
    response = client.post(
        f"/docs/{doc['id']}/delete",
        data={"confirm_text": "错了"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert len(_docs()) == 1  # 未删除


def test_list_page_renders_with_categories_and_search():
    client = _client()
    client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "年度检验报告.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    page = client.get("/docs/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "资料文档" in html
    assert "年度检验报告" in html
    assert "检验报告" in html  # 内置分类 tab

    found = client.get("/docs/?q=年度").get_data(as_text=True)
    assert "年度检验报告" in found
    none = client.get("/docs/?q=不存在关键词").get_data(as_text=True)
    assert "还没有资料文档" in none


def test_upload_rejects_unknown_category():
    client = _client()
    response = client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "x.png"), "category_id": "9999"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert _docs() == []
    assert "分类不存在" in response.get_data(as_text=True)


def test_heic_upload_supported_when_pillow_heif_available():
    try:
        import pillow_heif  # noqa: F401
        pillow_heif.register_heif_opener()
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), (1, 2, 3)).save(buffer, "HEIF")
        heic = buffer.getvalue()
    except Exception:  # noqa: BLE001
        pytest.skip("当前环境不支持生成 HEIC 样本")

    client = _client()
    response = client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(heic), "证书.heic")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    docs = _docs()
    assert docs and docs[0]["ext"] == "heic"


def test_v15_to_v16_migration_creates_pre_document_library_backup():
    init_db()
    # 造一个「v15 形态」的库：删掉资料文档两表，版本回退到 15。
    with get_db() as conn:
        conn.execute("DROP TABLE IF EXISTS document_files")
        conn.execute("DROP TABLE IF EXISTS document_categories")
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version(version, notes) VALUES (15, 'simulated v15')")

    before = {entry["name"] for entry in list_backups()}
    init_db()
    after = list_backups()
    assert any(entry["reason"] == "pre_document_library" for entry in after), "升级 v16 前必须自动整库备份"
    # 表已重建、版本推进到 v16
    with get_db() as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 16
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"document_files", "document_categories"} <= names
    # 备份是新产生的（不是升级前就有）
    assert {entry["name"] for entry in after} - before


def test_backup_zip_includes_documents_dir():
    init_db()
    doc_service.documents_root().mkdir(parents=True, exist_ok=True)
    (doc_service.documents_root() / "sample.txt").write_text("hello", encoding="utf-8")

    path = create_backup("pytest")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    assert any(name.startswith("documents/") for name in names), "备份必须打包 documents/"


def test_purge_all_data_clears_document_tables_and_files():
    from erp.utils.reset import purge_all_data

    client = _client()
    client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "要清掉.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    doc = _docs()[0]
    stored = doc_service.documents_root() / doc["stored_name"]
    assert stored.is_file()

    purge_all_data()

    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM document_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM document_categories").fetchone()[0] == 0
    # documents/ 目录被清空（不残留）
    if doc_service.documents_root().exists():
        assert list(doc_service.documents_root().iterdir()) == []


def test_print_and_reconciliation_templates_do_not_reference_documents():
    templates = Path(__file__).resolve().parents[1] / "app" / "erp" / "templates"
    for path in templates.rglob("*.html"):
        if not any(key in path.name for key in ("print", "recon", "summary")):
            continue
        text = path.read_text(encoding="utf-8")
        assert "document_files" not in text, path
        assert "document_categories" not in text, path
