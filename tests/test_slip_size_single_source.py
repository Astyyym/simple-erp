"""单联容量真源：一处配置（``config.slip_size``）必须同时影响打印与四类开单录入页。

P6（2026-10-14）起容量由模块常量改为**配置项**（设置页 6/8/10/12，默认 10）。
证据分层：这里是模板 / 上下文管线层；逐档行几何（一联装几行、折行余量）见
``tests/test_slip_geometry_tiers.py``；纸面位置仍须目标打印机实打验收。
"""
from pathlib import Path

import pytest

from erp import create_app
from erp.config import (
    SLIP_SIZE,
    SLIP_SIZE_CHOICES,
    normalize_slip_size,
    save_config,
    slip_row_padding_mm,
)
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import create_purchase_order, initialize_product

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "erp" / "templates"
ENTRY_PAGES = (
    "/orders/new",
    "/orders/return/new",
    "/purchases/new",
    "/purchases/return/new",
)


def test_no_template_hardcodes_the_slip_size_or_row_padding():
    """打印模板与三份录入页都读注入值；模板里不得有第二处常量（含写死的行内边距）。"""
    print_template = (TEMPLATES / "orders" / "print_template.html").read_text(encoding="utf-8")
    assert "{% set slip_size = 10 %}" not in print_template
    assert "items | batch(slip_size)" in print_template
    assert "padding: 0.9mm" not in print_template, "行内边距必须随档位下发，不能写死"
    assert "padding: {{slip_padding_mm}}mm 1.2mm;" in print_template

    for name in ("orders/new.html", "purchases/new.html", "purchases/return_new.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "const slipSize = {{ slip_size }};" in text, name


def test_invalid_tiers_fall_back_to_the_default():
    """档位越界/非法一律回落默认 10：配置被手改坏也不能让打印变形。"""
    assert SLIP_SIZE == 10
    assert SLIP_SIZE_CHOICES == (6, 8, 10, 12)
    for value in ("7", "0", "-3", "abc", "", None, "10.5"):
        assert normalize_slip_size(value) == SLIP_SIZE, value
    for value in SLIP_SIZE_CHOICES:
        assert normalize_slip_size(value) == value
        assert normalize_slip_size(str(value)) == value
    # 12 行必须比 10 行收得更紧（否则直接溢出成两联，见几何契约测试）
    assert slip_row_padding_mm(12) < slip_row_padding_mm(10)


@pytest.mark.parametrize("slip_size", SLIP_SIZE_CHOICES)
def test_config_slip_size_moves_entry_pages_and_settings_sample_together(slip_size):
    """四份录入页 + 设置页样张（打印渲染点之一，走 context processor）跟随同一配置。"""
    init_db()
    save_config({"slip_size": slip_size})
    client = create_app().test_client()

    for path in ENTRY_PAGES:
        html = client.get(path).get_data(as_text=True)
        assert f"const slipSize = {slip_size};" in html, path

    sample = client.get("/settings/print-preview").get_data(as_text=True)
    assert f"padding: {slip_row_padding_mm(slip_size)}mm 1.2mm" in sample, "样张没拿到该档位的行内边距"

    # 设置页要真的能看到并选中当前档位（用户 2026-10-14 反馈过「设置界面找不到」）
    page = client.get("/settings/").get_data(as_text=True)
    assert 'name="slip_size"' in page
    assert f'<option value="{slip_size}" selected>' in page
    for choice in SLIP_SIZE_CHOICES:
        assert f'<option value="{choice}"' in page


def _capture_pdf_html(monkeypatch, module, seam):
    """把某个打印渲染点的 WeasyPrint 换掉，截获它实际渲染的 HTML。"""
    captured = {}

    class Capture:
        def __init__(self, string=None, base_url=None):
            captured["html"] = string

        def write_pdf(self, out):
            Path(out).write_bytes(b"%PDF-1.4\n")

    if seam == "html_class":
        monkeypatch.setattr(module, "_html_class", lambda: Capture)
    else:
        monkeypatch.setattr(module, "_weasy", lambda: (None, Capture))
    return captured


def test_order_pdf_render_point_uses_the_configured_tier(monkeypatch):
    """销售单 PDF 渲染点（utils/pdf.py）必须拿到配置档位，而不是退回默认 10。"""
    from erp.utils import pdf as pdf_module

    init_db()
    save_config({"slip_size": 6})
    cid = create_customer("真源客户")
    pid = create_product("真源商品", "只", 10000)
    initialize_product(pid, "100", "10", "2026-10-01", "期初", "slip-init")
    order_id = create_order_from_typed_rows(
        cid, "MD202610140001",
        [{"product_id": pid, "product_name": "真源商品", "unit": "只",
          "quantity": "1", "unit_price_yuan": "100"}],
        status="saved", order_date="2026-10-14",
    )
    captured = _capture_pdf_html(monkeypatch, pdf_module, "html_class")
    app = create_app()
    with app.app_context():
        pdf_module.generate_order_pdf(order_id)

    html = captured["html"]
    assert "本张已满 6 行" not in html  # 只有 1 行，不该出现续打提示
    assert "padding: 0.9mm 1.2mm" in html
    assert "{{" not in html and "{%" not in html


def test_purchase_pdf_render_point_gets_the_slip_values_explicitly(monkeypatch):
    """拿货/退拿货 PDF 用 jinja_env 直渲、不过 context processor，必须显式传值。"""
    from erp.utils import purchase_pdf as purchase_pdf_module

    init_db()
    save_config({"slip_size": 6})
    cid = create_customer("真源往来对象")
    rows = []
    for index in range(1, 8):  # 7 行 > 6 行档位 → 必须出现续打提示
        pid = create_product(f"真源商品B{index}", "只", 10000)
        initialize_product(pid, "100", "10", "2026-10-01", "期初", f"slip-init-b{index}")
        rows.append({"product_id": pid, "quantity": "1", "unit_cost_yuan": "10"})
    order = create_purchase_order(
        "NH202610140001", "2026-10-14", rows,
        request_key="slip-purchase", customer_id=cid,
    )
    captured = _capture_pdf_html(monkeypatch, purchase_pdf_module, "weasy")
    app = create_app()
    with app.app_context():
        purchase_pdf_module.generate_purchase_pdf(order["order_id"])

    html = captured["html"]
    # 7 行 + 6 行档位 → 必须出现「本张已满 6 行」，否则说明拿货侧还在用默认 10。
    assert "本张已满 6 行" in html
    assert "padding: 0.9mm 1.2mm" in html
