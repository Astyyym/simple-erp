"""E-5：店铺信息引导（打印用店铺信息首次使用提示）。

计划条目（`开发短计划/2026-10-08_旧数据迁移承接与备份恢复_执行计划.md` §五 Batch E）：
迁移用户首次使用时提示填写打印用店铺信息（订货电话/地址/主体业务/法律声明），
否则打印件为空白。

实现口径（本文件锁住）：
- **只提示真的会印成空白的字段**：`print_order_phone` / `print_order_address` 在
  `CONFIG_DEFAULTS` 里没有出厂值；主营业务与备注（法律句）有出厂文案，不算缺失，
  否则提示会永远挂着、退化成噪声。
- 提示出现在**工作台**（迁移用户第一眼看到的地方），一条通往设置页对应卡片的链接。
- 可关闭、也可重新打开（对称端点），关闭只写一个布尔开关，**不动任何打印字段**。
- 提示口径只有一处（`config.missing_print_info_fields`），页面与路由共用。
"""

from __future__ import annotations

import re

from erp import create_app
from erp.config import CONFIG_DEFAULTS, PRINT_INFO_REQUIRED_FIELDS, load_config, missing_print_info_fields, save_config
from erp.db import init_db

NOTICE_ID = 'id="printInfoNotice"'


def _client():
    init_db()
    return create_app().test_client()


def _fill_print_info(client, *, phone="0571-88886666", address="杭州市西湖区文三路 100 号"):
    """通过真实设置页 POST 填写打印信息（不直接改文件，走用户路径）。"""
    return client.post(
        "/settings/save",
        data={
            "shop_name": "虚构消防器材门店",
            "ui_theme": "light",
            "ui_scale": "100",
            "ui_font_weight": "standard",
            "print_order_phone": phone,
            "print_order_address": address,
            "print_main_business": "虚构主营业务",
            "print_legal_note": "虚构法律句",
            "print_maker_name": "",
            "print_receiver_label": "收货人：____________",
        },
    )


# --------------------------- 口径 ---------------------------

def test_missing_fields_only_counts_blank_print_fields():
    assert missing_print_info_fields({}) == ["订货电话", "订货地址"]
    assert missing_print_info_fields({"print_order_phone": "  ", "print_order_address": None}) == ["订货电话", "订货地址"]
    assert missing_print_info_fields({"print_order_phone": "0571", "print_order_address": "杭州"}) == []
    # 只缺一项时只报那一项，不整条一起喊。
    assert missing_print_info_fields({"print_order_phone": "0571", "print_order_address": ""}) == ["订货地址"]


def test_out_of_box_defaults_do_not_include_the_two_required_fields():
    """出厂默认值必须不含这两项——否则提示永远不会出现，等于没做。"""
    for key, _label in PRINT_INFO_REQUIRED_FIELDS:
        assert not str(CONFIG_DEFAULTS.get(key) or "").strip(), f"{key} 不应有出厂默认值"
    # 主营业务/法律句有出厂文案 → 不算缺失（这是刻意的，不是遗漏）。
    assert str(CONFIG_DEFAULTS["print_main_business"]).strip()
    assert str(CONFIG_DEFAULTS["print_legal_note"]).strip()


# --------------------------- 工作台提示 ---------------------------

def test_workbench_shows_print_info_notice_when_blank():
    client = _client()
    html = client.get("/").get_data(as_text=True)

    assert NOTICE_ID in html
    notice = html.split(NOTICE_ID, 1)[1].split("</section>", 1)[0]
    assert "打印单上还有空白" in notice
    assert "订货电话" in notice and "订货地址" in notice
    assert 'href="/settings/#printInfoSection"' in notice
    assert "不再提示" in notice


def test_workbench_notice_disappears_after_filling_print_info():
    client = _client()
    assert NOTICE_ID in client.get("/").get_data(as_text=True)

    _fill_print_info(client)
    html = client.get("/").get_data(as_text=True)
    assert NOTICE_ID not in html
    # 真的落到了配置里（走用户 POST 路径，不是直接改文件）。
    cfg = load_config()
    assert cfg["print_order_phone"] == "0571-88886666"
    assert cfg["print_order_address"] == "杭州市西湖区文三路 100 号"


