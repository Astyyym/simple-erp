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


def test_all_batch_scripts_have_crlf_line_endings():
    """`.bat` 必须是 CRLF，否则 cmd.exe 行式解析失步、步骤被静默跳过。

    受控实验（2026-10-12，真实 cmd.exe，2×2 对照）确认**决定因素是行尾、不是编码**：

    | 编码 | chcp | 行尾 | 结果 |
    |---|---|---|---|
    | UTF-8 | 65001 | CRLF | ✓ 全部步骤执行 |
    | UTF-8 | 65001 | LF | ✗ 跳过（stdout 空） |
    | GBK | 936 | CRLF | ✓ 全部步骤执行 |
    | GBK | 936 | LF | ✗ 跳过（stdout 空） |

    所以**不要**以「编码不是 UTF-8」为理由去转 GBK——UTF-8 + CRLF 完全正常。
    详见 `skills/simple-business-erp-development/references/windows-bat-crlf-invocation.md`。

    用 `read_bytes` 数字节，**不要**用 `read_text().splitlines()` 或
    `grep -c $'\\r'`：两者都会把行尾 normalize/吞掉，导致 LF-only 也判成通过。
    """
    scripts = sorted(ROOT.glob("*.bat"))
    assert scripts, "根目录应存在批处理脚本"
    for script in scripts:
        raw = script.read_bytes()
        bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
        assert raw.count(b"\r\n") > 0, f"{script.name} 不是 CRLF 行尾（cmd.exe 会解析失步）"
        assert bare_lf == 0, f"{script.name} 含 {bare_lf} 个裸 LF，行尾混用会让 cmd.exe 静默跳过步骤"


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


def test_specs_bundle_mobile_templates_and_static_with_qrcode():
    """手机端资源必须能被 PyInstaller 带进包 —— 漏了源码正常、EXE 上永远 404/500。

    这条钉的是**构建输入**（不是构建产物）：两个 spec 的 `datas` 都是整目录
    `erp/templates` / `erp/static`，所以 `templates/mobile/` 与 `static/css/mobile.css`
    天然被包含；再确认这些文件真的在那些目录里，以及 `qrcode` 在 `hiddenimports`。
    """
    required_files = [
        ROOT / "app" / "erp" / "templates" / "mobile" / "base.html",
        ROOT / "app" / "erp" / "templates" / "mobile" / "today.html",
        ROOT / "app" / "erp" / "static" / "css" / "mobile.css",
        ROOT / "app" / "erp" / "static" / "js" / "mobile" / "mobile.js",
    ]
    for path in required_files:
        assert path.is_file(), f"缺少手机端资源：{path}"

    for spec_name in ("简单ERP.spec", "简单ERP-demo.spec"):
        tree = ast.parse((ROOT / spec_name).read_text(encoding="utf-8"))
        analysis = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis"
        )
        datas = next(keyword.value for keyword in analysis.keywords if keyword.arg == "datas")
        sources = set()
        for item in datas.elts:
            expression = ast.Expression(item.elts[0])
            sources.add(Path(eval(compile(expression, "<build-input>", "eval"), {"project_root": ROOT})))
        # 整目录打包：模板与静态资源目录都必须在 datas 里
        assert ROOT / "app" / "erp" / "templates" in sources, spec_name
        assert ROOT / "app" / "erp" / "static" in sources, spec_name

        hiddenimports = next(
            keyword.value for keyword in analysis.keywords if keyword.arg == "hiddenimports"
        )
        names = {elt.value for elt in hiddenimports.elts}
        assert "qrcode" in names, f"{spec_name} 的 hiddenimports 缺少 qrcode（设置页二维码会 500）"
