"""源码运行缺少 config.json 时必须回退到出厂模板，而不是启动失败。

背景：`config.json` 是**用户/本机可写配置**，不该被 git 跟踪——一旦跟踪，开发机
改设置（缩放、主题、店铺名）就会污染仓库，且「仓库里的模板」与「出厂模板」
两份内容会持续漂移。取消跟踪后，首次运行（文件还不存在）必须仍然能起来，
所以 `load_config()` 要回退到 `packaging/default_config.json`。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from erp import config as config_module

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "packaging" / "default_config.json"


def _factory_template() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _drop_writable_config() -> Path:
    """删掉当前生效的可写配置，模拟「首次运行 / 配置不在工作区」。"""
    path = config_module.config_file_path()
    path.unlink(missing_ok=True)
    return path


def test_source_run_falls_back_to_factory_template():
    path = _drop_writable_config()
    assert not path.exists()

    config = config_module.load_config()  # 不得抛 FileNotFoundError

    factory = _factory_template()
    assert config["ui_scale"] == factory["ui_scale"] == "100"
    assert config["shop_name"] == factory["shop_name"] == "简单ERP"
    assert config["app_name"] == "简单ERP"
    assert config["host"] == "127.0.0.1"
    assert config["local_access_password_enabled"] is False


def test_fallback_reads_the_packaging_template_not_a_second_copy():
    """回退源必须就是构建用的那份厂模板，不能另有一份副本。"""
    _drop_writable_config()
    config = config_module.load_config()
    factory = _factory_template()
    for key in ("ui_scale", "ui_theme", "ui_font_weight", "print_main_business", "order_pdf_page_width_mm"):
        assert config[key] == factory[key], key


def test_existing_config_still_wins_over_template():
    path = config_module.config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"shop_name": "真实店", "ui_scale": "125"}, ensure_ascii=False), encoding="utf-8")

    config = config_module.load_config()

    assert config["shop_name"] == "真实店"
    assert config["ui_scale"] == "125"


def test_frozen_run_still_prefers_bundled_config(monkeypatch, tmp_path):
    bundled = tmp_path / "bundle"
    bundled.mkdir()
    (bundled / "config.json").write_text(
        json.dumps({"shop_name": "内置店", "ui_scale": "150"}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(config_module, "bundled_root", lambda: bundled)
    monkeypatch.setattr(config_module, "is_frozen", lambda: True)
    _drop_writable_config()

    config = config_module.load_config()

    assert config["shop_name"] == "内置店"
    assert config["ui_scale"] == "150"


def test_save_config_writes_real_file_so_fallback_is_first_run_only():
    _drop_writable_config()

    saved = config_module.save_config({"shop_name": "新店"})

    assert saved["shop_name"] == "新店"
    path = config_module.config_file_path()
    assert path.exists(), "保存后必须落到可写路径，否则每次启动都回到出厂默认"
    assert json.loads(path.read_text(encoding="utf-8"))["shop_name"] == "新店"


def test_workspace_config_is_ignored_while_template_stays_tracked():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^config\.json\s*$", ignore, re.M), "根 config.json 必须被 .gitignore 排除"
    assert TEMPLATE.exists(), "出厂模板必须留在仓库里，它是源码运行的回退源"


def test_template_has_no_private_or_absolute_paths():
    factory = _factory_template()
    assert factory["shop_name"] == factory["app_name"] == "简单ERP"
    for key in ("print_order_phone", "print_order_address", "print_maker_name"):
        assert factory[key] == ""
    assert not any(key in factory for key in ("data_root", "project_root", "api_key", "token"))
    assert "C:\\" not in TEMPLATE.read_text(encoding="utf-8")
