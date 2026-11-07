"""2-A 资料文档库分类：内置四档 + 新增自定义分类。"""
from __future__ import annotations

import io

from erp import create_app
from erp.db import get_db, init_db


def png_bytes(w=20, h=20) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 10)).save(buffer, "PNG")
    return buffer.getvalue()


def _categories():
    with get_db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM document_categories ORDER BY sort_order, id"
        ).fetchall()]


def test_builtin_categories_seeded():
    init_db()
    names = [row["name"] for row in _categories()]
    assert names == ["检验报告", "合格证", "资质证书", "其他"]
    assert all(row["is_builtin"] == 1 for row in _categories())


def test_add_custom_category_appears_after_builtin():
    init_db()
    app = create_app()
    client = app.test_client()
    response = client.post("/docs/categories", data={"name": "检测补充"}, follow_redirects=True)
    assert response.status_code == 200
    cats = _categories()
    assert cats[-1]["name"] == "检测补充"
    assert cats[-1]["is_builtin"] == 0
    assert len(cats) == 5


def test_duplicate_category_rejected():
    init_db()
    app = create_app()
    client = app.test_client()
    client.post("/docs/categories", data={"name": "检测补充"})
    response = client.post("/docs/categories", data={"name": "检测补充"}, follow_redirects=True)
    assert "已存在" in response.get_data(as_text=True)
    assert len([c for c in _categories() if c["name"] == "检测补充"]) == 1


def test_upload_with_category_links_it():
    init_db()
    app = create_app()
    client = app.test_client()
    client.post("/docs/categories", data={"name": "自定义分类A"})
    with get_db() as conn:
        cat_id = conn.execute("SELECT id FROM document_categories WHERE name='自定义分类A'").fetchone()["id"]
    client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "带分类.png"), "category_id": str(cat_id)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with get_db() as conn:
        doc = conn.execute("SELECT * FROM document_files ORDER BY id DESC LIMIT 1").fetchone()
    assert doc["category_id"] == cat_id
    html = client.get("/docs/").get_data(as_text=True)
    assert "自定义分类A" in html
