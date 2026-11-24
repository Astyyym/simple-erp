"""第二监听（手机端）的启动 / 停止：端口固定 5001，不漂移。

为什么是第二个监听、而不是把主应用改成 `0.0.0.0`（决策文档 §2.2，已实测）：

- `desktop_app.py:217` 是 `serve(app, host="127.0.0.1", ...)`，waitress 的 host
  是**启动时绑定的 socket，运行中改不了**；改 host 只能重启 → 桌面窗口闪一下 →
  用户会以为程序崩了。
- 改用第二个监听后：主应用物理上不可能被局域网访问；`tests/test_config_fallback.py`
  的 `host == "127.0.0.1"` 断言不用改；开关可热切换；回滚就是关开关。

端口规则（§2.3）：5001 被占时**只报错，绝不自动退到 5002** ——
端口是配对 base_url 的一部分，自动漂移会让已配对手机静默失联。

⚠️ 2026-11 修正：被占时**开关不再回弹到关**。旧行为是「起不来就把
`mobile_access_enabled` 写回 False」，于是用户点开开关后什么都没发生、
下次冷启动也完全不尝试，意图被静默改写。现在开关保持用户意图（True），
失败原因另存 `mobile_last_start_error`（持久化在 config 里），冷启动会再试一次。
"""
from __future__ import annotations

import logging
import socket
import threading

from erp.config import load_config, save_config

logger = logging.getLogger(__name__)

LISTEN_PORT = 5001
LISTEN_HOST = "0.0.0.0"

_state: dict = {"server": None, "thread": None, "error": "", "port": LISTEN_PORT}
_lock = threading.Lock()

# 地址活性自测的超时（秒）。设置页每次渲染都会算地址，所以必须短：
# 本机回环/本地地址的连接/拒绝都是立刻返回的，0.2s 只兜底「网卡半死」的极端情况。
_LIVENESS_TIMEOUT = 0.2


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


def _address_alive(address: str, port: int | None = None) -> bool:
    """这条地址**此刻**在本机是不是真的「存在且在服务」，供 `_pick_lan_address` 排序。

    为什么需要它：`_lan_rank` 只看地址段（192.168 > 10. > 172.16–31）。本机装了
    VirtualBox Host-Only（典型 `192.168.56.1`）或 VMware 网卡时，它和家用 Wi-Fi
    的 `192.168.1.x` 属于**同一档**，最后是按字符串顺序随手挑一个 —— 设置页二维码
    可能给出一个手机根本连不上的地址，而电脑端一切显示正常（真实踩过）。

    两级确认，都是便宜的本地调用：
    ① `bind((addr, 0))`：地址必须真的落在本机某块已启用的网卡上（拔掉的 Wi-Fi、
       历史残留地址会失败）；
    ② `connect_ex((addr, 当前监听端口))`：第二监听此刻正绑在 `0.0.0.0:5001`，
       从这条地址能连上才说明「这块网卡上的端口确实在服务」。

    ⚠️ 它证明的是「本机可达」，**不是**「手机可达」：防火墙规则本机看不见，
    所以诊断文案里那句「不代表手机能连」必须留着，别因为有了这个自测就删。
    """
    text = str(address or "").strip()
    if not text:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(_LIVENESS_TIMEOUT)
            probe.bind((text, 0))
    except OSError:
        return False
    if _state.get("server") is None:
        # 监听没起来时 connect 探测**没有信息量**：所有候选都连不上，只会白等超时
        # （实测每次 200ms，而设置页渲染会调好几次 `status()`）。bind 成功已经
        # 证明「这块网卡在本机、且已启用」，够用于排序了。
        return True
    listen_port = int(port or _state.get("port") or LISTEN_PORT)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(_LIVENESS_TIMEOUT)
            return probe.connect_ex((text, listen_port)) == 0
    except OSError:
        return False


def _pick_lan_address(candidates) -> str:
    """从候选地址里挑最像「手机能连」的那个；一个都没有就返回 127.0.0.1。

    排序键依次是：
      ① 活性（`_address_alive`：网卡在、端口能连）；
      ② `_lan_rank` 的地址段档位（192.168 > 10. > 172.16–31）；
      ③ 候选来源顺序（默认路由探测出来的排在 `gethostbyname_ex` 之前，
         见 `_lan_candidates`）—— 真正的 Wi-Fi 通常就是默认路由那块网卡，
         而 VirtualBox / VMware 的虚拟网卡再「像」192.168 也不会出现在默认路由上。
    ②③ 一起才解决「虚拟网卡与 Wi-Fi 同为 192.168.x、都被判活」的情形；单靠活性
    分辨不出来（监听绑在 0.0.0.0 时，虚拟网卡那条地址同样连得上）。

    ⚠️ 全都不活时**不能**判成「没有局域网地址」：探测本身会失效（监听还没起来、
    防火墙拦了本地连接），那时退回改动前的地址段排序，行为与原来一致。
    """
    usable: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if _is_lan_ip(text) and text not in usable:
            usable.append(text)
    if not usable:
        return "127.0.0.1"
    alive = {address for address in usable if _address_alive(address)}
    pool = [address for address in usable if address in alive] or usable

    def _sort_key(address: str) -> tuple[int, int, int]:
        return (
            0 if address in alive else 1,
            _lan_rank(address)[0],
            usable.index(address),
        )

    return sorted(pool, key=_sort_key)[0]


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


