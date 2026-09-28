# Windows-native EXE packaging for local Flask/SQLite ERP

Use this reference when turning the local ERP into a parent/shop-friendly Windows desktop app.

## Target shape

- Final user action: double-click `简单ERP.exe` from `dist\简单ERP\`.
- Spec/BAT: `简单ERP.spec`, `打包Windows桌面版.bat` (older `消防ERP` names are historical).
- No WSL/terminal/`pip` for daily shop use.
- Prefer PyInstaller **one-folder**.
- Preferred live data: Documents/settings data root; never overwrite live `erp.db` on upgrade.

## Implementation pattern

1. Add a desktop launcher, e.g. `desktop_app.py`:
   - start Waitress on `127.0.0.1:5000` in a background thread;
   - wait until the port/`/health` is reachable;
   - open PyWebView/WebView2 window, with browser fallback if PyWebView is unavailable.
2. Adjust runtime paths:
   - normal source run: project root is source folder;
   - frozen run: writable runtime root is `Path(sys.executable).parent`;
   - bundled read-only assets may be under `sys._MEIPASS`.
3. Build using Windows Python in `.venv-win`, never WSL `.venv`.
4. PyInstaller spec should include templates, config, and needed hidden imports (`waitress`, `webview`, WeasyPrint stack).

## WeasyPrint / GTK pitfall

On Windows, WeasyPrint needs GTK/Pango DLLs. Symptom: EXE process appears in Task Manager but ERP server never listens on port 5000; import error mentions `gobject-2.0-0`, Pango, Cairo, or Fontconfig.

Fix pattern:

```python
GTK_BIN = r"C:\Program Files\GTK3-Runtime Win64\bin"
if os.path.isdir(GTK_BIN):
    os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", GTK_BIN)
    os.environ["PATH"] = GTK_BIN + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(GTK_BIN)
```

This must run **before** importing app modules that import `weasyprint`.

For portable builds, also bundle/copy GTK DLLs into the PyInstaller one-folder distribution.

## Dist lock: old EXE blocks PyInstaller COLLECT

Symptom: `PermissionError` on `dist\简单ERP\_internal\...` while COLLECT runs.

Root cause: previous `简单ERP.exe` still running from that dist folder (often port 5000).

Before rebuild:

1. Free ports **5000 and 5001** (never assign PowerShell `$pid`).
2. Stop processes under `dist\简单ERP` / smoke temp dirs.
3. Confirm `http://127.0.0.1:5000/health` fails.
4. Rebuild: `.venv-win\Scripts\python.exe -m PyInstaller 简单ERP.spec --clean --noconfirm` (avoid BAT `pause` for agents).

Smoke from **ASCII temp copy**, not live `dist/`.

## Verification checklist

- Free port 5000; no process from `dist/简单ERP`.
- Smoke ASCII temp + isolated `ERP_DATA_ROOT`.
- `/health` → `status=ok`, **new** `version`, v0.8.0+ `auth: disabled`.
- `/` and `/orders/new` → 200, no login UI / no「退出登录」.
- ZIP via Python; Release asset `simple-erp-windows-vX.Y.Z.zip`.
- Never git-commit `dist/`.
