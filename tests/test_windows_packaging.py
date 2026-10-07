"""Build inputs must not read or ship the workstation's private config."""
import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_bundles_only_generic_configuration():
    tree = ast.parse((ROOT / "简单ERP.spec").read_text(encoding="utf-8"))
    analysis = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis")
    datas = next(keyword.value for keyword in analysis.keywords if keyword.arg == "datas")
    configuration_sources = []
    for item in datas.elts:
        expression = ast.Expression(item.elts[0])
        source = Path(eval(compile(expression, "<build-input>", "eval"), {"project_root": ROOT}))
        if source.name in {"config.json", "default_config.json"}:
            configuration_sources.append(source)
    assert configuration_sources == [ROOT / "packaging" / "default_config.json"]
    settings = json.loads(configuration_sources[0].read_text(encoding="utf-8"))
    assert settings["shop_name"] == settings["app_name"] == "简单ERP"
    assert settings["host"] == "127.0.0.1"
    assert settings["database_path"] == "data/erp.db"
    assert settings["local_access_password_enabled"] is False
    for key in ("local_access_password_hash", "local_access_username", "print_order_phone", "print_order_address", "print_maker_name"):
        assert settings[key] == ""
    assert not any(key in settings for key in ("data_root", "project_root", "api_key", "token"))
    bat = (ROOT / "打包Windows桌面版.bat").read_text(encoding="utf-8")
    assert "copy config.json" not in bat
    assert "packaging\\default_config.json" in bat


def test_source_health_and_page_expose_exact_authorized_version(monkeypatch):
    from erp import create_app

    monkeypatch.delenv("ERP_VERSION", raising=False)
    # 版本号从 VERSION 读取，不在测试里硬编码：升版只改 VERSION 一处。
    declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", declared), declared
    app = create_app()
    client = app.test_client()
    assert client.get("/health").get_json()["version"] == declared
    assert declared in client.get("/").get_data(as_text=True)


def test_windows_spec_version_resource_and_public_bundle_name(monkeypatch):
    pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    observed = {}

    def analysis(*args, **kwargs):
        return SimpleNamespace(pure=[], zipped_data=[], scripts=[], binaries=[], zipfiles=[], datas=[(Path(source).name, source, "DATA") for source, destination in kwargs["datas"]])

    def exe(*args, **kwargs):
        observed.update(kwargs)
        return object()

    monkeypatch.chdir(ROOT)
    scope = {"Analysis": analysis, "PYZ": lambda *args, **kwargs: object(), "EXE": exe, "COLLECT": lambda *args, **kwargs: object()}
    exec(compile((ROOT / "简单ERP.spec").read_text(encoding="utf-8"), "简单ERP.spec", "exec"), scope)
    assert "version" in observed, "Windows file properties must carry the release version"
    resource = observed["version"]
    declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    major, minor, patch = (int(part) for part in declared.lstrip("vV").split("."))
    assert resource.ffi.fileVersionMS == resource.ffi.productVersionMS == (major << 16 | minor)
    assert resource.ffi.fileVersionLS == resource.ffi.productVersionLS == (patch << 16)
    strings = {item.name: item.val for child in resource.kids if child.__class__.__name__ == "StringFileInfo" for table in child.kids for item in table.kids}
    assert strings["ProductVersion"] == declared
    assert strings["FileVersion"] == f"{major}.{minor}.{patch}.0"
    configuration = [(name, source) for name, source, kind in scope["a"].datas if name == "config.json"]
    assert configuration == [("config.json", str(ROOT / "packaging" / "default_config.json"))]
