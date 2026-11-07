"""2-A 资料文档库：直接打开 / 直接打印（调本机关联程序，测试注入假 startfile）。"""
from __future__ import annotations

import io

from erp import create_app
from erp.db import get_db, init_db
from erp.services import documents as doc_service


def png_bytes(w=20, h=20) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (w, h), (30, 30, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def _upload(client) -> dict:
    client.post(
        "/docs/upload",
        data={"files": (io.BytesIO(png_bytes()), "待打开.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with get_db() as conn:
        return dict(conn.execute("SELECT * FROM document_files ORDER BY id DESC LIMIT 1").fetchone())


def _client():
    init_db()
    return create_app().test_client()


def test_open_uses_os_startfile(monkeypatch):
    client = _client()
    doc = _upload(client)
    calls = []
    monkeypatch.setattr(doc_service.os, "startfile", lambda path, *args: calls.append((path, *args)), raising=False)

    response = client.post(f"/docs/{doc['id']}/open", follow_redirects=True)
    assert response.status_code == 200
    assert calls, "打开应调用 os.startfile"
    assert str(calls[0][0]).endswith(doc["stored_name"])
    assert "已用本机程序打开" in response.get_data(as_text=True)


def test_print_uses_system_print_verb(monkeypatch):
    client = _client()
    doc = _upload(client)
    calls = []
    monkeypatch.setattr(doc_service.os, "startfile", lambda path, *args: calls.append((path, *args)), raising=False)

    response = client.post(f"/docs/{doc['id']}/print", follow_redirects=True)
    assert response.status_code == 200
    assert calls and calls[0][1] == "print", "打印应调用 os.startfile(path, 'print')"
    assert "已调用系统打印" in response.get_data(as_text=True)


def test_print_falls_back_with_message_when_no_print_verb(monkeypatch):
    client = _client()
    doc = _upload(client)

    def boom(path, *args):
        raise OSError("no print verb")

    monkeypatch.setattr(doc_service.os, "startfile", boom, raising=False)
    response = client.post(f"/docs/{doc['id']}/print", follow_redirects=True)
    assert response.status_code == 200
    assert "打开后打印" in response.get_data(as_text=True)


def test_file_route_inline_vs_attachment():
    client = _client()
    doc = _upload(client)
    response = client.get(f"/docs/{doc['id']}/file")
    assert response.status_code == 200
    # 图片内嵌（不是 attachment）
    disposition = response.headers.get("Content-Disposition", "")
    assert "attachment" not in disposition
