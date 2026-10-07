import re

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_product, update_product_record
from erp.services.inventory import initialize_product, post_inventory_event


def _product(name="库存展示商品", unit="个"):
    init_db()
    return create_product(name, "S1", unit, 2000)


def test_unit_is_locked_after_initialization_but_safety_stock_can_be_updated():
    product_id = _product()
    initialize_product(product_id, "10", "10", "2026-10-01", "期初", "unit-lock-001")

    with pytest.raises(ValueError, match="单位已锁定"):
        update_product_record(product_id, "库存展示商品", "S1", "箱", 2000, safety_stock="2")

    update_product_record(product_id, "库存展示商品", "S1", "个", 2000, safety_stock="2.500")
    with get_db() as conn:
        product = conn.execute("SELECT unit, safety_stock_3dp FROM products WHERE id=?", (product_id,)).fetchone()
    assert product["unit"] == "个"
    assert product["safety_stock_3dp"] == 2500


def test_product_pages_display_current_inventory_cost_amount_and_safety_stock():
    product_id = _product("页面库存商品")
    initialize_product(product_id, "10", "10", "2026-10-01", "期初", "display-001")
    post_inventory_event(product_id, "5", "14", "purchase", "display-purchase-001", "2026-10-02")
    update_product_record(product_id, "页面库存商品", "S1", "个", 2000, safety_stock="3")

    client = create_app().test_client()
    list_html = client.get("/products/").get_data(as_text=True)
    edit_html = client.get(f"/products/{product_id}/edit").get_data(as_text=True)

    for html in (list_html, edit_html):
        assert "当前库存" in html
        assert "移动平均成本" in html
        assert "库存金额" in html
        assert "安全库存" in html
        assert "15.000" not in html
        assert "11.333333" not in html
        assert re.search(r'<(?:td|dd)\b[^>]*>11\.33</(?:td|dd)>', html)
        assert re.search(r'<(?:td|dd)\b[^>]*>170\.00</(?:td|dd)>', html)
    assert '<td class="numeric-cell">15</td>' in list_html
    assert '<td class="numeric-cell">3</td>' in list_html
    assert '<dd class="fs-5 text-nowrap">15</dd>' in edit_html
    assert 'name="safety_stock" value="3"' in edit_html
    assert "单位已锁定" in edit_html or "单位" in edit_html


def test_unit_can_be_changed_before_inventory_initialization():
    product_id = _product("可改单位商品")
    update_product_record(product_id, "可改单位商品", "S1", "箱", 2100, safety_stock="1")

    with get_db() as conn:
        product = conn.execute("SELECT unit, default_price_cents FROM products WHERE id=?", (product_id,)).fetchone()
    assert tuple(product) == ("箱", 2100)


def test_safety_stock_rejects_negative_or_excess_precision():
    product_id = _product("安全库存校验商品")
    with pytest.raises(ValueError, match="安全库存不能为负数"):
        update_product_record(product_id, "安全库存校验商品", "S1", "个", 2000, safety_stock="-1")
    with pytest.raises(ValueError, match="安全库存最多保留3位小数"):
        update_product_record(product_id, "安全库存校验商品", "S1", "个", 2000, safety_stock="1.0001")
