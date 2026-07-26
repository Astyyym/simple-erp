"""With permanent no-login, print/PDF and business pages stay open without a session."""

from uuid import uuid4

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _make_order():
    suffix = uuid4().hex[:8]
    customer_name = f"鉴权打印-{suffix}"
    customer_id = create_customer(customer_name)
    order_id = create_order_from_typed_rows(
        customer_id,
        f"AUTH-{suffix}",
        [{"product_name": "鉴权阀", "unit": "只", "unit_price_yuan": "15", "quantity": "1"}],
        status="saved",
    )
    return customer_id, order_id


def test_pdf_open_without_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    monkeypatch.delenv("ERP_DESKTOP", raising=False)
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()

    resp = client.get(f"/orders/{order_id}/pdf", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:4] == b"%PDF"


def test_summary_and_print_preview_open_without_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    monkeypatch.delenv("ERP_DESKTOP", raising=False)
    init_db()
    customer_id, _ = _make_order()
    client = create_app().test_client()

    for path in (
        f"/accounts/summary_pdf?customer_id={customer_id}",
        "/orders/summary_pdf",
        "/settings/print-preview",
        "/settings/print-preview.pdf",
        "/",
    ):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 200, path


def test_non_print_pages_open_without_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    client = create_app().test_client()
    for path in ("/orders/new", "/orders/", "/settings/", "/customers/"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 200, path
        assert "退出登录" not in resp.get_data(as_text=True)


def test_health_public_auth_disabled(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    client = create_app().test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body.get("auth") == "disabled"


def test_desktop_mode_also_no_login(monkeypatch):
    """ERP_DESKTOP only changes print navigation; product is already no-login."""
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()

    ok = client.get(f"/orders/{order_id}/pdf")
    assert ok.status_code == 200
    assert ok.data[:4] == b"%PDF"

    # Logout is a no-op for access control; PDF remains open.
    client.post("/logout")
    again = client.get(f"/orders/{order_id}/pdf", follow_redirects=False)
    assert again.status_code == 200
    assert again.data[:4] == b"%PDF"


def test_desktop_shell_marker_and_in_shell_print_helpers(monkeypatch):
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert 'data-desktop="1"' in html
    assert "function openPrintUrl(url)" in html
    assert "desktop_preview=1" in html
    assert "openPrintUrl(result.pdf_url)" in html
