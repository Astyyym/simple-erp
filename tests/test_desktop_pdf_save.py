"""Regression coverage for Issue #8 desktop PDF previews and native PDF saving."""

import base64
import re
import sys
import types
from pathlib import Path
from uuid import uuid4

import desktop_app as desk
from pdf_test_utils import assert_a4_portrait_pdf
from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


class FakeWindow:
    def __init__(self, dialog_result=None, dialog_error=None):
        self.dialog_result = dialog_result
        self.dialog_error = dialog_error
        self.dialog_calls = []

    def create_file_dialog(self, *args, **kwargs):
        self.dialog_calls.append((args, kwargs))
        if self.dialog_error is not None:
            raise self.dialog_error
        return self.dialog_result


def _install_webview(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(SAVE_DIALOG="SAVE_DIALOG"))


def _sample_pdf_data_url(payload=b"%PDF-1.4\nissue-8\n"):
    return "data:application/pdf;base64," + base64.b64encode(payload).decode("ascii")


def _make_order():
    suffix = uuid4().hex[:8]
    customer_id = create_customer(f"桌面 PDF 客户-{suffix}")
    sale_order_id = create_order_from_typed_rows(
        customer_id,
        f"DESKTOP-PDF-SALE-{suffix}",
        [{"product_name": "桌面预览阀", "unit": "只", "unit_price_yuan": "15", "quantity": "1"}],
        status="saved",
        order_type="sale",
    )
    return_order_id = create_order_from_typed_rows(
        customer_id,
        f"DESKTOP-PDF-RETURN-{suffix}",
        [{"product_name": "退货预览阀", "unit": "只", "unit_price_yuan": "15", "quantity": "1"}],
        status="saved",
        order_type="return",
    )
    return customer_id, sale_order_id, return_order_id


def test_desktop_preview_covers_order_summaries_and_settings_sample(monkeypatch):
    """All four PDF sources render the shared in-session save toolbar in desktop mode."""
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    customer_id, sale_order_id, return_order_id = _make_order()
    client = create_app().test_client()

    for path in (
        f"/orders/{sale_order_id}/pdf?desktop_preview=1",
        f"/orders/{return_order_id}/pdf?desktop_preview=1",
        "/orders/summary_pdf?desktop_preview=1",
        f"/accounts/summary_pdf?customer_id={customer_id}&desktop_preview=1",
        "/settings/print-preview.pdf?desktop_preview=1",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.mimetype == "text/html", path
        html = response.get_data(as_text=True)
        assert 'id="savePdf"' in html, path
        assert re.search(
            r'<a\b(?=[^>]*id="returnToErp")(?=[^>]*href="/")[^>]*>\s*返回上一级\s*</a>',
            html,
        ), path
        assert "window.pywebview.api.save_pdf" in html, path
        assert "desktop_preview" not in html.split("fetch(", 1)[1].split(")", 1)[0], path


def test_desktop_preview_uses_explicit_internal_source_return(monkeypatch):
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    client = create_app().test_client()
    response = client.get("/settings/print-preview.pdf?desktop_preview=1&return_to=%2Forders%2F%3Fpage%3D2")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="returnToErp" href="/orders/?page=2"' in html
    assert "返回上一级" in html
    assert "返回开单" not in html

    entry = client.get("/orders/new")
    assert entry.status_code == 200
    assert "新建销售单" in entry.get_data(as_text=True)


def test_browser_pdf_sources_still_return_real_pdf():
    """The direct endpoints retain normal browser PDF behavior, including Settings."""
    init_db()
    customer_id, sale_order_id, return_order_id = _make_order()
    client = create_app().test_client()

    for path in (
        f"/orders/{sale_order_id}/pdf",
        f"/orders/{return_order_id}/pdf",
        "/orders/summary_pdf",
        f"/accounts/summary_pdf?customer_id={customer_id}",
        "/settings/print-preview.pdf",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.mimetype == "application/pdf", path
        assert response.data.startswith(b"%PDF"), path
        assert_a4_portrait_pdf(response.data)


def test_settings_page_exposes_html_and_pdf_preview_entries(monkeypatch):
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    html = create_app().test_client().get("/settings/").get_data(as_text=True)

    assert 'id="settingsPrintPreview"' in html
    assert 'id="settingsPrintPreviewPdf"' in html
    assert "生成预览 PDF" in html
    assert "/settings/print-preview.pdf?desktop_preview=1" in html


def test_save_pdf_writes_exact_bytes_and_sanitizes_filename(monkeypatch, tmp_path):
    _install_webview(monkeypatch)
    payload = b"%PDF-1.4\nbytes-must-match\n"
    target = tmp_path / "saved.pdf"
    fake = FakeWindow(dialog_result=[str(target)])
    monkeypatch.setattr(desk, "_window", fake)

    result = desk.DesktopApi().save_pdf("../../nested/preview", _sample_pdf_data_url(payload))

    assert result == {"ok": True, "path": str(target)}
    assert target.read_bytes() == payload
    assert fake.dialog_calls[0][1]["save_filename"] == "preview.pdf"
    assert fake.dialog_calls[0][1]["file_types"] == ("PDF Files (*.pdf)",)


def test_save_pdf_keeps_pdf_extension_without_duplicate(monkeypatch, tmp_path):
    _install_webview(monkeypatch)
    target = tmp_path / "saved.pdf"
    fake = FakeWindow(dialog_result=str(target))
    monkeypatch.setattr(desk, "_window", fake)

    result = desk.DesktopApi().save_pdf("report.PDF", _sample_pdf_data_url())

    assert result["ok"] is True
    assert fake.dialog_calls[0][1]["save_filename"] == "report.PDF"


def test_save_pdf_cancel_is_not_an_error(monkeypatch):
    _install_webview(monkeypatch)
    fake = FakeWindow(dialog_result=None)
    monkeypatch.setattr(desk, "_window", fake)

    result = desk.DesktopApi().save_pdf("preview.pdf", _sample_pdf_data_url())

    assert result == {"ok": False, "cancelled": True, "message": ""}


def test_save_pdf_rejects_invalid_data_url(monkeypatch, tmp_path):
    _install_webview(monkeypatch)
    fake = FakeWindow(dialog_result=[str(tmp_path / "must-not-exist.pdf")])
    monkeypatch.setattr(desk, "_window", fake)

    assert desk.DesktopApi().save_pdf("preview.pdf", "data:text/plain;base64,QQ==") == {
        "ok": False,
        "message": "PDF 数据无效",
    }
    assert desk.DesktopApi().save_pdf("preview.pdf", "data:application/pdf;base64,not-base64!") == {
        "ok": False,
        "message": "保存失败：Only base64 data is allowed",
    }


def test_save_pdf_returns_dialog_and_write_errors(monkeypatch, tmp_path):
    _install_webview(monkeypatch)
    dialog_error = FakeWindow(dialog_error=RuntimeError("dialog unavailable"))
    monkeypatch.setattr(desk, "_window", dialog_error)
    dialog_result = desk.DesktopApi().save_pdf("preview.pdf", _sample_pdf_data_url())
    assert dialog_result["ok"] is False
    assert "dialog unavailable" in dialog_result["message"]

    blocked_target = tmp_path / "missing" / "saved.pdf"
    write_error = FakeWindow(dialog_result=[str(blocked_target)])
    monkeypatch.setattr(desk, "_window", write_error)
    write_result = desk.DesktopApi().save_pdf("preview.pdf", _sample_pdf_data_url())
    assert write_result["ok"] is False
    assert "保存失败" in write_result["message"]
    assert not Path(blocked_target).exists()
