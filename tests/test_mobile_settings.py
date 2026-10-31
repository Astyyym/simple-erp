"""手机访问开关（S0）：设置页分区、第二监听的起停、端口占用报错。

这里会真的起一个 waitress 监听（用临时空闲端口，不抢 5001），再用 urllib 打一次
真实 HTTP —— 只测 `create_server` 能不能起来是不够的，手机端要的是「端口真的能用」。
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request

import pytest

from erp import mobile_server
from erp.db import init_db
from erp.services import mobile_pairing


@pytest.fixture(autouse=True)
def stop_after_each():
    yield
    mobile_server.stop_mobile_listener()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不让 urllib 自动跟 302：要断言的就是重定向本身。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _wait_for_http(url: str, timeout: float = 10.0, *, follow_redirects: bool = True) -> int:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        opener = urllib.request.build_opener(
            *( [] if follow_redirects else [_NoRedirect()] )
        )
        try:
            with opener.open(url, timeout=2) as response:  # noqa: S310 - 本机回环
                return response.status
        except urllib.error.HTTPError as error:  # 404/302 也算「端口活着」
            return error.code
        except Exception as error:  # noqa: BLE001
            last_error = error
            time.sleep(0.15)
    raise AssertionError(f"监听没有起来：{last_error}")


def test_settings_page_has_mobile_section():
    from erp import create_app

    init_db()
    client = create_app().test_client()
    html = client.get("/settings/").get_data(as_text=True)
    assert "手机访问" in html
    assert 'id="mobileAccessSection"' in html
    assert "打开手机访问" in html
    assert "诊断信息" in html


def test_pair_code_requires_listener_first():
    from erp import create_app

    init_db()
    client = create_app().test_client()
    response = client.post("/settings/mobile/pair-code", follow_redirects=True)
    assert response.status_code == 200
    assert "请先打开「手机访问」开关" in response.get_data(as_text=True)


def test_listener_starts_and_serves_pair_page_then_releases_port():
    init_db()
    port = _free_port()
    ok, error = mobile_server.start_mobile_listener(port=port)
    assert ok, error
    assert mobile_server.is_listening()
    assert mobile_server.status()["port"] == port

    # 未配对访问 /m/ → 302 到配对页；配对页本身 200
    assert _wait_for_http(f"http://127.0.0.1:{port}/m/", follow_redirects=False) == 302
    assert _wait_for_http(f"http://127.0.0.1:{port}/m/pair") == 200
    # 蓝图外天然 404（第二监听只挂 mobile_bp）
    assert _wait_for_http(f"http://127.0.0.1:{port}/customers/") == 404

    mobile_server.stop_mobile_listener()
    assert not mobile_server.is_listening()
    # 端口必须放开，否则下次启动仍被占
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_port_conflict_reports_instead_of_drifting():
    init_db()
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("0.0.0.0", port))
        blocker.listen(1)
        ok, error = mobile_server.start_mobile_listener(port=port)
        assert ok is False
        assert f"端口 {port} 被占用" in error
        assert not mobile_server.is_listening()
        assert mobile_server.listener_error() == error
    finally:
        blocker.close()


def test_toggle_route_saves_flag_and_reports_failure():
    from erp import create_app
    from erp.config import load_config

    init_db()
    app = create_app()
    client = app.test_client()

    # 打开：真起监听（用默认 5001；若本机被占，这个分支会走失败并回弹开关）
    response = client.post("/settings/mobile/toggle", data={"enabled": "1"}, follow_redirects=True)
    assert response.status_code == 200
    if mobile_server.is_listening():
        assert load_config()["mobile_access_enabled"] is True
        # 再关掉
        response = client.post("/settings/mobile/toggle", data={"enabled": "0"}, follow_redirects=True)
        assert response.status_code == 200
        assert not mobile_server.is_listening()
    assert load_config()["mobile_access_enabled"] is False


def test_revoke_route_removes_device():
    from erp import create_app

    init_db()
    code, _ = mobile_pairing.generate_pair_code()
    mobile_pairing.verify_pair_code(code, label="要被撤销的手机")
    device = mobile_pairing.list_devices()[0]

    client = create_app().test_client()
    response = client.post(
        "/settings/mobile/revoke", data={"device_id": device["id"]}, follow_redirects=True
    )
    assert response.status_code == 200
    assert "已撤销这台手机" in response.get_data(as_text=True)
    assert mobile_pairing.list_devices() == []


def test_revoke_all_route_reports_count():
    from erp import create_app

    init_db()
    for _ in range(2):
        code, _ = mobile_pairing.generate_pair_code()
        mobile_pairing.verify_pair_code(code)
    client = create_app().test_client()
    response = client.post("/settings/mobile/revoke-all", follow_redirects=True)
    assert "已撤销全部 2 台手机" in response.get_data(as_text=True)


def test_pick_lan_address_ignores_tun_and_link_local():
    """TUN 代理网卡（198.18.0.0/15）会抢默认路由，绝不能当成手机能连的地址。

    本机实测：`udp connect 8.8.8.8` 拿到的是 `198.18.0.1`（Meta 网卡），
    手机扫到那个地址永远连不上。
    """
    pick = mobile_server._pick_lan_address
    assert pick(["198.18.0.1"]) == "127.0.0.1"
    assert pick(["198.18.0.1", "192.168.10.5"]) == "192.168.10.5"
    assert pick(["169.254.36.183", "10.1.2.3"]) == "10.1.2.3"
    # 优先 192.168，其次 10.，再次 172.16–31
    assert pick(["172.20.0.9", "10.0.0.5", "192.168.1.20"]) == "192.168.1.20"
    assert pick(["172.15.0.1"]) == "127.0.0.1"           # 172.15 不是私网段
    assert pick(["172.31.255.1"]) == "172.31.255.1"
    assert pick(["0.0.0.0", "127.0.0.1"]) == "127.0.0.1"
    assert mobile_server._is_lan_ip("192.168.10.5") is True
    assert mobile_server._is_lan_ip("198.18.0.1") is False


def test_status_lan_address_never_reports_a_proxy_range():
    init_db()
    snapshot = mobile_server.status()
    assert not snapshot["lan_ip"].startswith(("198.18.", "198.19."))
    # 警告文案与地址判定必须一致（一个说能连、一个说不能，等于没修）
    assert (snapshot["lan_warning"] == "") is snapshot["lan_usable"]


def test_pair_code_is_rendered_inline_and_actually_works():
    """配对码必须出现在 POST 响应的正文里（不是页顶 flash）。

    用户在页面下半部点按钮，redirect + 顶部 flash 的形态他根本看不见 —— 这条是用户实测
    反馈后补的回归：点完只会「页面刷了一下、什么也没出现」。

    这条同时守住「无 JS 兜底路径」：前端有 JS 时会走 fetch 拿 JSON，
    但表单直提交（JS 挂了）必须仍然渲染出一个能用的码。
    """
    import re

    from erp import create_app

    init_db()
    port = _free_port()
    ok, error = mobile_server.start_mobile_listener(port=port)
    assert ok, error

    client = create_app().test_client()
    response = client.post("/settings/mobile/pair-code")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "秒内有效" in body

    # 从配对码元素里取码（不要匹配内联样式：样式一改测试就红，那是脆断言）。
    match = re.search(r'id="mobilePairCode"[^>]*>\s*(\d{6})\s*<', body)
    assert match, "POST 响应里找不到 6 位配对码"
    cookie_value, pair_error = mobile_pairing.verify_pair_code(match.group(1))
    assert pair_error == ""
    assert mobile_pairing.device_from_cookie(cookie_value) is not None


def test_mobile_actions_report_inside_the_section_not_at_page_top():
    from erp import create_app

    init_db()
    client = create_app().test_client()
    response = client.post("/settings/mobile/toggle", data={"enabled": "0"}, follow_redirects=True)
    body = response.get_data(as_text=True)
    section = body.split('id="mobileAccessSection"', 1)[1]
    assert "手机访问已关闭" in section


def test_pair_code_response_scrolls_itself_into_view():
    """生成配对码后浏览器会把滚动位置重置到顶部 —— 响应里必须带「把码滚进视野」的脚本。

    注意断言的是**滚动脚本本身**，不是 `mobilePairCode` 这个 id：2026-10-23 起
    配对码面板是常驻的（刷新页面还能看到同一个码、倒计时每秒走），
    所以 id 在普通 GET 里当然也在。这条测试真正要守的是
    「只有刚生成码的那一次响应才自动滚过去」。
    """
    from erp import create_app

    init_db()
    port = _free_port()
    ok, error = mobile_server.start_mobile_listener(port=port)
    assert ok, error

    client = create_app().test_client()
    body = client.post("/settings/mobile/pair-code").get_data(as_text=True)
    assert 'id="mobilePairCode"' in body
    assert "scrollIntoView" in body

    # 普通 GET 不该带这段自动滚动（否则每次打开设置页都会被拽到手机访问那一节）。
    plain = client.get("/settings/").get_data(as_text=True)
    assert "scrollIntoView({block: 'center'})" not in plain


def test_pair_code_state_endpoint_reports_live_countdown():
    """配对码状态接口：设置页倒计时与「换一个」都靠它。

    上一版的真缺陷：码只在 POST 响应里出现一次，有效期是渲染那一刻算出的静态文字，
    既不会倒数也没有任何入口能再看一眼或换一个（用户 2026-10-23 明确报了这一条）。
    """
    from erp import create_app

    init_db()
    port = _free_port()
    ok, error = mobile_server.start_mobile_listener(port=port)
    assert ok, error

    client = create_app().test_client()

    # 还没生成时：ok 但空码
    payload = client.get(
        "/settings/mobile/pair-code", headers={"Accept": "application/json"}
    ).get_json()
    assert payload["ok"] is True
    assert payload["code"] == ""
    assert payload["seconds_left"] == 0

    # 生成后：明文码 + 剩余秒数都能再取到（不是「只显示一次」）
    generated = client.post(
        "/settings/mobile/pair-code",
        headers={"Accept": "application/json", "X-Requested-With": "fetch"},
    ).get_json()
    assert generated["ok"] is True
    assert len(generated["code"]) == 6
    assert generated["seconds_left"] == mobile_pairing.PAIR_CODE_TTL_SECONDS

    state = client.get(
        "/settings/mobile/pair-code", headers={"Accept": "application/json"}
    ).get_json()
    assert state["code"] == generated["code"], "同一个码必须能反复取到，用户才能回头看一眼"
    assert 0 < state["seconds_left"] <= mobile_pairing.PAIR_CODE_TTL_SECONDS


def test_pair_code_json_regeneration_replaces_previous_code():
    """「换一个」必须真的换：新码生效，旧码立刻作废。"""
    from erp import create_app

    init_db()
    port = _free_port()
    ok, error = mobile_server.start_mobile_listener(port=port)
    assert ok, error

    client = create_app().test_client()
    headers = {"Accept": "application/json", "X-Requested-With": "fetch"}
    first = client.post("/settings/mobile/pair-code", headers=headers).get_json()["code"]
    second = client.post("/settings/mobile/pair-code", headers=headers).get_json()["code"]
    assert first != second

    # 旧码不能再配对（服务端只存最新一个 hash）
    _, old_error = mobile_pairing.verify_pair_code(first)
    assert old_error != ""
    cookie_value, new_error = mobile_pairing.verify_pair_code(second)
    assert new_error == ""
    assert mobile_pairing.device_from_cookie(cookie_value) is not None

    # 码用掉之后，状态接口不能还吐一个永远无效的明文码
    state = client.get(
        "/settings/mobile/pair-code", headers={"Accept": "application/json"}
    ).get_json()
    assert state["code"] == ""
    assert state["seconds_left"] == 0


def test_devices_json_endpoint_returns_devices():
    """设置页轮询接口必须真的能返回 JSON。

    这个接口是给「已配对设备」自动刷新用的（2026-10-22）：设置页是服务端渲染，
    配对成功后页面不会自己变，前端每 4 秒拉一次这里。

    之所以专门加这条测试：第一版写完漏了 `from flask import jsonify`，
    接口直接 500，而**页面看起来只是「不刷新」**，跟「功能没做」一模一样，
    排查时白花了很多时间。这条测试能在打包前就把它拦住。
    """
    from erp import create_app

    client = create_app().test_client()
    response = client.get("/settings/mobile/devices", headers={"Accept": "application/json"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload is not None, "必须返回 JSON，不能是 HTML 或错误页"
    assert payload["ok"] is True
    assert isinstance(payload["devices"], list)
    assert payload["count"] == len(payload["devices"])


# ---------------------------------------------------------------------------
# 关闭提醒的数据源（2026-10-24）
# ---------------------------------------------------------------------------

def test_close_state_endpoint_reports_listening_and_devices():
    """关闭提醒浮层的数据源必须是完整 JSON。

    它和 `mobile/devices` 是同族缺陷风险：接口 500 时页面只是「弹出来但没有时间」，
    看起来像样式没加载，而不像后端挂了 —— 所以这里逐字段断。

    `listening` 是这次新增的关键字段：**开关开着 ≠ 监听起来了**（5001 被占时
    开关会回弹、监听不启动）。缺了它，浮层会让店主拿起手机去同步，而那一定失败。
    """
    from erp import create_app
    from erp.services import mobile_pairing

    client = create_app().test_client()
    response = client.get("/settings/mobile/close-state", headers={"Accept": "application/json"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload is not None
    assert payload["ok"] is True
    # 没有设备时不能要求提醒（桌面壳据此直接放行关闭）。
    assert payload["has_devices"] == bool(mobile_pairing.list_devices())
    assert isinstance(payload["devices"], list)
    assert isinstance(payload["listening"], bool)
    assert "listening_error" in payload
    # 没在监听时二维码必须为空（给一张连不上的码等于教店主白扫一次）。
    if not payload["listening"]:
        assert payload["qr"] == ""
    # 相对时间要么是 null（从未同步），要么是非负整数。
    for device in payload["devices"]:
        seconds = device["last_sync_seconds"]
        assert seconds is None or seconds >= 0


def test_close_state_lists_device_uid_and_sync_fields():
    """设备条目必须带 uid 与同步时间 —— 浮层要用它们说「哪台、多久前同步的」。"""
    from erp import create_app

    client = create_app().test_client()
    payload = client.get("/settings/mobile/close-state").get_json()
    for device in payload["devices"]:
        assert "uid_short" in device
        assert "last_sync_label" in device
        assert "last_sync_seconds" in device


def test_diagnostics_flags_never_connected_when_nothing_ever_arrived():
    """一台手机都没连进来过时，诊断区必须给出可执行结论（2026-10-31 门店事故）。

    那天的三行诊断全是「对的」：最后一次手机成功请求 —、上次错误 无、端口自检通过，
    但没有一句能让店主读出「手机从没连进来过」—— 于是白查了一上午。
    """
    from erp import create_app
    from erp.config import save_config

    init_db()
    save_config({"mobile_access_enabled": True})
    # 起一个真实监听（临时端口，不抢 5001）：只有「正在监听」时诊断里才会出现
    # 「端口自检通过」，那句自曝局限的文案也才在页面上。
    port = _free_port()
    ok, error = mobile_server.start_mobile_listener(port=port)
    assert ok, error
    html = create_app().test_client().get("/settings/").get_data(as_text=True)
    assert 'id="mobileNeverConnected"' in html
    # 用提示正文里的独有短语证明整块渲染了（刷新脚本的注释里不会出现这句）。
    assert "如果手机那边已经试过扫码" in html
    # 端口自检必须自曝局限：它是本机回环，看不见防火墙。
    assert "不代表手机能连" in html


def test_diagnostics_never_connected_clears_after_a_device_pairs():
    """配对成功一台设备后，那条「从没连进来过」的提示必须消失。

    注意判据不是 `last_success_at`（它要等手机真的发请求才写），而是「有没有设备」——
    页面打开的那一刻还没收到请求是正常的，不能因此说人家没连上。
    """
    from erp import create_app
    from erp.config import save_config

    init_db()
    save_config({"mobile_access_enabled": True})
    code, error = mobile_pairing.generate_pair_code()
    assert error == ""
    cookie_value, error = mobile_pairing.verify_pair_code(code, label="测试手机", platform="app")
    assert error == ""
    assert cookie_value

    html = create_app().test_client().get("/settings/").get_data(as_text=True)
    # 只按 id + 正文独有短语断言：刷新脚本的注释里也提到这条提示，按中文短语泛断言会被注释骗过。
    assert 'id="mobileNeverConnected"' not in html
    assert "如果手机那边已经试过扫码" not in html
