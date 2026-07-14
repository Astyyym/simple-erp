import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[2]

# UI / print defaults merged into config when keys are missing.
CONFIG_DEFAULTS: dict[str, Any] = {
    "app_name": "简单ERP",
    "shop_name": "简单ERP",
    "host": "127.0.0.1",
    "port": 5000,
    "debug": False,
    "database_path": "data/erp.db",
    "backup_retention_days": 30,
    "monthly_backup_retention_months": 12,
    "print_offset_x_mm": 0,
    "print_offset_y_mm": 0,
    "print_scale": 1.0,
    # 一联二联连续纸撕开后：241×140；两联未撕约 280 高，禁止当单页高度
    "printer_paper_width_mm": 241,
    "printer_paper_height_mm": 140,
    "local_access_password_enabled": True,
    "local_access_password_hash": "",
    "local_access_username": "wangyanli",
    "ui_theme": "light",  # light | dark | system
    "ui_scale": "100",  # 100 | 125 | 150
    "print_order_phone": "REDACTED_PHONE_2　REDACTED_PHONE_3　REDACTED_PHONE_1（支付宝）",
    "print_order_address": "REDACTED_ADDRESS",
    "print_main_business": "软密封闸阀，蝶阀，铜芯硬密封（国，韩标）过滤器，止回阀等消防闸门",
    "print_legal_note": "本销售单等同于合同，具有法律效力，收货人签字或附托运物流单号生效，直至货款结清",
    "print_maker_name": "REDACTED_CONTACT",
    "print_receiver_label": "收货人：____________",
}

UI_SCALE_CHOICES = ("100", "125", "150")
UI_THEME_CHOICES = ("light", "dark", "system")
MIGRATE_DIRNAMES = ("data", "backups", "imports", "logs")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def runtime_root() -> Path:
    """Return the writable app folder for normal runs and PyInstaller builds."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def bundled_root() -> Path:
    """Return the read-only bundled asset folder when frozen, else the source root."""
    return Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)).resolve()


def location_file() -> Path:
    """Path memory only — not the business database."""
    override = os.environ.get("ERP_LOCATION_FILE")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "简单ERP" / "data_location.json"


def default_data_root() -> Path:
    """Recommended data root when nothing is remembered yet."""
    if is_frozen():
        return (Path.home() / "Documents" / "简单ERP数据").resolve()
    # Source / tests: same as runtime_root (project root, or monkeypatched tmp).
    return runtime_root().resolve()


def default_data_root_display() -> str:
    if is_frozen():
        return str(Path.home() / "Documents" / "简单ERP数据")
    return str(runtime_root().resolve())


def resolve_data_root() -> Path:
    env = os.environ.get("ERP_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()

    loc = location_file()
    if loc.exists():
        try:
            payload = json.loads(loc.read_text(encoding="utf-8"))
            root = payload.get("data_root")
            if root:
                return Path(str(root)).expanduser().resolve()
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    return default_data_root()


def save_data_root(path: Path | str) -> Path:
    root = Path(path).expanduser().resolve()
    loc = location_file()
    loc.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_root": str(root),
        "set_at": datetime.now().isoformat(timespec="seconds"),
        "version": 1,
    }
    loc.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def data_root() -> Path:
    return resolve_data_root()


def config_file_path() -> Path:
    return Path(os.environ.get("ERP_CONFIG_PATH", runtime_root() / "config.json"))


def project_path(*parts: str) -> Path:
    """Writable business paths live under data_root (db, backups, logs, …)."""
    return data_root().joinpath(*parts)


def ensure_runtime_dirs(root: Path | None = None) -> None:
    base = Path(root) if root is not None else data_root()
    for dirname in ["data", "backups", "logs", "imports", "temp_pdf"]:
        (base / dirname).mkdir(parents=True, exist_ok=True)


def ensure_data_location_initialized() -> Path:
    """
    Resolve data root, create subdirs, and remember path when appropriate.

    - ERP_DATA_ROOT: use it, do not rewrite location unless missing and frozen.
    - Frozen first run: default Documents folder + write location json.
    - Source: default project/runtime root; write location only if user already has one
      or after explicit migrate/save from settings.
    """
    env = os.environ.get("ERP_DATA_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        ensure_runtime_dirs(root)
        return root

    loc = location_file()
    if loc.exists():
        root = resolve_data_root()
        ensure_runtime_dirs(root)
        return root

    root = default_data_root()
    ensure_runtime_dirs(root)
    if is_frozen():
        save_data_root(root)
    return root


def _merge_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    merged = dict(CONFIG_DEFAULTS)
    merged.update(raw or {})
    # Normalize UI choices.
    scale = str(merged.get("ui_scale", "100")).replace("%", "")
    if scale not in UI_SCALE_CHOICES:
        scale = "100"
    merged["ui_scale"] = scale
    theme = str(merged.get("ui_theme", "light")).lower()
    if theme not in UI_THEME_CHOICES:
        theme = "light"
    merged["ui_theme"] = theme
    return merged


def load_config() -> dict[str, Any]:
    config_path = config_file_path()
    if not config_path.exists() and is_frozen():
        bundled = bundled_root() / "config.json"
        if bundled.exists():
            config_path = bundled
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    config = _merge_defaults(raw)
    config["project_root"] = str(runtime_root())
    config["data_root"] = str(data_root())
    return config


def writable_config_path() -> Path:
    """Always write beside the app (or ERP_CONFIG_PATH), never into _MEIPASS only."""
    return Path(os.environ.get("ERP_CONFIG_PATH", runtime_root() / "config.json"))


def save_config(updates: dict[str, Any], *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge updates into config and persist. shop_name and app_name stay in sync when either is set."""
    current = dict(base) if base is not None else load_config()
    # Drop runtime-only keys before save.
    for key in ("project_root", "data_root"):
        current.pop(key, None)

    payload = dict(current)
    payload.update(updates or {})

    if "shop_name" in updates and "app_name" not in updates:
        payload["app_name"] = str(updates["shop_name"]).strip()
    if "app_name" in updates and "shop_name" not in updates:
        payload["shop_name"] = str(updates["app_name"]).strip()
    if "shop_name" in updates:
        payload["shop_name"] = str(payload.get("shop_name", "")).strip()
        payload["app_name"] = payload["shop_name"]

    if "ui_scale" in payload:
        scale = str(payload["ui_scale"]).replace("%", "")
        if scale not in UI_SCALE_CHOICES:
            raise ValueError("界面缩放仅支持 100%、125%、150%")
        payload["ui_scale"] = scale
    if "ui_theme" in payload:
        theme = str(payload["ui_theme"]).lower()
        if theme not in UI_THEME_CHOICES:
            raise ValueError("主题仅支持 浅色 / 深色 / 跟随系统")
        payload["ui_theme"] = theme

    # Never persist derived keys.
    payload.pop("project_root", None)
    payload.pop("data_root", None)

    path = writable_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return load_config()


