from pathlib import Path
from uuid import uuid4

from erp import create_app
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
    assert "打印位置校准" in html
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


def test_settings_page_marks_desktop_copy_when_env_set(monkeypatch):
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    app = create_app()
    client = app.test_client()
    html = client.get("/settings/").get_data(as_text=True)
    assert "打开系统选夹" in html or "浏览文件夹" in html
    assert "浏览器开发预览请直接粘贴" not in html


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
    assert fake.title == "消防ERP"


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