def last_start_error() -> str:
    """上次启动监听失败的原因（持久化在 config 里，**重启后仍然能看到**）。

    与 `listener_error()` 的区别：那个是本进程内监听线程的即时状态，进程退出即消失；
    这个是「用户上次打开开关时为什么没起来」。冷启动会按开关再试一次，仍失败时
    店主重新打开设置页必须还能读到原因 —— 否则他只会看到「已打开，监听未起来」，
    没有任何可执行信息。
    """
    try:
        return str(load_config().get("mobile_last_start_error") or "")
    except Exception:  # noqa: BLE001 — 配置读不出来不能连带拖坏诊断面板
        logger.warning("读取手机访问启动失败原因失败", exc_info=True)
        return ""


def _record_start_failure(message: str) -> str:
    """启动失败：本进程状态 + 配置各记一份，返回给调用方（原样当 error 用）。"""
    _state["error"] = message
    try:
        save_config({"mobile_last_start_error": message})
    except Exception:  # noqa: BLE001 — 记不下原因也绝不能因此起不来
        logger.warning("记录手机访问启动失败原因到配置失败（已忽略）：%s", message, exc_info=True)
    return message


def _clear_start_failure() -> None:
    """启动成功：清掉上一次的失败原因（只在真的写到过时才写盘）。"""
    try:
        if str(load_config().get("mobile_last_start_error") or ""):
            save_config({"mobile_last_start_error": ""})
    except Exception:  # noqa: BLE001 — 清理失败不影响监听本身
        logger.warning("清除手机访问启动失败原因失败（已忽略）", exc_info=True)


def start_mobile_listener(port: int = LISTEN_PORT) -> tuple[bool, str]:
    """启动第二监听。返回 `(ok, error_message)`；失败时 error 是给店主看的中文原因。

    失败**只报告、不改开关**：`mobile_access_enabled` 是用户意图，由调用方保留，
    冷启动（`desktop_app.py` / `app/app.py`）据此再试一次。原因持久化在
    `mobile_last_start_error`，设置页诊断区与关闭提醒都能读到。
    """
    global _state
    with _lock:
        if _state.get("server") is not None:
            return True, ""
        try:
            from waitress import create_server
        except ImportError as exc:  # pragma: no cover
            return False, _record_start_failure(f"缺少 waitress 依赖：{exc}")

        # 先自己探一次端口：Windows 上 `SO_REUSEADDR` 允许**重复绑定**已在监听的端口
        # （与 POSIX 不同），等 waitress 报错是等不到的——它会「绑定成功」然后行为诡异。
        # 所以占用判定用 connect 探测，不靠 bind 异常。
        if port_in_use(int(port)):
            message = (
                f"端口 {port} 被占用，可能是上次没退干净。"
                "请重启程序后重试；仍失败就说明有别的程序在用这个端口。"
            )
            return False, _record_start_failure(message)

        from erp.mobile_app import create_mobile_app

        app = create_mobile_app()
        try:
            server = create_server(app, host=LISTEN_HOST, port=int(port), threads=4)
        except OSError as exc:
            # 端口固定 5001，不自动漂移（端口是配对 base_url 的一部分，漂移是静默失败）。
            message = (
                f"端口 {port} 被占用，可能是上次没退干净（{exc}）。"
                "请重启程序后重试；仍失败就说明有别的程序在用这个端口。"
            )
            return False, _record_start_failure(message)

        thread = threading.Thread(
            target=_serve, args=(server,), name="erp-mobile-listener", daemon=True
        )
        _state.update({"server": server, "thread": thread, "error": "", "port": int(port)})
        thread.start()
        _clear_start_failure()
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
        # 本进程内的即时错误（监听线程死掉等）。
        "error": listener_error(),
        # 上次启动失败的原因（跨重启保留）。与 `error` 分开是必要的：
        # 冷启动重试成功时 `error` 为空，但「上次为什么没起来」仍然该给店主看到。
        "last_start_error": last_start_error(),
    }
