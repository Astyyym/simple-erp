import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolate_test_database(tmp_path, monkeypatch):
    """Route database and runtime files to a temporary folder, never live business data."""
    from erp import db as db_module
    from erp import config as config_module

    test_root = tmp_path / "runtime"
    test_root.mkdir()
    test_db = test_root / "data" / "erp.db"
    test_db.parent.mkdir(parents=True, exist_ok=True)
    location = tmp_path / "location" / "data_location.json"
    location.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("ERP_CONFIG_PATH", str(test_root / "config.json"))
    monkeypatch.setenv("ERP_LOCATION_FILE", str(location))
    monkeypatch.setenv("ERP_DISABLE_AUTH", "1")
    monkeypatch.delenv("ERP_DATA_ROOT", raising=False)
    # Desktop-shell markers must not leak across tests (order-dependent data-desktop="1").
    monkeypatch.delenv("ERP_DESKTOP", raising=False)
    monkeypatch.delenv("ERP_DESKTOP_SHELL", raising=False)

    monkeypatch.setattr(config_module, "runtime_root", lambda: test_root)
    monkeypatch.setattr(config_module, "is_frozen", lambda: False)
    monkeypatch.setattr(db_module, "db_path", lambda: test_db)

    (test_root / "config.json").write_text(
        (PROJECT_ROOT / "config.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Remember data_root as test_root so project_path aligns with db layout under data/.
    config_module.save_data_root(test_root)
