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
4. Include templates, static modules, VERSION, a **public default config template**, and needed hidden imports (`waitress`, `webview`, WeasyPrint stack). Never bundle/post-copy workstation `config.json`: use `packaging/default_config.json` and explicitly rename the TOC entry to `config.json`, because datas destination changes the folder, not the basename. Validate root and bundled config against the public template; never reuse an old dist with unknown private settings/data for a distributable build.
5. Derive Windows ProductVersion/numeric resources from exact VERSION; verify actual PE, /health and UI after freezing.

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

1. Inspect owners of **5000 and 5001** (never assign PowerShell `$pid`); do not stop another user instance merely to build.
2. Stop only registered agent-owned smoke processes. If old dist is live or uncertain, build with unique scratch `--distpath`/`--workpath`, preserve old output, deliver a separate version directory.
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
- Check native WebView2 / real Python bridge separately from headless browser-to-EXE HTTP. Minimized windows can make CDP mouse/screenshot unreliable: preserve that evidence boundary and use a reliable keyboard path rather than claiming OS mouse/native visual acceptance. Save dialog and physical printer remain separate.
- Test clean first install with shipped config and no version override, and close/restart using a synthetic populated root. Read exact business state back, check posting conservation and listening PID; inspect loaded PDF modules to confirm bundled GTK/Pango use. This does not prove a fresh separate computer or fully offline operation.

## Legacy startup and failure diagnostics

- Empty/current-schema smoke does not prove old installations can upgrade. Include the frozen pre-inventory schema in `tests/fixtures/legacy_schema_v2.sql`, seed fictional business rows, and run the actual EXE through upgrade and restart. Never use live business data as a fixture.
- `CREATE TABLE IF NOT EXISTS` does not add missing columns to old tables. Create indexes depending on newly introduced fields only after the corresponding `ALTER TABLE` steps; keep schema changes, dependent indexes and schema-version publication in the same transaction. Exercise rollback and retry when an index cannot be created.
- Capture desktop server-thread exceptions, log the original traceback and pass the error to the startup waiter. A generic port-wait timeout alone hides initialization failures; extending the timeout is not a database migration fix.
- Verify that the exact new EXE owns loopback port 5000 and that `/health` reports the expected version and isolated data root; native window capture and the real Python bridge are separate evidence from HTTP availability.
- A preview parent with `document.readyState=complete` may still have a blank PDF iframe while the native reader loads. Observe the rendered PDF before claiming visual acceptance; preserve early screenshots as timing evidence, not completed-render evidence.

## Synthetic demo packages

- Keep normal first-run/data-root behavior untouched. Put demo-only wrapper/spec in scratch, force executable-local data/config/location overrides before imports, and align a dedicated loopback server port, readiness probe and shell URL. Reject an occupied demo port rather than attaching to an unrelated instance.
- Count business documents explicitly across sales/returns, purchases and purchase returns; customer/product rows, line items, inventory initializations and reconciliation snapshots are supporting data, not extra documents. Seed through real services and retain the category manifest and conservation checks.
- Before archiving a demo DB, close the owned EXE, run WAL checkpoint, explicitly close the SQLite connection, and verify sidecars are absent. `with sqlite3.connect(...)` commits/rolls back but does not close that connection; do not blindly delete a potentially live WAL.
- Strip only owned demo logs/PDF caches/absolute location memory, preserve the seeded database, and verify the exact archive by extraction and real EXE restart at the new path. Packaged path isolation must survive inherited wrong ERP environment values.

## Agent shell invocation: MSYS bash → `cmd` / Windows tools (2026-10-13)

Windows-only pitfalls that make a build *look* like it succeeded while nothing ran:

- **`cmd //c "x.bat"` is not `cmd /c`.** Under this host's MSYS bash the doubled slash is not collapsed, so `cmd` starts an **interactive** shell, immediately hits EOF, prints the banner + prompt, and exits `0`. It looks successful and does nothing. Use `cmd /c ...`.
- **Chinese BAT filenames don't survive bash → cmd.** bash hands `cmd` UTF-8 bytes while `cmd` resolves against the ANSI codepage, so `打包Windows安装包.bat` is "not found". Launch it from native Windows Python instead, which passes a wide-char command line:
  `subprocess.Popen(["cmd", "/c", "打包Windows安装包.bat"], cwd=ROOT, stdin=subprocess.DEVNULL)` — `DEVNULL` also feeds the BAT's trailing `pause` so the agent never blocks.
- **Windows tool output is GBK, not UTF-8.** `subprocess.run(..., text=True)` on `tasklist` / `netstat` / `powershell` raises `UnicodeDecodeError` (and the traceback can abort a probe *before* its cleanup step, leaving the EXE running). Capture bytes and `decode("gbk", errors="replace")`. If a probe can fail mid-way, put the kill/cleanup in `finally`.
- **Don't detect a login gate with `"password" in body`.** The order-entry pages deliberately set `autocomplete="new-password"` to suppress browser autofill, so that substring is always present. Match on the real login markers (e.g. `退出登录`) or inspect the template.
- **`node --test tests/frontend/` fails on Node 26** (`pass 0 / fail 1`, the directory is treated as one test file). Always pass the glob: `node --test tests/frontend/*.test.mjs`.
