import os
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Flask, render_template, request

from .auth import ensure_auth_defaults, current_username, is_desktop_shell
from .config import ensure_data_location_initialized, load_config, bundled_root, runtime_root
from .db import get_db, init_db, integrity_check
from .utils.money import cents_to_yuan
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


def _normalized_origin(value: str | None) -> tuple[str, str, int] | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        scheme = parsed.scheme.lower()
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, parsed.hostname.lower(), port


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
            "is_desktop": is_desktop_shell(),
            # Permanent no-login product default.
            "auth_enabled": False,
        }

    @app.before_request
    def reject_cross_origin_mutations():
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        fetch_site = request.headers.get("Sec-Fetch-Site", "").strip().lower()
        if fetch_site in {"cross-site", "same-site"}:
            return "跨站写请求已拒绝", 403
        origin = request.headers.get("Origin")
        if origin is not None and _normalized_origin(origin) != _normalized_origin(request.host_url):
            return "跨站写请求已拒绝", 403
        return None

    # Login gate removed: permanent free access for local single-machine use.
    # is_authenticated() remains True for any residual callers.

    app.register_blueprint(auth_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(recycle_bp)
    app.register_blueprint(settings_bp)

    @app.get("/")
    def index():
        today = date.today()
        today_iso = today.isoformat()
        with get_db() as conn:
            metrics = conn.execute(
                """
                SELECT
                    COALESCE(SUM(total_amount_cents), 0) AS net_amount_cents,
                    COUNT(CASE WHEN order_type='sale' THEN 1 END) AS sales_count,
                    COUNT(CASE WHEN order_type='return' THEN 1 END) AS returns_count
                FROM orders
                WHERE order_date=?
                  AND status IN ('saved', 'printed')
                  AND deleted_at IS NULL
                """,
                (today_iso,),
            ).fetchone()
            recent_rows = conn.execute(
                """
                SELECT o.id, o.order_no, o.order_date, o.total_amount_cents,
                       o.order_type, o.status, o.created_at,
                       COALESCE(c.name, '（未知客户）') AS customer_name
                FROM orders AS o
                LEFT JOIN customers AS c ON c.id=o.customer_id
                WHERE o.order_type IN ('sale', 'return')
                  AND o.status IN ('saved', 'printed')
                  AND o.deleted_at IS NULL
                ORDER BY o.created_at DESC, o.id DESC
                LIMIT 5
                """
            ).fetchall()
        recent_orders = []
        for row in recent_rows:
            order = dict(row)
            created_at = datetime.fromisoformat(order["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            order["created_at_display"] = created_at.astimezone().strftime("%Y-%m-%d %H:%M")
            amount_cents = order["total_amount_cents"]
            amount_sign = "−" if amount_cents < 0 else ""
            order["amount_display"] = f"{amount_sign}¥{cents_to_yuan(abs(amount_cents))}"
            recent_orders.append(order)
        return render_template(
            "index.html",
            today_display=f"{today.year}年{today.month}月{today.day}日",
            today_net_amount_cents=metrics["net_amount_cents"],
            today_sales_count=metrics["sales_count"],
            today_returns_count=metrics["returns_count"],
            recent_orders=recent_orders,
            cents_to_yuan=cents_to_yuan,
        )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "database": integrity_check(),
            "version": app.config["ERP_VERSION"],
            "data_root": load_config().get("data_root"),
            "auth": "disabled",
        }

    return app