def test_workbench_notice_reports_only_the_field_still_missing():
    client = _client()
    _fill_print_info(client, phone="0571-88886666", address="")
    html = client.get("/").get_data(as_text=True)

    assert NOTICE_ID in html
    notice = html.split(NOTICE_ID, 1)[1].split("</section>", 1)[0]
    assert "订货地址" in notice
    assert "订货电话" not in notice


# --------------------------- 关闭 / 重新打开 ---------------------------

def test_dismiss_hides_notice_without_touching_print_fields():
    client = _client()
    response = client.post("/settings/print-info/dismiss", follow_redirects=True)
    assert response.status_code == 200

    assert NOTICE_ID not in client.get("/").get_data(as_text=True)
    cfg = load_config()
    assert cfg["print_info_prompt_dismissed"] is True
    # 关提示不能顺手把打印字段写掉。
    assert cfg["print_order_phone"] == ""
    assert cfg["print_order_address"] == ""
    assert cfg["print_main_business"] == CONFIG_DEFAULTS["print_main_business"]


def test_restore_reopens_notice_and_is_idempotent():
    client = _client()
    client.post("/settings/print-info/dismiss", follow_redirects=True)
    assert NOTICE_ID not in client.get("/").get_data(as_text=True)

    client.post("/settings/print-info/restore", follow_redirects=True)
    assert NOTICE_ID in client.get("/").get_data(as_text=True)
    assert load_config()["print_info_prompt_dismissed"] is False

    # 重复打开/关闭都不应报错或翻转成反状态。
    client.post("/settings/print-info/restore", follow_redirects=True)
    assert load_config()["print_info_prompt_dismissed"] is False


def test_filled_fields_win_over_dismiss_flag():
    """补全后即使提示没被关，也不该再出现（关闭开关不是显示的唯一条件）。"""
    client = _client()
    _fill_print_info(client)
    assert NOTICE_ID not in client.get("/").get_data(as_text=True)
    assert load_config()["print_info_prompt_dismissed"] is False


# --------------------------- 设置页 ---------------------------

def test_settings_page_has_print_info_section_with_missing_warning():
    client = _client()
    html = client.get("/settings/").get_data(as_text=True)

    assert 'id="printInfoSection"' in html
    assert "打印件上会留空白" in html
    assert "订货电话" in html and "订货地址" in html
    # 关闭/重开的入口在设置页，用户关掉后能找回来。
    assert 'id="printInfoPromptSection"' in html
    assert "/settings/print-info/dismiss" in html
    assert "不再提示" in html


def test_settings_page_switches_prompt_button_after_dismiss():
    client = _client()
    client.post("/settings/print-info/dismiss", follow_redirects=True)
    html = client.get("/settings/").get_data(as_text=True)

    assert "/settings/print-info/restore" in html
    assert "重新打开提示" in html
    assert "当前：已关闭" in html


def test_settings_page_warning_disappears_when_filled():
    client = _client()
    _fill_print_info(client)
    html = client.get("/settings/").get_data(as_text=True)

    assert "打印件上会留空白" not in html


# --------------------------- 打印件真的不再空白 ---------------------------

def test_print_preview_renders_the_filled_shop_info():
    """端到端：填了打印信息后，设置页样张真的印出来（不是只改了配置）。"""
    client = _client()
    _fill_print_info(client, phone="0571-88886666", address="杭州市西湖区文三路 100 号")
    html = client.get("/settings/print-preview").get_data(as_text=True)

    assert "0571-88886666" in html
    assert "杭州市西湖区文三路 100 号" in html
    # 确认这两个值落在打印模板的对应位置，而不是别处碰巧出现。
    assert re.search(r"订货电话：</strong>0571-88886666", html)
    assert re.search(r"订货地址：</strong>杭州市西湖区文三路 100 号", html)


def test_workbench_notice_never_prints_raw_config_keys():
    """提示文案是给人看的中文名，不能漏出内部键名。"""
    html = _client().get("/").get_data(as_text=True)
    notice = html.split(NOTICE_ID, 1)[1].split("</section>", 1)[0]
    for key, _label in PRINT_INFO_REQUIRED_FIELDS:
        assert key not in notice


def test_dismiss_flag_survives_config_reload_and_merge():
    """开关能持久化，且旧配置（无该键）读出来是 False，不是 None。"""
    save_config({"print_info_prompt_dismissed": True})
    assert load_config()["print_info_prompt_dismissed"] is True
    save_config({"print_info_prompt_dismissed": False})
    assert load_config()["print_info_prompt_dismissed"] is False
