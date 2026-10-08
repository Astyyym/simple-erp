"""E6：作废单打印件「已作废」水印。

作废销售单目前仍可打印（有意的不对称：拿货单已禁打）。本轮给销售/退货侧加
水印，让打印件与系统状态一致。

验证方式（本轮限定）：模板渲染层断言 + public_order 放行断言。
- 真实 PDF 文本层提取依赖外部 pdftotext，本轮不依赖它；
- 实打（目标打印机）不在本轮范围。
"""
from erp import create_app
from erp.db import init_db
from erp.utils import pdf as pdf_module
from erp.utils.pdf import generate_order_pdf
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _render(order) -> str:
    from flask import render_template

    app = create_app()
    with app.app_context():
        return render_template(
            "orders/print_template.html",
            order=order,
            items=[],
            config=app.config["ERP_CONFIG"],
            cents_to_yuan=lambda c: str(c),
        )


def test_void_order_print_template_renders_watermark():
    html = _render(
        {
            "order_no": "MD202610010001",
            "order_date": "2026-10-01",
            "order_type": "sale",
            "customer_name": "水印客户",
            "notes": "",
            "total_amount_cents": 1000,
            "status": "void",
        }
    )
    assert "已作废" in html
    assert 'class="watermark"' in html


def test_saved_order_print_template_has_no_watermark():
    html = _render(
        {
            "order_no": "MD202610010002",
            "order_date": "2026-10-01",
            "order_type": "sale",
            "customer_name": "正常客户",
            "notes": "",
            "total_amount_cents": 1000,
            "status": "saved",
        }
    )
    assert "已作废" not in html


def test_settings_sample_context_without_status_does_not_crash():
    """设置页样张上下文只有 5 个键、无 status → 模板必须用 is defined 守卫。"""
    html = _render(
        {
            "order_no": "SAMPLE-001",
            "order_date": "2026-10-01",
            "order_type": "sale",
            "customer_name": "样张客户",
            "notes": "",
            "total_amount_cents": 5000,
        }
    )
    assert "已作废" not in html


def test_public_order_exposes_status_for_void_order():
    init_db()
    customer_id = create_customer("水印链路客户")
    order_id = create_order_from_typed_rows(
        customer_id, "MD202610010101",
        [{"product_name": "水印商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-01",
    )
    # 生成一次确认 status 已在 public_order 中放行（否则水印永远渲染不出）。
    app = create_app()
    with app.app_context():
        captured = {}
        original = pdf_module.render_template

        def spy(name, **context):
            captured.update(context)
            return original(name, **context)

        pdf_module.render_template = spy
        try:
            generate_order_pdf(order_id)
        finally:
            pdf_module.render_template = original
    assert captured["order"]["status"] == "saved"
