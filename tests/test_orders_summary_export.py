from uuid import uuid4

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def test_orders_summary_export_single_and_multi_customer_and_ignores_type_filter():
    init_db()
    suffix = uuid4().hex[:8]
    a_name = f"汇总甲{suffix}"
    b_name = f"汇总乙{suffix}"
    a_id = create_customer(a_name)
    b_id = create_customer(b_name)
    create_order_from_typed_rows(
        a_id,
        f"SUM-A-SALE-{suffix}",
        [{"product_name": "甲商品", "unit": "个", "unit_price_yuan": "100", "quantity": "1"}],
        status="saved",
        order_date="2026-07-01",
        order_type="sale",
    )
    create_order_from_typed_rows(
        a_id,
        f"SUM-A-RET-{suffix}",
        [{"product_name": "甲退货", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_date="2026-07-02",
        order_type="return",
    )
    create_order_from_typed_rows(
        b_id,
        f"SUM-B-SALE-{suffix}",
        [{"product_name": "乙商品", "unit": "个", "unit_price_yuan": "50", "quantity": "1"}],
        status="saved",
        order_date="2026-07-03",
        order_type="sale",
    )
    client = create_app().test_client()

    page = client.get(
        "/orders/",
        query_string={"date_mode": "range", "start_date": "2026-07-01", "end_date": "2026-07-03"},
    ).get_data(as_text=True)
    assert "导出汇总表" in page
    assert "未锁定唯一客户" in page
    assert "单据管理" in page

    single = client.get(
        "/orders/summary_pdf",
        query_string={
            "customer": a_name,
            "date_mode": "range",
            "start_date": "2026-07-01",
            "end_date": "2026-07-03",
            "order_type": "sale",  # must be ignored for summary content
        },
    )
    assert single.status_code == 200
    assert single.mimetype == "application/pdf"
    assert single.data[:4] == b"%PDF"

    multi = client.get(
        "/orders/summary_pdf",
        query_string={"date_mode": "range", "start_date": "2026-07-01", "end_date": "2026-07-03"},
    )
    assert multi.status_code == 200
    assert multi.mimetype == "application/pdf"
    assert multi.data[:4] == b"%PDF"
    # Multi-customer PDF should be larger than single-customer PDF.
    assert len(multi.data) > len(single.data)
