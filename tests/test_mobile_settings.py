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

    match = re.search(r"letter-spacing:10px[^>]*>(\d{6})<", body)
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
    """生成配对码后浏览器会把滚动位置重置到顶部 —— 响应里必须带「把码滚进视野」的脚本。"""
    from erp import create_app

    init_db()
    port = _free_port()
    ok, error = mobile_server.start_mobile_listener(port=port)
    assert ok, error

    client = create_app().test_client()
    body = client.post("/settings/mobile/pair-code").get_data(as_text=True)
    assert 'id="mobilePairCode"' in body
    assert "scrollIntoView" in body

    # 普通 GET 不该带这段脚本（不改变别的页面滚动行为）
    plain = client.get("/settings/").get_data(as_text=True)
    assert "mobilePairCode" not in plain


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
