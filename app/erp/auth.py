"""Simple single-account local login for the desktop ERP."""

from __future__ import annotations

import os
from typing import Any

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

# Built-in shop account (tell the operator once; change later via config if needed).
DEFAULT_USERNAME = "simple_erp"
DEFAULT_PASSWORD = "SimpleERP@2026"

SESSION_USER_KEY = "erp_auth_user"


def auth_disabled() -> bool:
    """Tests / emergency bypass. Production desktop should leave this unset."""
    return os.environ.get("ERP_DISABLE_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}


def ensure_auth_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """
    Make sure login fields exist.

    Reuses legacy keys:
    - local_access_password_enabled
    - local_access_password_hash
    Adds:
    - local_access_username
    """
    from erp.config import save_config

    username = str(config.get("local_access_username") or "").strip() or DEFAULT_USERNAME
    enabled = bool(config.get("local_access_password_enabled", True))
    password_hash = str(config.get("local_access_password_hash") or "").strip()

    need_write = False
    updates: dict[str, Any] = {}

    if config.get("local_access_username") != username:
        updates["local_access_username"] = username
        need_write = True
    if not enabled:
        # Login gate is always on for this product version.
        updates["local_access_password_enabled"] = True
        need_write = True
    if not password_hash:
        updates["local_access_password_hash"] = generate_password_hash(DEFAULT_PASSWORD)
        need_write = True

    if need_write:
        return save_config(updates, base=config)
    return config


def verify_credentials(username: str, password: str, config: dict[str, Any] | None = None) -> bool:
    from erp.config import load_config

    cfg = ensure_auth_defaults(config or load_config())
    expected_user = str(cfg.get("local_access_username") or DEFAULT_USERNAME).strip()
    password_hash = str(cfg.get("local_access_password_hash") or "")
    if not password_hash:
        return False
    if (username or "").strip() != expected_user:
        return False
    try:
        return check_password_hash(password_hash, password or "")
    except Exception:
        return False


def login_user(username: str) -> None:
    session[SESSION_USER_KEY] = (username or "").strip()
    session.permanent = True


def logout_user() -> None:
    session.pop(SESSION_USER_KEY, None)


def current_username() -> str:
    try:
        return str(session.get(SESSION_USER_KEY) or "").strip()
    except Exception:
        # Outside request context (PDF generation, CLI, etc.)
        return ""


def is_authenticated() -> bool:
    if auth_disabled():
        return True
    return bool(current_username())
