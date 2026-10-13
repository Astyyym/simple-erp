"""单联容量真源：``config.SLIP_SIZE`` 一处改动必须同时影响打印与四类开单录入页。

证据分层：这里是模板/上下文管线层，不是打印几何层（打印页数与偏移另有测试）。
"""
from pathlib import Path

import erp
from erp import create_app
from erp.db import init_db
from erp.utils.money import cents_to_yuan

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "erp" / "templates"
ENTRY_PAGES = (
    "/orders/new",
    "/orders/return/new",
    "/purchases/new",
    "/purchases/return/new",
)


def test_no_template_hardcodes_the_slip_size():
    """四个渲染点共用的打印模板 + 三份录入页模板都读注入值，模板里不得有第二处常量。"""
    print_template = (TEMPLATES / "orders" / "print_template.html").read_text(encoding="utf-8")
    assert "{% set slip_size = 10 %}" not in print_template
    assert "items | batch(slip_size)" in print_template

    for name in ("orders/new.html", "purchases/new.html", "purchases/return_new.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "const slipSize = {{ slip_size }};" in text, name


def test_changing_the_constant_moves_print_and_entry_pages_together(monkeypatch):
    init_db()
    # create_app 从 erp.SLIP_SIZE 取该常量并注入 jinja 全局；改一处即改全部渲染点。
    monkeypatch.setattr(erp, "SLIP_SIZE", 5)
    app = create_app()

    client = app.test_client()
    for path in ENTRY_PAGES:
        html = client.get(path).get_data(as_text=True)
        assert "const slipSize = 5;" in html, path

    items = [
        {"product_name": f"闸阀{index:03d}", "unit": "只", "quantity": "1",
         "unit_price_cents": 10000, "subtotal_cents": 10000}
        for index in range(1, 13)
    ]
    order = {
        "order_no": "MD202610130001", "order_date": "2026-10-13", "order_type": "sale",
        "customer_name": "真源客户", "notes": "", "total_amount_cents": 120000, "status": "saved",
    }
    with app.app_context():
        from flask import render_template

        html = render_template(
            "orders/print_template.html",
            order=order,
            items=items,
            config=app.config["ERP_CONFIG"],
            cents_to_yuan=cents_to_yuan,
            document_title="销售清单",
        )

    assert "本张已满 5 行" in html
    assert "以下内容续打在第 2 张" in html
    assert "以下内容续打在第 3 张" in html
    assert "以下内容续打在第 4 张" not in html
