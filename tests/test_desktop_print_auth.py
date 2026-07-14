"""Desktop print must reuse pywebview session; browser still requires login."""

from uuid import uuid4

from erp import create_app
from erp.auth import DEFAULT_PASSWORD, DEFAULT_USERNAME
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _login(client):
    return client.post(
        "/login",
        data={"username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD, "next": "/"},
        follow_redirects=False,
    )


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


def test_unauthenticated_pdf_redirects_to_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    monkeypatch.delenv("ERP_DESKTOP", raising=False)
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()

    resp = client.get(f"/orders/{order_id}/pdf", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_authenticated_user_can_open_pdf(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get(f"/orders/{order_id}/pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:4] == b"%PDF"


def test_unauthenticated_summary_and_print_preview_redirect(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    monkeypatch.delenv("ERP_DESKTOP", raising=False)
    init_db()
    customer_id, _ = _make_order()
    client = create_app().test_client()

    for path in (
        f"/accounts/summary_pdf?customer_id={customer_id}",
        "/orders/summary_pdf",
        "/settings/print-preview",
        "/",
    ):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 303), path
        assert "/login" in resp.headers["Location"], path


def test_non_print_pages_still_require_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    client = create_app().test_client()
    for path in ("/orders/new", "/orders/", "/settings/", "/customers/"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["Location"]


def test_health_public_with_auth_enabled(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    client = create_app().test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_desktop_mode_does_not_disable_auth(monkeypatch):
    """ERP_DESKTOP only changes print navigation; it is not a login bypass."""
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()

    blocked = client.get(f"/orders/{order_id}/pdf", follow_redirects=False)
    assert blocked.status_code in (302, 303)
    assert "/login" in blocked.headers["Location"]

    assert _login(client).status_code in (302, 303)
    ok = client.get(f"/orders/{order_id}/pdf")
    assert ok.status_code == 200
    assert ok.data[:4] == b"%PDF"

    # After logout, same PDF URL requires login again (no lingering free access).
    client.post("/logout")
    again = client.get(f"/orders/{order_id}/pdf", follow_redirects=False)
    assert again.status_code in (302, 303)
    assert "/login" in again.headers["Location"]


def test_desktop_shell_marker_and_in_shell_print_helpers(monkeypatch):
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert 'data-desktop="1"' in html
    assert "function openPrintUrl(url)" in html
    assert "window.location.assign(url)" in html
    assert "openPrintUrl(result.pdf_url)" in html
