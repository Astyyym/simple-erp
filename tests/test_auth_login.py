from erp import create_app
from erp.auth import DEFAULT_PASSWORD, DEFAULT_USERNAME, ensure_auth_defaults, verify_credentials
from erp.config import load_config
from erp.db import init_db


def test_unauthenticated_request_redirects_to_login(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    app = create_app()
    client = app.test_client()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_login_success_and_logout(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    app = create_app()
    client = app.test_client()

    bad = client.post(
        "/login",
        data={"username": DEFAULT_USERNAME, "password": "wrong", "next": "/"},
        follow_redirects=False,
    )
    assert bad.status_code == 200
    assert "用户名或密码不正确" in bad.get_data(as_text=True)

    ok = client.post(
        "/login",
        data={"username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD, "next": "/settings/"},
        follow_redirects=False,
    )
    assert ok.status_code in (302, 303)
    assert "/settings/" in ok.headers["Location"]

    page = client.get("/settings/")
    assert page.status_code == 200
    assert "公司名称" in page.get_data(as_text=True)
    assert "退出登录" in page.get_data(as_text=True)

    out = client.post("/logout", follow_redirects=False)
    assert out.status_code in (302, 303)
    assert "/login" in out.headers["Location"]
    blocked = client.get("/orders/new", follow_redirects=False)
    assert blocked.status_code in (302, 303)
    assert "/login" in blocked.headers["Location"]


def test_login_page_has_password_visibility_toggle(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    app = create_app()
    client = app.test_client()
    html = client.get("/login").get_data(as_text=True)
    assert 'id="togglePassword"' in html
    assert "显示密码" in html
    assert 'type="password"' in html


def test_health_stays_public_when_auth_enabled(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    app = create_app()
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data.get("auth") == "enabled"


def test_default_credentials_verify_after_bootstrap(monkeypatch):
    monkeypatch.delenv("ERP_DISABLE_AUTH", raising=False)
    init_db()
    create_app()
    cfg = ensure_auth_defaults(load_config())
    assert cfg["local_access_password_enabled"] is True
    assert cfg["local_access_username"] == DEFAULT_USERNAME
    assert cfg["local_access_password_hash"]
    assert verify_credentials(DEFAULT_USERNAME, DEFAULT_PASSWORD, cfg) is True
    assert verify_credentials(DEFAULT_USERNAME, "nope", cfg) is False
