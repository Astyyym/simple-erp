"""Local access helpers for the desktop ERP.

Product default (2026-07-26): permanent no-login. Session helpers remain for
compatibility with older tests and optional future re-enablement.
"""

from __future__ import annotations

import os
from typing import Any

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

# Legacy defaults kept only for optional credential verification helpers.
DEFAULT_USERNAME = "wangyanli"
DEFAULT_PASSWORD = "zzzz"

SESSION_USER_KEY = "erp_auth_user"


def auth_disabled() -> bool:
    """
    Auth is permanently off for this product version.

    ERP_DISABLE_AUTH remains recognized for older test/env compatibility,
    but the product no longer requires login even when it is unset.
    """
    return True


def is_desktop_shell() -> bool:
    """True when running inside the pywebview desktop shell (desktop_app.py)."""
    return os.environ.get("ERP_DESKTOP", "").strip().lower() in {"1", "true", "yes", "on"} or os.environ.get(
        "ERP_DESKTOP_SHELL", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def ensure_auth_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """
    Keep local_access_* keys present and force the password gate off.

    Reuses legacy keys:
    - local_access_password_enabled
    - local_access_password_hash
    - local_access_username
    """
    from erp.config import save_config

    username = str(config.get("local_access_username") or "").strip()
    enabled = bool(config.get("local_access_password_enabled", False))
    password_hash = str(config.get("local_access_password_hash") or "").strip()

    need_write = False
    updates: dict[str, Any] = {}

    if config.get("local_access_username") != username:
        updates["local_access_username"] = username
        need_write = True
    if enabled:
        # Permanent no-login: never keep the gate on.
        updates["local_access_password_enabled"] = False
        need_write = True
    # Do not invent a default password hash anymore.
    if "local_access_password_hash" not in config and password_hash == "":
        updates["local_access_password_hash"] = ""
        need_write = True

    if need_write:
        return save_config(updates, base=config)
    return config


def verify_credentials(username: str, password: str, config: dict[str, Any] | None = None) -> bool:
    """Legacy helper; product UI no longer uses password login."""
    from erp.config import load_config

    cfg = ensure_auth_defaults(config or load_config())
    if not bool(cfg.get("local_access_password_enabled", False)):
        return False
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
    # Permanent no-login: every request is treated as authenticated.
    return True


def hash_password(password: str) -> str:
    """Optional utility if credentials are ever re-enabled via config."""
    return generate_password_hash(password or "")
