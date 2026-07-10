import os
from pathlib import Path
from flask import Flask, render_template

from .config import ensure_runtime_dirs, load_config, bundled_root, runtime_root
from .db import init_db, integrity_check
from .routes.accounts import accounts_bp
from .routes.customers import customers_bp
from .routes.orders import orders_bp
from .routes.products import products_bp
from .routes.recycle import recycle_bp
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
    ensure_runtime_dirs()
    setup_logging()
    app = Flask(__name__)
    app.config["ERP_CONFIG"] = load_config()
    app.config["ERP_VERSION"] = app_version()
    app.config["SECRET_KEY"] = "local-fire-erp-dev-secret"
    init_db()

    @app.context_processor
    def inject_version():
        return {"app_version": app.config["ERP_VERSION"]}

    app.register_blueprint(products_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(recycle_bp)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "database": integrity_check(), "version": app.config["ERP_VERSION"]}

    return app
