from erp import create_app
from erp.auth import ensure_auth_defaults, is_authenticated
from erp.config import load_config
from erp.db import init_db


def test_home_is_public_without_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    app = create_app()
    client = app.test_client()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "退出登录" not in html
    assert 'name="username"' not in html


def test_business_pages_open_without_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    client = create_app().test_client()
    for path in ("/orders/new", "/settings/", "/customers/", "/products/"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 200, path


def test_login_route_redirects_home(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    client = create_app().test_client()
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].endswith("/") or resp.headers["Location"] == "/"
    # No login form rendered even with follow.
    page = client.get("/login", follow_redirects=True)
    assert page.status_code == 200
    assert "用户名或密码不正确" not in page.get_data(as_text=True)
    assert 'id="togglePassword"' not in page.get_data(as_text=True)


def test_logout_routes_go_home(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    client = create_app().test_client()
    out = client.post("/logout", follow_redirects=False)
    assert out.status_code in (302, 303)
    assert out.headers["Location"].endswith("/") or out.headers["Location"] == "/"
    out_get = client.get("/logout", follow_redirects=False)
    assert out_get.status_code in (302, 303)


def test_health_reports_auth_disabled(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    app = create_app()
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data.get("auth") == "disabled"


def test_auth_defaults_force_password_gate_off(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    create_app()
    cfg = ensure_auth_defaults(load_config())
    assert cfg["local_access_password_enabled"] is False
    assert is_authenticated() is True
