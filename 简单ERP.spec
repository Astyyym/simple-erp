# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

project_root = Path.cwd()
gtk_bin = Path(r'C:\Program Files\GTK3-Runtime Win64\bin')
gtk_binaries = []
if gtk_bin.exists():
    gtk_binaries = [(str(path), '.') for path in gtk_bin.glob('*.dll')]

block_cipher = None


a = Analysis(
    ['desktop_app.py'],
    pathex=[str(project_root), str(project_root / 'app')],
    binaries=gtk_binaries,
    datas=[
        (str(project_root / 'app' / 'erp' / 'templates'), 'erp/templates'),
        (str(project_root / 'config.json'), '.'),
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
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='简单ERP',
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
