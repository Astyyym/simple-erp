# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import re
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

project_root = Path.cwd()
release_version = (project_root / 'VERSION').read_text(encoding='utf-8').strip()
version_match = re.fullmatch(r'[vV]?(\d+)\.(\d+)\.(\d+)', release_version)
if version_match is None:
    raise ValueError('VERSION must contain a three-part release version')
version_numbers = tuple(int(part) for part in version_match.groups()) + (0,)
windows_version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_numbers, prodvers=version_numbers,
                     mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('080404b0', [
            StringStruct('FileDescription', '简单ERP Windows 桌面版'),
            StringStruct('FileVersion', '.'.join(str(part) for part in version_numbers)),
            StringStruct('InternalName', '简单ERP'),
            StringStruct('OriginalFilename', '简单ERP.exe'),
            StringStruct('ProductName', '简单ERP'),
            StringStruct('ProductVersion', release_version),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0804, 1200])]),
    ],
)
gtk_bin = Path(r'C:\Program Files\GTK3-Runtime Win64\bin')
gtk_binaries = []
if gtk_bin.exists():
    gtk_binaries = [(str(path), '.') for path in gtk_bin.glob('*.dll')]

# 品牌图标：与侧栏 .brand-mark 同一个 Keyline file-spreadsheet 图形（MIT）。
app_icon = project_root / 'packaging' / '简单ERP.ico'

block_cipher = None


a = Analysis(
    ['desktop_app.py'],
    pathex=[str(project_root), str(project_root / 'app')],
    binaries=gtk_binaries,
    datas=[
        (str(project_root / 'app' / 'erp' / 'templates'), 'erp/templates'),
        (str(project_root / 'app' / 'erp' / 'static'), 'erp/static'),
        (str(project_root / 'packaging' / 'default_config.json'), '.'),
        (str(project_root / 'VERSION'), '.'),
    ],
    hiddenimports=[
        'waitress',
        'webview',
        'webview.platforms.edgechromium',
        'weasyprint',
        'pydyf',
        'pypinyin',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# Rename only the public template in the bundle; never read workstation config.
a.datas = [
    ('config.json' if name == 'default_config.json' else name, source, kind)
    for name, source, kind in a.datas
]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='简单ERP',
    version=windows_version,
    icon=str(app_icon),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='简单ERP',
)
