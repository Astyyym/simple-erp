"""1-C 全局搜索：分组命中、回收站排除、作废单标注、空查询不报错。"""
from __future__ import annotations

from uuid import uuid4

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _client():
    init_db()
    return create_app().test_client()


def test_empty_query_renders_without_error():
    client = _client()
    response = client.get("/search/")
    assert response.status_code == 200
    assert "全局搜索" in response.get_data(as_text=True)


def test_search_by_order_number_and_customer_and_product():
    init_db()
    suffix = uuid4().hex[:6]
    customer_name = f"搜索客户{suffix}"
    customer_id = create_customer(customer_name, phone="13900000000")
    order_no = f"MD20260101{suffix[:4]}"
    create_order_from_typed_rows(
        customer_id,
        order_no,
        [{"product_name": f"搜索商品{suffix}", "unit": "个", "unit_price_yuan": "10.00", "quantity": "1"}],
        status="saved",
        order_date="2026-01-01",
        order_type="sale",
    )
    client = create_app().test_client()

    by_no = client.get(f"/search/?q={order_no}")
    html = by_no.get_data(as_text=True)
    assert order_no in html

    by_customer = client.get(f"/search/?q={customer_name}")
    assert customer_name in by_customer.get_data(as_text=True)

    by_product = client.get(f"/search/?q=搜索商品{suffix}")
    assert f"搜索商品{suffix}" in by_product.get_data(as_text=True)


def test_search_matches_customer_pinyin_initials():
    init_db()
    create_customer("张小明", phone="13800000000")
    client = create_app().test_client()
    response = client.get("/search/?q=zxm")
    assert "张小明" in response.get_data(as_text=True)


def test_search_excludes_recycle_bin_and_marks_void():
    init_db()
    deleted_customer = create_customer("已删客户ABC")
    with get_db() as conn:
        conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (deleted_customer,))

    live_customer = create_customer("作废客户XYZ")
    create_order_from_typed_rows(
        live_customer,
        "MD202601029999",
        [{"product_name": "作废品", "unit": "个", "unit_price_yuan": "5.00", "quantity": "1"}],
        status="saved",
        order_date="2026-01-02",
        order_type="sale",
    )
    with get_db() as conn:
        conn.execute("UPDATE orders SET status='void' WHERE order_no='MD202601029999'")

    client = create_app().test_client()

    # 回收站里的客户搜不到（结果里不出现客户分组；输入框回显关键词不算命中）
    deleted_html = client.get("/search/?q=已删客户ABC").get_data(as_text=True)
    assert 'id="searchGroup-customers"' not in deleted_html

    # 作废单据仍可搜到，并标注「已作废」
    void_html = client.get("/search/?q=MD202601029999").get_data(as_text=True)
    assert "MD202601029999" in void_html
    assert "已作废" in void_html
