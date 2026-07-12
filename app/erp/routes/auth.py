from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for, session

from erp.auth import ensure_auth_defaults, is_authenticated, login_user, logout_user, verify_credentials
from erp.config import load_config

auth_bp = Blueprint("auth", __name__)


def _safe_next(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return "/"
    # Only same-origin relative paths.
    if value.startswith("/") and not value.startswith("//"):
        return value
    parsed = urlparse(value)
    if not parsed.scheme and not parsed.netloc and value.startswith("/"):
        return value
    return "/"


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    cfg = ensure_auth_defaults(load_config())
    if is_authenticated():
        return redirect(_safe_next(request.args.get("next") or "/"))

    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = _safe_next(request.form.get("next") or request.args.get("next") or "/")
        if verify_credentials(username, password, cfg):
            login_user(username)
            return redirect(next_url)
        error = "用户名或密码不正确"
        return render_template(
            "auth/login.html",
            error=error,
            username=username,
            next_url=next_url,
            shop_name=cfg.get("shop_name", ""),
        )

    next_url = _safe_next(request.args.get("next") or "/")
    return render_template(
        "auth/login.html",
        error=error,
        username="",
        next_url=next_url,
        shop_name=cfg.get("shop_name", ""),
    )


@auth_bp.post("/logout")
def logout():
    logout_user()
    flash("已退出登录", "success")
    return redirect(url_for("auth.login"))


@auth_bp.get("/logout")
def logout_get():
    logout_user()
    return redirect(url_for("auth.login"))
