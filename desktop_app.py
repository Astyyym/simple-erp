from __future__ import annotations

import os
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

from erp import create_app
from erp.config import load_config


APP_URL = "http://127.0.0.1:5000"
_window = None


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


def _run_server() -> None:
    app = create_app()
    serve(app, host="127.0.0.1", port=5000, threads=8)


def _wait_for_server(timeout_seconds: int = 20) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _port_is_open():
            return
        time.sleep(0.2)
    raise RuntimeError("简单ERP启动超时，请重新打开或联系维护人员。")


def _window_title() -> str:
    try:
        name = str(load_config().get("shop_name") or "").strip()
    except Exception:
        name = ""
    return name or "简单ERP"


def main() -> None:
    global _window
    os.environ.setdefault("ERP_PORT", "5000")
    if not _port_is_open():
        server_thread = threading.Thread(target=_run_server, daemon=True)
        server_thread.start()
        _wait_for_server()

    title = _window_title()
    try:
        import webview
    except Exception:
        webbrowser.open(APP_URL)
        while True:
            time.sleep(3600)

    api = DesktopApi()
    _window = webview.create_window(
        title,
        APP_URL,
        width=1280,
        height=820,
        min_size=(1100, 700),
        js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
