from uuid import uuid4

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _seed_sale_and_return():
    init_db()
    suffix = uuid4().hex[:8]
    customer_name = f"类型筛选客户{suffix}"
    customer_id = create_customer(customer_name)
    create_order_from_typed_rows(
        customer_id,
        f"SALE-TYPE-{suffix}",
        [{"product_name": "销售商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
        order_date="2026-07-08",
        order_type="sale",
    )
    create_order_from_typed_rows(
        customer_id,
        f"RETURN-TYPE-{suffix}",
        [{"product_name": "退货商品", "unit": "个", "unit_price_yuan": "5", "quantity": "1"}],
        status="saved",
        order_date="2026-07-08",
        order_type="return",
    )
    return customer_name, suffix


def test_order_type_filter_supports_all_sale_and_return_while_preserving_conditions():
    customer_name, suffix = _seed_sale_and_return()
    client = create_app().test_client()

    all_html = client.get(
        "/orders/",
        query_string={"customer": customer_name, "start_date": "2026-07-08", "end_date": "2026-07-08"},
    ).get_data(as_text=True)
    sale_html = client.get(
        "/orders/",
        query_string={"customer": customer_name, "start_date": "2026-07-08", "end_date": "2026-07-08", "order_type": "sale"},
    ).get_data(as_text=True)
    return_html = client.get(
        "/orders/",
        query_string={"customer": customer_name, "start_date": "2026-07-08", "end_date": "2026-07-08", "order_type": "return"},
    ).get_data(as_text=True)

    assert f"SALE-TYPE-{suffix}" in all_html and f"RETURN-TYPE-{suffix}" in all_html
    assert f"SALE-TYPE-{suffix}" in sale_html and f"RETURN-TYPE-{suffix}" not in sale_html
    assert f"RETURN-TYPE-{suffix}" in return_html and f"SALE-TYPE-{suffix}" not in return_html
    # 订单类型筛选改为可多选（复选框 + 清空）；不勾选=全部，故无「全选」按钮。
    assert 'name="order_type"' in all_html
    assert "订单类型（可多选）" in all_html
    assert 'id="orderTypesale"' in all_html and 'id="orderTypereturn"' in all_html
    assert 'data-type-none' in all_html and 'data-type-all' not in all_html
    assert f'value="{customer_name}"' in sale_html
    assert 'value="2026-07-08"' in sale_html


def test_order_type_multi_select_keeps_heatmap_link_scope():
    customer_name, _ = _seed_sale_and_return()
    html = create_app().test_client().get(
        "/orders/",
        query_string={"customer": customer_name, "order_type": "return"},
    ).get_data(as_text=True)

    # 类型多选筛选在当前页保留（复选框勾选状态），且单据类型标签渲染在列表中。
    assert 'id="orderTypereturn"' in html and 'value="return"' in html
    assert 'name="order_type"' in html
    assert "退货单" in html


def test_order_type_multi_select_supports_partial_and_single_choice():
    customer_name, suffix = _seed_sale_and_return()
    client = create_app().test_client()
    base = {"customer": customer_name, "start_date": "2026-07-08", "end_date": "2026-07-08"}

    both = client.get("/orders/", query_string={**base, "order_type": ["sale", "return"]}).get_data(as_text=True)
    assert f"SALE-TYPE-{suffix}" in both and f"RETURN-TYPE-{suffix}" in both
    only_sale = client.get("/orders/", query_string={**base, "order_type": ["sale"]}).get_data(as_text=True)
    assert f"SALE-TYPE-{suffix}" in only_sale and f"RETURN-TYPE-{suffix}" not in only_sale
    # 不勾选任何类型 = 全部
    none_checked = client.get("/orders/", query_string=base).get_data(as_text=True)
    assert f"SALE-TYPE-{suffix}" in none_checked and f"RETURN-TYPE-{suffix}" in none_checked


def test_invalid_order_type_falls_back_to_all_orders():
    customer_name, suffix = _seed_sale_and_return()
    html = create_app().test_client().get(
        "/orders/", query_string={"customer": customer_name, "order_type": "unexpected"}
    ).get_data(as_text=True)

    assert f"SALE-TYPE-{suffix}" in html and f"RETURN-TYPE-{suffix}" in html
