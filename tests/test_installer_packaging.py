"""安装包（Inno Setup）构建契约：per-user 安装、版本单一真源、不碰用户数据。

这些断言守护的是**会伤到用户**的设计点，不是格式洁癖：
- 装到 Program Files 会让 config.json 写不进去（程序在 EXE 旁写配置）；
- 升级覆盖 config.json 会丢店铺设置；
- 卸载删数据目录会丢账。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_DIR = ROOT / "packaging" / "installer"
ISS = INSTALLER_DIR / "简单ERP.iss"


@pytest.fixture(scope="module")
def iss_text() -> str:
    assert ISS.exists(), f"缺少安装脚本 {ISS}"
    return ISS.read_text(encoding="utf-8")


def test_installer_is_per_user_and_never_requests_elevation(iss_text):
    assert r"DefaultDirName={localappdata}\Programs\简单ERP" in iss_text
    assert "PrivilegesRequired=lowest" in iss_text
    # 空值 = 禁止用户切换到提权模式（提权会落到受保护的系统目录，配置就写不动了）
    assert re.search(r"^PrivilegesRequiredOverridesAllowed=\s*$", iss_text, re.M)
    assert "{pf}" not in iss_text.lower()
    assert "{commonpf" not in iss_text.lower()


def test_installer_version_has_a_single_source_of_truth(iss_text):
    # 版本必须从仓库根 VERSION 读，脚本里不得出现写死的 vX.Y.Z
    assert '"..\\..\\VERSION"' in iss_text
    assert "AppVersion Trim(FileRead(FileOpen(" in iss_text
    declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", declared), declared
    assert declared not in iss_text, "安装脚本里出现了写死版本号，应只从 VERSION 读"
    # PE 版本字段要纯数字：v3.6.0 -> 3.6.0.0
    assert 'NumericVersion Copy(AppVersion, 2) + ".0"' in iss_text


def test_upgrade_preserves_user_config_and_uninstall_keeps_it(iss_text):
    assert 'Excludes: "config.json"' in iss_text
    config_line = next(
        line for line in iss_text.splitlines()
        if line.startswith("Source:") and line.rstrip().endswith("uninsneveruninstall")
    )
    assert "config.json" in config_line
    assert "onlyifdoesntexist" in config_line


def test_installer_never_touches_the_business_data_root(iss_text):
    uninstall = iss_text.split("[UninstallDelete]", 1)[1].split("[Code]", 1)[0]
    assert "{app}" in uninstall
    for forbidden in ("Documents", "简单ERP数据", "{userdocs}", "erp.db", "{localappdata}\\"):
        assert forbidden not in uninstall, f"[UninstallDelete] 不得涉及 {forbidden}"
    # 文件清单来源只能是已构建的程序目录，不得指向业务数据或工作机配置
    files_section = iss_text.split("[Files]", 1)[1].split("[Icons]", 1)[0]
    assert files_section.count("Source:") == 3
    assert "{#SourceDir}" in files_section
    assert (ROOT / "config.json").as_posix() not in files_section
    for forbidden in (".db", "demo_data", "backups", "imports", "logs"):
        assert forbidden not in files_section
    # Excludes 写裸 config.json 会按文件名匹配全树，连 _internal\config.json 一起排掉；
    # 必须显式补回内置回退配置，否则安装版少了 bundled_root() 的回退。
    assert 'Source: "{#SourceDir}\\_internal\\config.json"' in files_section


def test_installer_ships_simplified_chinese_messages(iss_text):
    # 中文语言文件随仓库提供（Inno 发行包不含），用 SourcePath 引用脚本同目录的那份
    assert 'MessagesFile: "{#SourcePath}\\ChineseSimplified.isl"' in iss_text
    language_file = INSTALLER_DIR / "ChineseSimplified.isl"
    assert language_file.exists(), "Inno Setup 发行包不含中文语言文件，必须随仓库提供"
    assert "[LangOptions]" in language_file.read_text(encoding="utf-8")
    assert (INSTALLER_DIR / "安装说明.txt").exists()
    assert "InfoBeforeFile=" in iss_text


def test_installer_warns_instead_of_silently_installing_webview2(iss_text):
    # 缺 WebView2 时只提示 + 给官方地址，不静默联网下载
    assert "WebView2" in iss_text
    assert "developer.microsoft.com/microsoft-edge/webview2" in iss_text
    assert "InitializeSetup" in iss_text  # 安装前检测程序是否在运行


def test_installer_build_script_reuses_the_one_folder_output():
    script = (ROOT / "打包Windows安装包.bat").read_text(encoding="utf-8")
    assert "简单ERP.spec" in script, "安装包必须建立在既有 one-folder 构建之上"
    assert "ISCC.exe" in script
    assert "packaging\\default_config.json" in script
    assert "copy config.json" not in script
    assert "packaging\\installer\\简单ERP.iss" in script
    assert "/DAppVersion=" in script


def test_build_script_always_produces_all_three_artifacts():
    """交付标准是三种形态齐备；脚本少做一个，下次发版就会缺资产。"""
    script = (ROOT / "打包Windows安装包.bat").read_text(encoding="utf-8")
    assert "[1/3]" in script and "[2/3]" in script and "[3/3]" in script
    assert "simple-erp-setup-%APPVER%.exe" in script
    assert "simple-erp-windows-%APPVER%.zip" in script
    assert "make_release_zip.py" in script
    # 必须用 python -m PyInstaller：直接调 pyinstaller.exe 在 MSYS 下无输出
    assert "python.exe -m PyInstaller" in script


def test_release_zip_helper_rejects_business_data(tmp_path):
    """ZIP 打包脚本要在写盘前就拒绝业务数据，而不是靠事后人工检查。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_release_zip", ROOT / "packaging" / "make_release_zip.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = tmp_path / "简单ERP"
    (source / "_internal").mkdir(parents=True)
    (source / "简单ERP.exe").write_bytes(b"MZ")
    (source / "_internal" / "VERSION").write_text("v3.6.0\n", encoding="utf-8")
    (source / "config.json").write_text("{}", encoding="utf-8")

    info = module.build_zip(source, tmp_path / "out.zip")
    assert info["top_levels"] == ["简单ERP"]
    assert info["entries"] == 3
    assert info["has_root_config"] and info["has_version"]

    # 混入业务库 / 日志 / 演示数据 -> 必须直接拒绝
    for bad in ("data/erp.db", "logs/app.log", "demo_data/seed.json"):
        victim = source / bad
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_bytes(b"x")
        with pytest.raises(SystemExit, match="禁止的条目"):
            module.build_zip(source, tmp_path / "out.zip")
        victim.unlink()


def test_release_zip_helper_requires_version_format():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_release_zip", ROOT / "packaging" / "make_release_zip.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit, match="版本格式不合法"):
        module._read_version(ROOT, "3.6")
    assert module._read_version(ROOT, "v9.9.9") == "v9.9.9"