def migrate_data_root(new_root: Path | str) -> Path:
    """
    Copy business folders to new_root, then point location json there.
    Refuses to overwrite an existing non-empty erp.db at the destination.
    Does not delete the old directory.
    """
    old_root = data_root().resolve()
    target = Path(new_root).expanduser().resolve()
    if target == old_root:
        ensure_runtime_dirs(target)
        return target

    target_db = target / "data" / "erp.db"
    if target_db.exists() and target_db.stat().st_size > 0:
        raise ValueError(f"目标目录已存在数据库，拒绝覆盖：{target_db}")

    target.mkdir(parents=True, exist_ok=True)
    for name in MIGRATE_DIRNAMES:
        src = old_root / name
        dst = target / name
        if not src.exists():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if dst.exists():
            # Merge-copy files; still protected by erp.db check above for data/.
            for item in src.rglob("*"):
                rel = item.relative_to(src)
                dest_item = dst / rel
                if item.is_dir():
                    dest_item.mkdir(parents=True, exist_ok=True)
                else:
                    dest_item.parent.mkdir(parents=True, exist_ok=True)
                    if dest_item.exists() and name == "data" and dest_item.name.startswith("erp.db"):
                        raise ValueError(f"目标目录已存在数据库文件，拒绝覆盖：{dest_item}")
                    shutil.copy2(item, dest_item)
        else:
            shutil.copytree(src, dst)

    ensure_runtime_dirs(target)
    # Verify db landed if source had one.
    old_db = old_root / "data" / "erp.db"
    if old_db.exists() and old_db.stat().st_size > 0:
        new_db = target / "data" / "erp.db"
        if not new_db.exists() or new_db.stat().st_size <= 0:
            raise RuntimeError("迁移后未找到有效的 erp.db，已中止切换")

    save_data_root(target)
    return target
