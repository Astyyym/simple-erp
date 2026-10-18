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

APP_URL_DEFAULT_PORT = 5000
_window = None


def _valid_port(value: Any) -> int | None:
    """把候选值收敛成合法端口；非法返回 None。

    不抛异常：端口解析失败不该让桌面壳起不来，回退默认值即可。
    """
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def resolve_port() -> int:
    """桌面壳监听端口：`ERP_PORT` 环境变量 > `config.json` 的 `port` > 5000。

    原先端口在 `APP_URL` / `_run_server` / `_port_is_open` 三处**写死 5000**，
    既不读 `config.json` 也不读 `ERP_PORT`——于是「换个端口跑隔离实例」根本做不到，
    只能让 5000 空着等它。现在与 `app/app.py` 同一口径（env 覆盖 config），
    隔离冒烟/预览可用 `ERP_PORT` 避开端口占用。

    **host 仍固定 `127.0.0.1`，不从配置读**：产品永久免登录，
    绝不能因为 `config.json` 里写了 `0.0.0.0` 就把服务暴露到局域网/公网。
    """
    env_port = _valid_port(os.environ.get("ERP_PORT"))
    if env_port is not None:
        return env_port
    try:
        from erp.config import load_config

        return _valid_port(load_config().get("port")) or APP_URL_DEFAULT_PORT
    except Exception:  # noqa: BLE001 — 配置读不到不该阻止启动，回退默认端口
        return APP_URL_DEFAULT_PORT


def _app_url(port: int | None = None) -> str:
    """桌面壳要加载的地址；host 恒为回环地址（见 `resolve_port` 的安全约束）。"""
    return f"http://127.0.0.1:{port if port is not None else resolve_port()}"

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


def _port_is_open(host: str = "127.0.0.1", port: int | None = None) -> bool:
    """端口是否已被监听；`port=None` 时按 `resolve_port()` 解析。

    保持**零参可调用**：既有启动测试用零参 lambda 替换本函数，签名不能要求实参。
    """
    if port is None:
        port = resolve_port()
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
        # host 固定回环：产品永久免登录，不得从配置读成 0.0.0.0。
        serve(app, host="127.0.0.1", port=resolve_port(), threads=8)
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


def _verify_database_or_raise() -> None:
    """启动前确认业务库能打开。

    `main()` 在「端口已被占用」分支里不会调用 `_create_app()`，于是这个校验
    是那条路径上**唯一**能发现数据库损坏的地方。失败时抛 RuntimeError（带原始原因），
    文案与 `_create_app()` 失败时保持同一口径，用户看到的仍是可读的故障说明。
    """
    try:
        from erp import create_app as _create_erp_app

        _create_erp_app()
    except Exception as exc:
        logging.getLogger(__name__).exception("本地服务启动失败")
        raise RuntimeError(f"简单ERP启动失败：{exc}") from exc


def _window_title() -> str:
    try:
        from erp.config import load_config

        name = str(load_config().get("shop_name") or "").strip()
    except Exception:
        name = ""
    return name or "简单ERP"


def main() -> None:
    global _window
    # 端口只解析一次并贯穿本次启动（探测/绑定/加载地址必须同一个值，
    # 否则探测的是一个端口、绑的是另一个，会误判「已在运行」）。
    port = resolve_port()
    os.environ.setdefault("ERP_PORT", str(port))
    app_url = _app_url(port)
    already_running = _port_is_open(port=port)
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
        # Batch D：把 WeasyPrint 的首次导入成本（约 0.75s）挪到开机后台，
        # 首次「保存并打印」不再为导入等待。
        try:
            from erp.utils.pdf import prewarm_pdf_engine

            prewarm_pdf_engine()
        except Exception:  # noqa: BLE001 — 预热失败不能影响启动
            pass
        # 手机只读端（第二监听 5001）：只有开关打开才起。起不来不能挡住桌面端启动——
        # 手机连不上是「少一个功能」，程序起不来是「什么都干不了」。
        try:
            from erp.config import load_config
            from erp.mobile_server import start_mobile_listener

            if load_config().get("mobile_access_enabled"):
                started, listen_error = start_mobile_listener()
                if not started:
                    logging.getLogger(__name__).warning("手机访问监听未启动：%s", listen_error)
        except Exception:  # noqa: BLE001 — 绝不因手机端影响主程序
            logging.getLogger(__name__).exception("手机访问监听启动失败")

    title = _window_title() if app is not None else "简单ERP"
    try:
        import webview
    except Exception:
        # 没有桌面壳（缺 pywebview / WebView2 不可用）：回退到系统浏览器。
        #
        # 2026-10-17 修复（两条都是真缺陷，不是测试凑数）：
        # ① 原来无条件 `webbrowser.open` + `while True: sleep(3600)`。当 `app is None`
        #    时根本没有任何服务在跑，开出来的只是一个「无法访问」的空页，然后进程
        #    永远挂着 —— 测试里表现为卡满超时（faulthandler 定位到这一行）。
        # ② 更要紧的是：`already_running=True` 时 app 会保持 None。端口被别的程序
        #    占着（甚至被一个残留的旧实例占着）也会被当成「已经启动」，
        #    于是既不去校验数据库、也不报错，直接把用户扔进一个不相干的页面。
        # 现在：**先确认数据库可用**（这是启动必须做的事，与端口无关），
        # 再决定是打开浏览器等待，还是把真实故障抛回去。
        _verify_database_or_raise()
        webbrowser.open(app_url)
        while True:
            time.sleep(3600)
        webbrowser.open(app_url)
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
            _window.load_url(app_url)
        except Exception as exc:  # never leave a permanent splash on failure
            logging.getLogger(__name__).exception("切换到主界面失败")
            _window.load_html(_error_html(str(exc)))

    _window.events.loaded += _on_loaded
    threading.Thread(target=_swap_to_app, daemon=True).start()
    try:
        webview.start()
    finally:
        # 退出时关掉第二监听并等线程结束；否则下次启动端口仍被占（会报「被占用」）。
        try:
            from erp.mobile_server import stop_mobile_listener

            stop_mobile_listener()
        except Exception:  # noqa: BLE001 — 退出清理失败不该改变退出码
            pass


if __name__ == "__main__":
    main()
