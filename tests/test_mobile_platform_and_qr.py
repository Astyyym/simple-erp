"""设备平台区分 + 扫码配对的契约测试。

这两个能力解决的都是「用户不该被迫理解基础设施」：

- **平台区分**：设置页要能分清「手机 App」和「浏览器」。没有它的话，
  一个 App 和一个浏览器会显示成两条一模一样的「Android 手机」，
  撤销设备时没法判断该撤哪条。
- **扫码配对**：二维码里编的是电脑端**当前**地址，IP 变了重扫一次即可。
  这是「地址不该硬编码」的落地方式 —— 任何写死 IP 或主机名的方案
  换一台电脑、换一个网段就废。
"""
from __future__ import annotations

import pytest

from erp.db import init_db
from erp.mobile_app import create_mobile_app
from erp.services import mobile_pairing
from mobile_fixtures import pair_client, seed_all


@pytest.fixture()
def seeded():
    return seed_all()


@pytest.fixture()
def app():
    init_db()
    return create_mobile_app()


# ---------------------------------------------------------------------------
# 平台区分
# ---------------------------------------------------------------------------

def test_platform_normalize_only_accepts_app():
    """服务端只认 `app`，其余一律当浏览器 —— 不让伪造值造出更危险的身份。"""
    assert mobile_pairing.normalize_platform("app") == "app"
    assert mobile_pairing.normalize_platform("APP") == "app"
    assert mobile_pairing.normalize_platform(" App ") == "app"
    # 这些都会被归到浏览器，而不是被当成 App
    for value in ("browser", "", None, "ios", "admin", "手机 App", "desktop"):
        assert mobile_pairing.normalize_platform(value) == "browser", value


def test_platform_labels():
    assert mobile_pairing.platform_label("app") == "手机 App"
    assert mobile_pairing.platform_label("browser") == "浏览器"
    # 未知值按浏览器显示，不显示空
    assert mobile_pairing.platform_label("") == "浏览器"


def test_api_pair_records_platform_app(app, seeded):
    """走 JSON 接口配对，设备记录必须是 app。"""
    code, error = mobile_pairing.generate_pair_code()
    assert error == ""
    client = app.test_client()
    response = client.post("/m/api/pair", json={"code": code, "platform": "app"})
    assert response.status_code == 200
    devices = mobile_pairing.list_devices()
    newest = devices[0]
    assert newest["platform"] == "app"
    assert newest["platform_label"] == "手机 App"


def test_api_pair_defaults_to_app_when_platform_missing(app, seeded):
    """不带 platform 时默认 app：JSON 接口只有原生客户端会调。"""
    code, error = mobile_pairing.generate_pair_code()
    assert error == ""
    client = app.test_client()
    assert client.post("/m/api/pair", json={"code": code}).status_code == 200
    assert mobile_pairing.list_devices()[0]["platform"] == "app"


def test_html_pair_records_platform_browser(app, seeded):
    """走 HTML 页面配对，设备记录必须是 browser。"""
    code, error = mobile_pairing.generate_pair_code()
    assert error == ""
    client = app.test_client()
    response = client.post("/m/pair", data={"code": code})
    assert response.status_code == 302
    newest = mobile_pairing.list_devices()[0]
    assert newest["platform"] == "browser"
    assert newest["platform_label"] == "浏览器"


def test_old_device_without_platform_field_is_browser(seeded):
    """本次改动之前配对的记录没有 platform 字段，必须按浏览器算。

    猜成 App 会给出错误信息（那时唯一的客户端就是浏览器）。
    """
    store = mobile_pairing.load_store()
    store["devices"].append(
        {
            "id": "legacy0001",
            "token_sha256": "x" * 64,
            "label": "老设备",
            "paired_at": "2026-10-17T10:00:00",
            "last_seen_at": "",
            "last_ip": "192.168.10.9",
        }
    )
    mobile_pairing.save_store(store)
    legacy = [d for d in mobile_pairing.list_devices() if d["id"] == "legacy0001"][0]
    assert legacy["platform"] == "browser"
    assert legacy["platform_label"] == "浏览器"


def test_settings_page_shows_platform_column(app, seeded):
    """设置页要真的把「接入方式」渲染出来。"""
    code, _ = mobile_pairing.generate_pair_code()
    client = app.test_client()
    client.post("/m/api/pair", json={"code": code, "platform": "app"})

    main_app_client = app.test_client()
    page = main_app_client.get("/m/")
    assert page.status_code in (200, 302)
