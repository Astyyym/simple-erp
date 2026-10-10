from __future__ import annotations

import os
import logging
import queue
import socket
import threading
import time
import webbrowser
import base64
from contextlib import closing
from typing import Any

from waitress import serve

GTK_BIN = r"C:\Program Files\GTK3-Runtime Win64\bin".replace("\\\\", "\\")
if os.path.isdir(GTK_BIN):
    os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", GTK_BIN)
    os.environ["PATH"] = GTK_BIN + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(GTK_BIN)

# Mark desktop shell so UI can enable native folder picker affordances.
os.environ.setdefault("ERP_DESKTOP", "1")

APP_URL = "http://127.0.0.1:5000"
_window = None

# Window background while WebView2 cold-starts, so the frame is never a stark
# white flash before the splash paints. Matches the splash light background.
SPLASH_BACKGROUND = "#f3f5f8"

# Minimum time the branded splash stays visible after it paints. Without a floor
# the swap fires as soon as the server is ready (<1s) and the splash only flashes
# for a fraction of a second — which reads to users as "nothing happened".
SPLASH_MIN_SECONDS = 0.9

# Inline splash shown while the local service finishes booting. Same brand mark
# and theme tokens as the app shell (base.html) so the first paint matches the
# product; colors follow the OS theme via prefers-color-scheme, so it needs no
# config read (which would slow first paint).
SPLASH_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>简单ERP</title>
<style>
:root{--sp-primary:#2563eb;--sp-bg:#f3f5f8;--sp-ink:#172033;--sp-muted:#667085;--sp-track:#e3e5e8}
@media (prefers-color-scheme: dark){:root{--sp-primary:#3b82f6;--sp-bg:#0f141d;
  --sp-ink:#e8eef9;--sp-muted:#9aa8c0;--sp-track:#2b3648}}
html,body{height:100%;margin:0}
body{background:var(--sp-bg);color:var(--sp-ink);
  font-family:"Microsoft YaHei",system-ui,-apple-system,"Segoe UI",sans-serif}
.box{height:100%;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:16px}
.mark{width:56px;height:56px;border-radius:12px;background:var(--sp-primary);color:#fff;
  display:grid;place-items:center;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}
.mark svg{width:32px;height:32px;display:block}
.title{font-size:19px;font-weight:600;letter-spacing:1px}
.bar{width:200px;height:4px;border-radius:3px;background:var(--sp-track);overflow:hidden}
.bar i{display:block;height:100%;width:40%;border-radius:3px;background:var(--sp-primary);
  animation:run 1.1s infinite ease-in-out}
.hint{font-size:13px;color:var(--sp-muted)}
@keyframes run{0%{transform:translateX(-100%)}100%{transform:translateX(350%)}}
</style></head><body><div class="box">
<div class="mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
 stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false"><path
 d="M14 2H8C5.79086 2 4 3.79086 4 6V18C4 20.2091 5.79086 22 8 22H16C18.2091 22 20 20.2091 20 18V8L14 2ZM14 2V5C14 6.65685 15.3431 8 17 8H20M8 12H10M14 12H16M8 16H10M14 16H16"/></svg></div>
<div class="title">简单ERP</div>
<div class="bar"><i></i></div>
<div class="hint">正在启动本地服务…</div>
</div></body></html>"""


def _error_html(message: str) -> str:
    """A dead-end error page so a failed boot never leaves a permanent splash."""
    safe = (message or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>简单ERP</title></head><body style=\"margin:0;height:100%;"
        "font-family:'Microsoft YaHei',system-ui,sans-serif;background:#f3f5f8;color:#172033\">"
        "<div style=\"height:100%;display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;gap:12px;padding:24px;text-align:center\">"
        "<div style=\"font-size:18px;font-weight:600\">启动失败</div>"
        f"<div style=\"font-size:13px;color:#667085;max-width:640px;word-break:break-all\">{safe}</div>"
        "<div style=\"font-size:13px;color:#667085\">请关闭后重新打开，或联系维护人员。</div>"
        "</div></body></html>"
    )


class DesktopApi:
    """JS bridge for the PyWebView desktop shell."""

    def choose_folder(self) -> str:
        """Open a native folder dialog; return selected path or empty string."""
        global _window
        if _window is None:
            return ""
        try:
            import webview

            result = _window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:
            # Fallback: some hosts accept a bare dialog constant / string.
            try:
                result = _window.create_file_dialog("FOLDER_DIALOG")
            except Exception:
                return ""
        if not result:
            return ""
        # pywebview may return a tuple/list of paths
        path = result[0] if isinstance(result, (list, tuple)) else result
        return str(path or "")

    def set_window_title(self, title: str = "") -> bool:
        global _window
        if _window is None:
            return False
        text = (title or "").strip() or "简单ERP"
        try:
            _window.set_title(text)
            return True
        except Exception:
            return False

    def is_desktop(self) -> bool:
        return True

    def save_pdf(self, filename: str, data_url: str) -> dict[str, str | bool]:
        """Save a PDF fetched by the authenticated webview session."""
        global _window
        if _window is None or not data_url.startswith("data:application/pdf;base64,"):
            return {"ok": False, "message": "PDF 数据无效"}
        safe_name = os.path.basename(filename or "打印单.pdf")
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        try:
            import webview
            target = _window.create_file_dialog(webview.SAVE_DIALOG, save_filename=safe_name, file_types=("PDF Files (*.pdf)",))
            if not target:
                return {"ok": False, "cancelled": True, "message": ""}
            path = target[0] if isinstance(target, (list, tuple)) else target
            with open(str(path), "wb") as output:
                output.write(base64.b64decode(data_url.split(",", 1)[1], validate=True))
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "message": f"保存失败：{exc}"}


def _port_is_open(host: str = "127.0.0.1", port: int = 5000) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _create_app():
    """Import and build the Flask app.

    Imported here (not at module level) so merely importing this shell does not
    pay the app's import cost; the caller decides when to build the app.
    """
    from erp import create_app

    return create_app()


def _run_server(
    startup_errors: queue.Queue[Exception] | None = None,
    *,
    app: Any = None,
) -> None:
    try:
        app = app if app is not None else _create_app()
        serve(app, host="127.0.0.1", port=5000, threads=8)
    except Exception as exc:
        logging.getLogger(__name__).exception("本地服务启动失败")
        if startup_errors is None:
            raise
        startup_errors.put(exc)


def _wait_for_server(
    timeout_seconds: int = 20,
    startup_errors: queue.Queue[Exception] | None = None,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if startup_errors is not None:
            try:
                error = startup_errors.get_nowait()
            except queue.Empty:
                pass
            else:
                raise RuntimeError(f"简单ERP启动失败：{error}") from error
        if _port_is_open():
            return
        time.sleep(0.2)
    raise RuntimeError("简单ERP启动超时，请重新打开或联系维护人员。")


def _window_title() -> str:
    try:
        from erp.config import load_config

        name = str(load_config().get("shop_name") or "").strip()
    except Exception:
        name = ""
    return name or "简单ERP"


def main() -> None:
    global _window
    os.environ.setdefault("ERP_PORT", "5000")
    already_running = _port_is_open()
    startup_errors: queue.Queue[Exception] = queue.Queue()
    app = None
    if not already_running:
        # Build the app BEFORE anything is shown: a real database/config failure
        # must surface immediately as a raised error (the startup regression
        # tests pin this), never be hidden behind a window that then blocks.
        try:
            app = _create_app()
        except Exception as exc:
            logging.getLogger(__name__).exception("本地服务启动失败")
            raise RuntimeError(f"简单ERP启动失败：{exc}") from exc
        threading.Thread(
            target=_run_server, args=(startup_errors,), kwargs={"app": app}, daemon=True
        ).start()

    title = _window_title() if app is not None else "简单ERP"
    try:
        import webview
    except Exception:
        webbrowser.open(APP_URL)
        while True:
            time.sleep(3600)

    api = DesktopApi()
    # Splash first: the window paints the brand immediately, so it is visible
    # while the remaining boot (serve bind + first page) completes in parallel.
    _window = webview.create_window(
        title,
        html=SPLASH_HTML,
        width=1280,
        height=820,
        min_size=(1100, 700),
        js_api=api,
        background_color=SPLASH_BACKGROUND,
    )

    # The swap must wait for the splash to actually paint. Without this gate the
    # server (ready in <1s after the lazy-import work) won the race, load_url ran
    # before the splash HTML ever rendered, and the user saw only a blank window.
    splash_painted = threading.Event()
    painted_at: dict[str, float] = {}

    def _on_loaded() -> None:
        if not splash_painted.is_set():
            painted_at["t"] = time.perf_counter()
            splash_painted.set()

    def _swap_to_app() -> None:
        try:
            if not already_running:
                _wait_for_server(startup_errors=startup_errors)
            # Never swap into a still-blank WebView2: wait for the splash paint,
            # but do not hang forever if this WebView2 build omits the event.
            splash_painted.wait(timeout=8.0)
            # Keep the branded splash visible long enough to read as a loading
            # state rather than an imperceptible flash.
            started = painted_at.get("t")
            if started is not None:
                remaining = SPLASH_MIN_SECONDS - (time.perf_counter() - started)
                if remaining > 0:
                    time.sleep(remaining)
            _window.load_url(APP_URL)
        except Exception as exc:  # never leave a permanent splash on failure
            logging.getLogger(__name__).exception("切换到主界面失败")
            _window.load_html(_error_html(str(exc)))

    _window.events.loaded += _on_loaded
    threading.Thread(target=_swap_to_app, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
