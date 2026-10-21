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

# 2026-10-21：工作台顶部那张「打印单上还有空白」提示卡已按用户要求废弃，
# 改由首次使用引导弹窗承担（同一件事不再提示两次）。这里保留旧常量仅用于
# **反向断言**：旧卡必须彻底消失，不能和新弹窗同时出现。
LEGACY_NOTICE_ID = 'id="printInfoNotice"'
NOTICE_ID = 'id="firstRunModal"'


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

def test_workbench_shows_first_run_modal_when_shop_name_is_default():
    """公司名称仍是出厂默认值时，工作台要弹首次使用引导。"""
    client = _client()
    html = client.get("/").get_data(as_text=True)

    assert NOTICE_ID in html
    modal = html.split(NOTICE_ID, 1)[1].split("</form>", 1)[0]
    # 三个字段都要在：公司名称 + 订货电话 + 订货地址。
    assert 'name="shop_name"' in modal
    assert 'name="print_order_phone"' in modal
    assert 'name="print_order_address"' in modal
    # 旧提示卡必须彻底消失，不能两处都提示。
    assert LEGACY_NOTICE_ID not in html
    # 三个出口都在：保存、稍后再说、不再提示。
    assert "保存并开始使用" in modal
    assert "稍后再说" in modal
    assert "不再提示" in modal


def test_first_run_modal_disappears_after_saving_shop_name():
    client = _client()
    assert NOTICE_ID in client.get("/").get_data(as_text=True)

    client.post("/settings/first-run/save", data={
        "shop_name": "虚构消防器材门店",
        "print_order_phone": "0571-88886666",
        "print_order_address": "杭州市西湖区文三路 100 号",
    }, follow_redirects=True)

    html = client.get("/").get_data(as_text=True)
    assert NOTICE_ID not in html
    # 真的落到了配置里（走用户 POST 路径，不是直接改文件）。
    cfg = load_config()
    assert cfg["shop_name"] == "虚构消防器材门店"
    assert cfg["print_order_phone"] == "0571-88886666"
    assert cfg["print_order_address"] == "杭州市西湖区文三路 100 号"


def test_first_run_modal_stays_when_shop_name_kept_as_default():
    """只填打印信息、公司名仍是默认值 → 引导仍要弹（公司名才是触发条件）。"""
    client = _client()
    client.post("/settings/first-run/save", data={
        "shop_name": "简单ERP",
        "print_order_phone": "0571-88886666",
        "print_order_address": "",
    }, follow_redirects=True)

    html = client.get("/").get_data(as_text=True)
    assert NOTICE_ID in html
    assert load_config()["print_order_phone"] == "0571-88886666"


# --------------------------- 关闭 / 重新打开 ---------------------------

def test_dismiss_hides_modal_without_touching_print_fields():
    client = _client()
    response = client.post("/settings/first-run/dismiss", follow_redirects=True)
    assert response.status_code == 200

    assert NOTICE_ID not in client.get("/").get_data(as_text=True)
    cfg = load_config()
    assert cfg["first_run_prompt_dismissed"] is True
    # 关提示不能顺手把打印字段写掉。
    assert cfg["print_order_phone"] == ""
    assert cfg["print_order_address"] == ""
    assert cfg["print_main_business"] == CONFIG_DEFAULTS["print_main_business"]


def test_snooze_hides_modal_then_returns_after_cooldown():
    """「稍后再说」= 推迟 7 天：当天不再弹，冷却期过后重新弹。"""
    client = _client()
    client.post("/settings/first-run/snooze", follow_redirects=True)
    assert NOTICE_ID not in client.get("/").get_data(as_text=True)

    cfg = load_config()
    assert cfg["first_run_prompt_snoozed_until"]
    # 冷却期内不弹。
    from erp.config import first_run_prompt_due
    assert first_run_prompt_due(cfg) is False
    # 把冷却改成已过期 → 重新弹（模拟 7 天后）。
    save_config({"first_run_prompt_snoozed_until": "2020-01-01"})
    assert NOTICE_ID in client.get("/").get_data(as_text=True)


def test_filled_shop_name_wins_over_dismiss_flag():
    """填了公司名后即使没关提示，也不该再弹（关闭开关不是显示的唯一条件）。"""
    client = _client()
    _fill_print_info(client)
    assert NOTICE_ID not in client.get("/").get_data(as_text=True)
    assert load_config()["first_run_prompt_dismissed"] is False


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
    # 2026-10-17：HTML 样张路由已删除，改为直接渲染同一个样张模板
    #（PDF 生成函数 generate_settings_print_preview_pdf 走的也是这一套 context + 模板）。
    from flask import render_template

    from erp.utils.money import cents_to_yuan
    from erp.utils.pdf import settings_print_preview_context

    app = create_app()
    with app.app_context():
        order, items, config = settings_print_preview_context()
        html = render_template(
            "orders/print_template.html",
            order=order, items=items, config=config, cents_to_yuan=cents_to_yuan,
        )

    assert "0571-88886666" in html
    assert "杭州市西湖区文三路 100 号" in html
    # 确认这两个值落在打印模板的对应位置，而不是别处碰巧出现。
    assert re.search(r"订货电话：</strong>0571-88886666", html)
    assert re.search(r"订货地址：</strong>杭州市西湖区文三路 100 号", html)


def test_first_run_modal_never_prints_raw_config_keys():
    """弹窗文案是给人看的中文名，不能把内部键名当标签漏出来。"""
    html = _client().get("/").get_data(as_text=True)
    modal = html.split(NOTICE_ID, 1)[1].split("</form>", 1)[0]
    for key, _label in PRINT_INFO_REQUIRED_FIELDS:
        # 键名只允许出现在 name= 属性里，不允许出现在可见文案中。
        assert f">{key}<" not in modal
        assert f">{key}:" not in modal


def test_dismiss_flag_survives_config_reload_and_merge():
    """开关能持久化，且旧配置（无该键）读出来是 False，不是 None。"""
    save_config({"print_info_prompt_dismissed": True})
    assert load_config()["print_info_prompt_dismissed"] is True
    save_config({"print_info_prompt_dismissed": False})
    assert load_config()["print_info_prompt_dismissed"] is False
