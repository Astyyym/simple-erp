from pathlib import Path
from uuid import uuid4

import pytest

from erp import create_app
from erp import config as config_module
from erp.config import load_config, migrate_data_root, data_root, save_config
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def test_settings_page_renders_and_nav_link():
    init_db()
    app = create_app()
    client = app.test_client()
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/settings/"' in home.get_data(as_text=True)

    page = client.get("/settings/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "公司名称" in html
    assert "订货电话" in html
    assert "一键迁移并切换" in html
    assert "预览打印效果" in html
    assert "生成预览 PDF" in html
    assert 'id="settingsPrintPreviewPdf"' in html
    assert "/settings/print-preview.pdf" in html
    assert "打印位置校准" in html
    assert "A4 竖向（210×297mm）" in html
    assert "A4 纵向" in html
    assert "实际大小/100%" in html
    assert "241mm 长边先进" not in html
    assert 'name="print_offset_x_mm"' in html
    assert 'name="print_offset_y_mm"' in html
    assert 'name="print_scale"' in html
    assert "settingsLeaveModal" in html
    assert "保存并离开" in html
    assert "不保存离开" in html
    assert "data-dirty-guard" in html
    assert "浏览文件夹" in html
    assert "settingsBrowseFolder" in html
    assert "choose_folder" in html


def test_settings_page_hides_explanatory_microcopy_but_keeps_operation_guidance():
    init_db()
    html = create_app().test_client().get("/settings/").get_data(as_text=True)

    assert "公司信息、数据目录、界面显示与打印单文案" not in html
    assert "只改这一处" not in html
    assert "仅网页界面，不影响打印" not in html
    assert "正文大小与布局保持不变" not in html
    assert "保存后页面自动更新" in html
    assert "会询问是否先保存" in html
    assert "版式位置固定" in html
    assert "迁移包含 data / backups / imports / logs" in html


def test_settings_page_exposes_font_weight_choices_with_legacy_default():
    init_db()
    app = create_app()
    client = app.test_client()
    html = client.get("/settings/").get_data(as_text=True)

    assert 'data-ui-font-weight="standard"' in html
    assert 'name="ui_font_weight" value="standard"' in html
    assert 'name="ui_font_weight" value="medium"' in html
    assert 'name="ui_font_weight" value="strong"' in html
    assert 'id="displaySettings"' in html
    assert "字体粗细" in html
    assert "字体粗细（仅网页界面，不影响打印）" not in html
    assert "document.documentElement.dataset.uiFontWeight = input.value" in html
    assert config_module._merge_defaults({})["ui_font_weight"] == "standard"


def test_save_settings_persists_font_weight_and_rejects_unknown_choice():
    init_db()
    app = create_app()
    client = app.test_client()
    response = client.post(
        "/settings/save",
        data={
            "shop_name": "字体粗细测试店",
            "ui_theme": "light",
            "ui_scale": "100",
            "ui_font_weight": "strong",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert load_config()["ui_font_weight"] == "strong"
    assert 'data-ui-font-weight="strong"' in response.get_data(as_text=True)

    with pytest.raises(ValueError, match="字体粗细"):
        save_config({"ui_font_weight": "ultra"})


def test_settings_page_marks_desktop_copy_when_env_set(monkeypatch):
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    app = create_app()
    client = app.test_client()
    html = client.get("/settings/").get_data(as_text=True)
    assert "打开系统选夹" in html or "浏览文件夹" in html
    assert "浏览器开发预览请直接粘贴" not in html


def test_default_brand_and_storage_folder_names(monkeypatch, tmp_path):
    monkeypatch.delenv("ERP_LOCATION_FILE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(config_module, "is_frozen", lambda: True)

    assert config_module.location_file() == (
        tmp_path / "local-app-data" / "简单ERP" / "data_location.json"
    )
    assert config_module.default_data_root_display().endswith("简单ERP数据")


def test_desktop_api_choose_folder_and_title_helpers(monkeypatch):
    import desktop_app as desk

    class FakeWindow:
        def __init__(self):
            self.title = ""
            self.last_dialog = None

        def create_file_dialog(self, dialog_type):
            self.last_dialog = dialog_type
            return [r"D:\店里数据\简单ERP"]

        def set_title(self, title):
            self.title = title

    fake = FakeWindow()
    monkeypatch.setattr(desk, "_window", fake)
    api = desk.DesktopApi()
    assert api.is_desktop() is True
    # Without real pywebview installed in WSL, API still returns a path via fallback.
    assert api.choose_folder() == r"D:\店里数据\简单ERP"
    assert fake.last_dialog is not None
    assert api.set_window_title("简单ERP") is True
    assert fake.title == "简单ERP"
    assert api.set_window_title("  ") is True
    assert fake.title == "简单ERP"


def test_save_settings_can_redirect_to_next_path():
    init_db()
    app = create_app()
    client = app.test_client()
    new_name = f"跳转公司-{uuid4().hex[:6]}"
    resp = client.post(
        "/settings/save",
        data={
            "shop_name": new_name,
            "ui_theme": "light",
            "ui_scale": "100",
            "print_order_phone": "1",
            "print_order_address": "2",
            "print_main_business": "3",
            "print_legal_note": "4",
            "print_maker_name": "5",
            "print_receiver_label": "收货人：____",
            "next": "/orders/new",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].endswith("/orders/new")
    assert load_config()["shop_name"] == new_name


def test_save_settings_rejects_external_next_url():
    init_db()
    app = create_app()
    client = app.test_client()
    resp = client.post(
        "/settings/save",
        data={
            "shop_name": f"安全公司-{uuid4().hex[:6]}",
            "ui_theme": "light",
            "ui_scale": "100",
            "print_order_phone": "1",
            "print_order_address": "2",
            "print_main_business": "3",
            "print_legal_note": "4",
            "print_maker_name": "5",
            "print_receiver_label": "收货人：____",
            "next": "https://evil.example/phish",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/settings/" in resp.headers["Location"]
    assert "evil.example" not in resp.headers["Location"]


def test_save_company_name_syncs_app_name_and_topbar():
    init_db()
    app = create_app()
    client = app.test_client()
    new_name = f"测试公司-{uuid4().hex[:6]}"
    resp = client.post(
        "/settings/save",
        data={
            "shop_name": new_name,
            "ui_theme": "dark",
            "ui_scale": "125",
            "print_order_phone": "10086",
            "print_order_address": "测试地址",
            "print_main_business": "测试主营",
            "print_legal_note": "测试备注句",
            "print_maker_name": "测试制单",
            "print_receiver_label": "收货人：____",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    cfg = load_config()
    assert cfg["shop_name"] == new_name
    assert cfg["app_name"] == new_name
    assert cfg["ui_theme"] == "dark"
    assert cfg["ui_scale"] == "125"
    assert cfg["print_order_phone"] == "10086"
    assert cfg["print_maker_name"] == "测试制单"

    home = client.get("/")
    html = home.get_data(as_text=True)
    assert new_name in html
    assert 'data-ui-theme="dark"' in html
    assert 'data-ui-scale="125"' in html


def test_save_settings_print_offsets_and_scale():
    init_db()
    app = create_app()
    client = app.test_client()
    resp = client.post(
        "/settings/save",
        data={
            "shop_name": f"校准公司-{uuid4().hex[:6]}",
            "ui_theme": "light",
            "ui_scale": "100",
            "print_order_phone": "1",
            "print_order_address": "2",
            "print_main_business": "3",
            "print_legal_note": "4",
            "print_maker_name": "5",
            "print_receiver_label": "收货人：____",
            "print_offset_x_mm": "8",
            "print_offset_y_mm": "-10",
            "print_scale": "1.0",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    cfg = load_config()
    assert float(cfg["print_offset_x_mm"]) == 8.0
    assert float(cfg["print_offset_y_mm"]) == -10.0
    assert float(cfg["print_scale"]) == 1.0


def test_print_preview_uses_config_and_does_not_create_orders():
    init_db()
    app = create_app()
    client = app.test_client()
    save_config(
        {
            "shop_name": "预览抬头公司",
            "print_order_phone": "预览电话-XYZ",
            "print_maker_name": "预览制单人",
            "print_receiver_label": "收货人：预览线",
        }
    )
    with get_db() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]

    resp = client.get("/settings/print-preview")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "预览抬头公司" in html
    assert "预览电话-XYZ" in html
    assert "制单人：预览制单人" in html
    assert "收货人：预览线" in html
    assert "-webkit-text-stroke" not in html
    assert "销售清单" in html
    assert "MD202607120001" in html

    with get_db() as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]
    assert after == before


def test_migrate_data_root_copies_db_and_refuses_overwrite(tmp_path):
    init_db()
    suffix = uuid4().hex[:8]
    customer_id = create_customer(f"迁移客户-{suffix}")
    create_order_from_typed_rows(
        customer_id,
        f"MIG-{suffix}-001",
        [{"product_name": "阀", "unit": "只", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
    )
    old_root = data_root()
    old_db = old_root / "data" / "erp.db"
    assert old_db.exists()

    new_root = tmp_path / "new-data-root"
    migrate_data_root(new_root)
    assert data_root() == new_root.resolve()
    assert (new_root / "data" / "erp.db").exists()
    assert (new_root / "data" / "erp.db").stat().st_size > 0
    # Old root kept
    assert old_db.exists()

    # Refuse overwrite when destination already has a non-empty db
    other = tmp_path / "occupied"
    (other / "data").mkdir(parents=True)
    (other / "data" / "erp.db").write_bytes(b"not-empty-db")
    try:
        migrate_data_root(other)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "拒绝覆盖" in str(exc)
