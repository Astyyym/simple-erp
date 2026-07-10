import json
import os
import sys
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    """Return the writable app folder for normal runs and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def bundled_root() -> Path:
    """Return the read-only bundled asset folder when frozen, else the source root."""
    return Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)).resolve()


def load_config() -> dict[str, Any]:
    config_path = Path(os.environ.get("ERP_CONFIG_PATH", runtime_root() / "config.json"))
    if not config_path.exists() and getattr(sys, "frozen", False):
        config_path = bundled_root() / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    config["project_root"] = str(runtime_root())
    return config


def project_path(*parts: str) -> Path:
    return runtime_root().joinpath(*parts)


def ensure_runtime_dirs() -> None:
    for dirname in ["data", "backups", "logs", "imports", "temp_pdf"]:
        project_path(dirname).mkdir(parents=True, exist_ok=True)
