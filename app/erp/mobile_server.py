"""第二监听（手机端）的启动 / 停止：端口固定 5001，不漂移。

为什么是第二个监听、而不是把主应用改成 `0.0.0.0`（决策文档 §2.2，已实测）：

- `desktop_app.py:217` 是 `serve(app, host="127.0.0.1", ...)`，waitress 的 host
  是**启动时绑定的 socket，运行中改不了**；改 host 只能重启 → 桌面窗口闪一下 →
  用户会以为程序崩了。
- 改用第二个监听后：主应用物理上不可能被局域网访问；`tests/test_config_fallback.py`
  的 `host == "127.0.0.1"` 断言不用改；开关可热切换；回滚就是关开关。

端口规则（§2.3）：5001 被占时**开关自动回弹到关 + 明确报错**，绝不自动退到 5002 ——
端口是配对 base_url 的一部分，自动漂移会让已配对手机静默失联。
"""
from __future__ import annotations

import socket
import threading

LISTEN_PORT = 5001
LISTEN_HOST = "0.0.0.0"

_state: dict = {"server": None, "thread": None, "error": "", "port": LISTEN_PORT}
_lock = threading.Lock()


# 代理/虚拟网卡会抢默认路由：本机实测 `198.18.0.1`（Meta / TUN）会被
# 「连 8.8.8.8 看默认路由」这种取地址法选中，而手机永远连不上它。
# 198.18.0.0/15 是 RFC 2544 基准测试保留段，Clash/Meta 这类 TUN 客户端常用它做假地址。
BENCHMARK_PREFIXES = ("198.18.", "198.19.")
LINK_LOCAL_PREFIX = "169.254."


def _is_lan_ip(address: str) -> bool:
    """是不是一个手机**可能连到**的局域网地址（RFC1918 内网段）。"""
    text = str(address or "").strip()
    if not text:
        return False
    if text.startswith(("127.", "0.")):
        return False
    if text.startswith(LINK_LOCAL_PREFIX) or text.startswith(BENCHMARK_PREFIXES):
        return False
    if text.startswith(("192.168.", "10.")):
        return True
    if text.startswith("172."):
        try:
            second = int(text.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def _lan_rank(address: str) -> tuple[int, str]:
    """同类里优先 192.168（家用路由器最常见），其次 10.，再次 172.16–31。"""
    if address.startswith("192.168."):
        return (0, address)
    if address.startswith("10."):
        return (1, address)
    return (2, address)


def _pick_lan_address(candidates) -> str:
    """从候选地址里挑最像「手机能连」的那个；一个都没有就返回 127.0.0.1。"""
    usable = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if _is_lan_ip(text) and text not in usable:
            usable.append(text)
    if not usable:
        return "127.0.0.1"
    return sorted(usable, key=_lan_rank)[0]


def _lan_candidates() -> list[str]:
    """候选来源两路：默认路由选中的网卡 + 主机名解析出的全部 IPv4。

    只用第一路会被 TUN 代理骗（实测本机返回 198.18.0.1），所以两路合并后再过滤。
    """
    found: list[str] = []
    for target in ("8.8.8.8", "223.5.5.5", "192.168.10.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.settimeout(0.2)
                probe.connect((target, 80))
                found.append(probe.getsockname()[0])
        except OSError:
            continue
    try:
        found.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return found


def lan_ip() -> str:
    """本机在局域网里的地址（供二维码与设置页显示）。"""
    return _pick_lan_address(_lan_candidates())


def lan_ip_warning() -> str:
    """地址不可用时给一句店主能看懂的话；可用时返回空串。"""
    if _is_lan_ip(lan_ip()):
        return ""
    return (
        "没找到局域网地址（可能只连了代理/虚拟网卡，或 Wi-Fi 没连上）。"
        "手机连不上的话，先检查这台电脑的 Wi-Fi。"
    )


def port_in_use(port: int = LISTEN_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def mobile_url(port: int | None = None, ip: str | None = None) -> str:
    """手机要打开的地址。`ip` 传进来可避免重复探测网卡。"""
    address = ip or lan_ip()
    return f"http://{address}:{int(port or _state.get('port') or LISTEN_PORT)}/m/"


def is_listening() -> bool:
    return _state.get("server") is not None


def listener_error() -> str:
    return str(_state.get("error") or "")


def listener_port() -> int:
    return int(_state.get("port") or LISTEN_PORT)


def start_mobile_listener(port: int = LISTEN_PORT) -> tuple[bool, str]:
    """启动第二监听。返回 `(ok, error_message)`；失败时 error 是给店主看的中文原因。"""
    global _state
    with _lock:
        if _state.get("server") is not None:
            return True, ""
        try:
            from waitress import create_server
        except ImportError as exc:  # pragma: no cover
            _state["error"] = f"缺少 waitress 依赖：{exc}"
            return False, _state["error"]

        # 先自己探一次端口：Windows 上 `SO_REUSEADDR` 允许**重复绑定**已在监听的端口
        # （与 POSIX 不同），等 waitress 报错是等不到的——它会「绑定成功」然后行为诡异。
        # 所以占用判定用 connect 探测，不靠 bind 异常。
        if port_in_use(int(port)):
            message = (
                f"端口 {port} 被占用，可能是上次没退干净。"
                "请重启程序后重试；仍失败就说明有别的程序在用这个端口。"
            )
            _state["error"] = message
            return False, message

        from erp.mobile_app import create_mobile_app

        app = create_mobile_app()
        try:
            server = create_server(app, host=LISTEN_HOST, port=int(port), threads=4)
        except OSError as exc:
            message = (
                f"端口 {port} 被占用，可能是上次没退干净（{exc}）。"
                "请重启程序后重试；仍失败就换个端口并重新配对手机。"
            )
            _state["error"] = message
            return False, message

        thread = threading.Thread(
            target=_serve, args=(server,), name="erp-mobile-listener", daemon=True
        )
        _state.update({"server": server, "thread": thread, "error": "", "port": int(port)})
        thread.start()
        return True, ""


def _serve(server) -> None:
    try:
        server.run()
    except Exception as exc:  # noqa: BLE001 — 监听线程死了要留下可诊断原因
        _state["error"] = f"手机访问监听已停止：{exc}"
        _state["server"] = None


def stop_mobile_listener() -> None:
    """关闭监听并等线程结束；不这样做下次启动端口仍在占用。"""
    global _state
    with _lock:
        server = _state.get("server")
        thread = _state.get("thread")
        _state["server"] = None
        _state["thread"] = None
    if server is None:
        return
    try:
        server.close()
    except Exception:  # noqa: BLE001 — 关闭失败也要尝试回收线程
        pass
    if isinstance(thread, threading.Thread) and thread.is_alive():
        thread.join(timeout=3)


def status() -> dict:
    """设置页诊断面板用的快照（不含任何业务数据）。"""
    address = lan_ip()
    usable = _is_lan_ip(address)
    return {
        "listening": is_listening(),
        "port": listener_port(),
        "host": LISTEN_HOST,
        "lan_ip": address,
        "lan_usable": usable,
        "lan_warning": "" if usable else lan_ip_warning(),
        "url": mobile_url(ip=address),
        "error": listener_error(),
    }
