from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser
from contextlib import closing

from waitress import serve

GTK_BIN = r"C:\Program Files\GTK3-Runtime Win64\bin".replace("\\\\", "\\")
if os.path.isdir(GTK_BIN):
    os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", GTK_BIN)
    os.environ["PATH"] = GTK_BIN + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(GTK_BIN)

from erp import create_app


APP_URL = "http://127.0.0.1:5000"


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
    raise RuntimeError("消防ERP启动超时，请重新打开或联系维护人员。")


def main() -> None:
    os.environ.setdefault("ERP_PORT", "5000")
    if not _port_is_open():
        server_thread = threading.Thread(target=_run_server, daemon=True)
        server_thread.start()
        _wait_for_server()

    try:
        import webview
    except Exception:
        webbrowser.open(APP_URL)
        while True:
            time.sleep(3600)

    window = webview.create_window("消防ERP", APP_URL, width=1280, height=820, min_size=(1100, 700))
    webview.start()


if __name__ == "__main__":
    main()
