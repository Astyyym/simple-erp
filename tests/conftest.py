import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))
os.environ["ERP_CONFIG_PATH"] = str(PROJECT_ROOT / "config.json")


@pytest.fixture(autouse=True)
def isolate_test_database(tmp_path, monkeypatch):
    """Route database and runtime files to a temporary folder, never live business data."""
    from erp import db as db_module
    from erp import config as config_module

    test_root = tmp_path / "runtime"
    test_root.mkdir()
    test_db = test_root / "erp.db"
    monkeypatch.setattr(db_module, "db_path", lambda: test_db)
    monkeypatch.setattr(config_module, "runtime_root", lambda: test_root)
    (test_root / "config.json").write_text(
        (PROJECT_ROOT / "config.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
