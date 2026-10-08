from pathlib import Path

import pytest

from erp import create_app
from erp import utils
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from tests.pdf_test_utils import assert_a4_portrait_pdf


def _saved_order(order_type="sale"):
    init_db()
    customer_id = create_customer("Phase4客户")
    product_id = create_product("Phase4商品", "S-1", "个", 2000)
    order_id = create_order_from_typed_rows(
        customer_id,
        f"P4-{'RETURN' if order_type == 'return' else 'SALE'}",
        [{
            "product_id": product_id,
            "product_name": "Phase4商品",
            "unit": "个",
            "unit_price_yuan": "20",
            "quantity": "1",
        }],
        status="saved",
        order_type=order_type,
        notes="客户备注",
    )
    return order_id


@pytest.mark.parametrize("order_type, public_title", [("sale", "销售清单"), ("return", "退货清单")])
def test_order_pdf_uses_public_allowlist_and_a4_portrait(monkeypatch, order_type, public_title, tmp_path):
    order_id = _saved_order(order_type)
    captured = {}

    class CapturingHtml:
        def __init__(self, *, string, base_url):
            captured["html"] = string

        def write_pdf(self, output_path):
            Path(output_path).write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(utils.pdf, "HTML", CapturingHtml)
    with create_app().app_context():
        path = utils.pdf.generate_order_pdf(order_id)

    assert path.read_bytes().startswith(b"%PDF")
    html = captured["html"]
    assert public_title in html
    assert "内部成本哨兵" not in html
    assert "成本价" not in html
    assert "利润" not in html
    assert "库存" not in html
    assert "货源" not in html
    assert "Phase4客户" in html
    assert "Phase4商品" in html
    assert "客户备注" in html


def test_order_pdf_context_does_not_pass_internal_columns_to_template(monkeypatch):
    order_id = _saved_order()
    captured = {}

    def capture_template(template_name, **context):
        captured["template_name"] = template_name
        captured["order"] = context["order"]
        captured["items"] = context["items"]
        return "public html"

    monkeypatch.setattr(utils.pdf, "render_template", capture_template)
    monkeypatch.setattr(utils.pdf, "HTML", lambda *, string, base_url: type("Html", (), {"write_pdf": lambda self, path: Path(path).write_bytes(b"%PDF")})())
    with create_app().app_context():
        utils.pdf.generate_order_pdf(order_id)

    assert set(dict(captured["order"])) <= {
        "id", "order_no", "order_date", "order_type", "customer_name", "notes", "total_amount_cents",
        # E6：作废单打印件需要 status 渲染「已作废」水印；status 是面向用户的单据状态，非内部成本列。
        "status",
    }
    assert all(set(dict(item)) <= {
        "product_name", "spec", "unit", "quantity", "unit_price_cents", "subtotal_cents"
    } for item in captured["items"])


def test_purchase_pdf_contract_exposes_business_price_not_internal_cost(tmp_path):
    from tests.test_purchase_printing import seed, pdf_text, every_page_box

    _, _, purchase_id, return_id, _ = seed()
    client = create_app().test_client()
    for path in (f'/purchases/{purchase_id}/pdf', f'/purchases/return/{return_id}/pdf'):
        response = client.get(path)
        assert response.status_code == 200 and response.mimetype == 'application/pdf'
        every_page_box(response.data)
        text = pdf_text(response.data, tmp_path)
        assert '12.34' in text  # corresponding seller sees this transaction's business price
        for private in ('INTERNAL_', '成本价', '移动成本', '利润', '库存', '货源', '收货人', '本销售单'):
            assert private not in text


def test_settings_and_customer_summary_templates_have_no_internal_field_names():
    from erp.utils.pdf import settings_print_preview_context

    order, items, _ = settings_print_preview_context()
    assert set(order) <= {"order_no", "order_date", "customer_name", "total_amount_cents", "notes"}
    assert all(set(item) <= {"product_name", "spec", "unit", "quantity", "unit_price_cents", "subtotal_cents"} for item in items)


def test_real_order_pdf_is_a4_portrait(tmp_path):
    order_id = _saved_order()
    response = create_app().test_client().get(f"/orders/{order_id}/pdf")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert_a4_portrait_pdf(response.data)