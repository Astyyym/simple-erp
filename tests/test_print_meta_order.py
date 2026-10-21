"""打印单抬头的三格顺序（2026-10-21 用户要求）。

四类单据（销售/退货/拿货/退拿货）共用 `orders/print_template.html`，
用户要求：**客户贴左、日期居中、单号贴右**。此前是「单号 / 日期 / 客户」。

这个顺序不是纯样式问题 —— `.meta` 用 `table-cell` + `nth-child` 做三格对齐，
所以 DOM 顺序决定了视觉位置。测试直接断言渲染出来的格子文本顺序，
防止以后有人调 CSS 却没注意 DOM 还是旧顺序（或反之）。
"""
from __future__ import annotations

import re

import pytest

from erp import create_app
from erp.utils.money import cents_to_yuan


class _Cfg(dict):
    def __getattr__(self, key):  # Jinja 里用 config.shop_name 取值
        return self.get(key, "")


def _render(order: dict, *, object_role: str | None = None) -> str:
    app = create_app()
    with app.app_context():
        context = dict(
            config=_Cfg(
                shop_name="虚构门店", print_order_phone="0571", print_order_address="杭州",
                print_main_business="主营", print_legal_note="法律句",
                print_maker_name="", print_receiver_label="收货人：____",
            ),
            order=order,
            items=[{"product_name": "闸阀", "unit": "个", "quantity": 1,
                    "unit_price_cents": 1000, "subtotal_cents": 1000}],
            slip_size=10,
            cents_to_yuan=cents_to_yuan,
            format_quantity=str,
            format_quantity_3dp=str,
            document_title="清单",
        )
        if object_role is not None:
            context["object_role"] = object_role
        return app.jinja_env.get_template("orders/print_template.html").render(**context)


def _meta_cells(html: str) -> list[str]:
    block = re.search(r'<div class="meta">(.*?)</div>', html, re.S)
    assert block, "打印模板里必须有 .meta 抬头区块"
    return [cell.strip() for cell in re.findall(r"<span>(.*?)</span>", block.group(1), re.S)]


CASES = [
    ("销售单", {"order_no": "MD202610210001", "order_date": "2026-10-21",
              "customer_name": "客户甲", "order_type": "sale", "status": "saved",
              "total_amount_cents": 1000}, None, "客户"),
    ("退货单", {"order_no": "MD202610210002", "order_date": "2026-10-21",
              "customer_name": "客户乙", "order_type": "return", "status": "saved",
              "total_amount_cents": 1000}, None, "客户"),
    ("拿货单", {"order_no": "NH202610210001", "order_date": "2026-10-21",
              "customer_name": "供应商丙", "order_type": "purchase", "status": "saved",
              "total_amount_cents": 1000}, "往来对象（本单卖方）", "往来对象（本单卖方）"),
    ("退拿货单", {"order_no": "NH202610210002", "order_date": "2026-10-21",
               "customer_name": "供应商丁", "order_type": "purchase_return", "status": "saved",
               "total_amount_cents": 1000}, "往来对象（本单卖方）", "往来对象（本单卖方）"),
]


@pytest.mark.parametrize("label,order,object_role,customer_label", CASES)
def test_meta_order_is_customer_date_order_no(label, order, object_role, customer_label):
    cells = _meta_cells(_render(order, object_role=object_role))
    assert len(cells) == 3, f"{label}：抬头应有三格，实际 {cells}"

    # 1. 客户在左
    assert cells[0].startswith(customer_label + ":"), f"{label}：左格应是客户，实际 {cells[0]!r}"
    assert order["customer_name"] in cells[0]

    # 2. 日期居中
    assert cells[1].startswith("日期:"), f"{label}：中格应是日期，实际 {cells[1]!r}"
    assert order["order_date"] in cells[1]

    # 3. 单号在右
    assert cells[2].startswith("单号:"), f"{label}：右格应是单号，实际 {cells[2]!r}"
    assert order["order_no"] in cells[2]


def test_sale_and_return_use_plain_customer_label():
    """销售/退货不传 object_role，默认标签必须是「客户」而不是 None。"""
    for order in (CASES[0][1], CASES[1][1]):
        cells = _meta_cells(_render(order))
        assert cells[0].startswith("客户:"), f"默认标签应回落成「客户」，实际 {cells[0]!r}"
        assert "None" not in cells[0]
