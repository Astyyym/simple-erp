import os
from pathlib import Path
from datetime import timedelta

from flask import Flask, render_template, redirect, request, url_for, session

from .auth import ensure_auth_defaults, is_authenticated, current_username
from .config import ensure_data_location_initialized, load_config, bundled_root, runtime_root
from .db import init_db, integrity_check
from .routes.accounts import accounts_bp
from .routes.auth import auth_bp
from .routes.customers import customers_bp
from .routes.orders import orders_bp
from .routes.products import products_bp
from .routes.recycle import recycle_bp
from .routes.settings import settings_bp
from .utils.logging import setup_logging


def app_version() -> str:
    env_version = os.environ.get("ERP_VERSION")
    if env_version:
        return env_version
    for root in (runtime_root(), bundled_root()):
        version_path = Path(root) / "VERSION"
        if version_path.exists():
            return version_path.read_text(encoding="utf-8").strip()
    return "v0.0.0"


def create_app() -> Flask:
    ensure_data_location_initialized()
    setup_logging()
    app = Flask(__name__)
    cfg = ensure_auth_defaults(load_config())
    app.config["ERP_CONFIG"] = cfg
    app.config["ERP_VERSION"] = app_version()
    app.config["SECRET_KEY"] = os.environ.get("ERP_SECRET_KEY", "local-fire-erp-dev-secret")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    init_db()

    @app.context_processor
    def inject_globals():
        cfg_now = load_config()
        app.config["ERP_CONFIG"] = cfg_now
        return {
            "app_version": app.config["ERP_VERSION"],
            "erp_config": cfg_now,
            "shop_name": cfg_now.get("shop_name", ""),
            "ui_theme": cfg_now.get("ui_theme", "light"),
            "ui_scale": cfg_now.get("ui_scale", "100"),
            "current_user": current_username(),
            "auth_enabled": not os.environ.get("ERP_DISABLE_AUTH", "").strip().lower()
            in {"1", "true", "yes", "on"},
        }

    @app.before_request
    def require_login():
        if is_authenticated():
            return None
        endpoint = request.endpoint or ""
        path = request.path or ""
        # Public endpoints
        if endpoint in {"auth.login", "auth.logout", "auth.logout_get", "health"}:
            return None
        if path.startswith("/static"):
            return None
        # Keep health JSON public for smoke tests / packaging probes
        if path == "/health":
            return None
        return redirect(url_for("auth.login", next=request.full_path if request.query_string else request.path))

    app.register_blueprint(auth_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(recycle_bp)
    app.register_blueprint(settings_bp)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "database": integrity_check(),
            "version": app.config["ERP_VERSION"],
            "data_root": load_config().get("data_root"),
            "auth": "disabled" if os.environ.get("ERP_DISABLE_AUTH") else "enabled",
        }

    return app
