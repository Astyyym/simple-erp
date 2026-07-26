from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, redirect, request

from erp.auth import logout_user

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
    # Permanent no-login: never show a credential form.
    next_url = _safe_next(request.values.get("next") or request.args.get("next") or "/")
    return redirect(next_url)


@auth_bp.post("/logout")
def logout():
    logout_user()
    return redirect("/")


@auth_bp.get("/logout")
def logout_get():
    logout_user()
    return redirect("/")
