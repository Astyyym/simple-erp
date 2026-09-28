import json
import re
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit

from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, mark_order_printed, void_order
from erp import create_app


def test_orders_list_filters_by_customer_and_date_independently():
    init_db()
    customer_a = create_customer("筛选甲")
    customer_b = create_customer("筛选乙")
    create_order_from_typed_rows(customer_a, "O-A-OLD", [{"product_name": "A", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved", order_date="2026-07-01")
    create_order_from_typed_rows(customer_a, "O-A-NEW", [{"product_name": "A", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved", order_date="2026-07-10")
    create_order_from_typed_rows(customer_b, "O-B-NEW", [{"product_name": "B", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}], status="saved", order_date="2026-07-10")
    app = create_app()
    client = app.test_client()
    html = client.get("/orders/?customer=筛选甲&start_date=2026-07-05&end_date=2026-07-12").data.decode("utf-8")
    assert "O-A-NEW" in html
    assert "O-A-OLD" not in html
    assert "O-B-NEW" not in html


def test_orders_list_paginates_full_filtered_result_and_preserves_filter_scope():
    init_db()
    customer = create_customer("分页筛选甲")
    other_customer = create_customer("分页筛选乙")
    row = [{"product_name": "分页测试商品", "unit": "个", "unit_price_yuan": "1", "quantity": "1"}]
    for sequence in range(1, 202):
        create_order_from_typed_rows(
            customer,
            f"MD20260710{sequence:04d}",
            row,
            status="saved",
            order_date="2026-07-10",
        )
    create_order_from_typed_rows(
        other_customer,
        "MD202607109999",
        row,
        status="saved",
        order_date="2026-07-10",
    )

    client = create_app().test_client()
    filters = {
        "customer": "分页筛选甲",
        "date_mode": "range",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "order_type": "sale",
    }
    first_response = client.get("/orders/?" + urlencode(filters))
    first_html = first_response.data.decode("utf-8")

    assert first_response.status_code == 200
    assert "显示 <strong>1–50</strong> 条，共 <strong>201</strong> 条" in first_html
    assert "MD202607100201" in first_html
    next_link = re.search(r'<a[^>]*aria-label="下一页"[^>]*href="([^"]+)"', first_html)
    assert next_link is not None, "第一页应显示下一页链接"

    next_url = unescape(next_link.group(1))
    next_args = parse_qs(urlsplit(next_url).query)
    assert next_args["customer"] == [filters["customer"]]
    assert next_args["start_date"] == [filters["start_date"]]
    assert next_args["end_date"] == [filters["end_date"]]
    assert next_args["order_type"] == [filters["order_type"]]
    assert next_args["page"] == ["2"]

    second_response = client.get(next_url)
    second_html = second_response.data.decode("utf-8")
    assert second_response.status_code == 200
    assert "显示 <strong>51–100</strong> 条，共 <strong>201</strong> 条" in second_html

    last_args = {**filters, "page": 5}
    last_response = client.get("/orders/?" + urlencode(last_args))
    last_html = last_response.data.decode("utf-8")
    assert last_response.status_code == 200
    assert "显示 <strong>201–201</strong> 条，共 <strong>201</strong> 条" in last_html
    assert "MD202607100001" in last_html
    assert len(re.findall(r'<input form="bulkOrdersForm" type="checkbox" name="ids" value="\d+">', first_html)) == 50
    assert len(re.findall(r'<input form="bulkOrdersForm" type="checkbox" name="ids" value="\d+">', last_html)) == 1

    def purchase_statistics(html):
        match = re.search(
            r'<script id="customerPurchaseData" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        assert match is not None
        return json.loads(match.group(1))

    assert purchase_statistics(first_html) == purchase_statistics(second_html)
    assert purchase_statistics(first_html) == purchase_statistics(last_html)


def test_orders_list_shows_business_status_labels_and_document_types():
    init_db()
    customer = create_customer("状态展示客户")
    row = [{"product_name": "状态商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}]
    create_order_from_typed_rows(customer, "STATUS-DRAFT", row, status="draft", order_date="2026-07-10")
    create_order_from_typed_rows(customer, "STATUS-SAVED", row, status="saved", order_date="2026-07-10")
    printed_id = create_order_from_typed_rows(customer, "STATUS-PRINTED", row, status="saved", order_date="2026-07-10")
    mark_order_printed(printed_id)
    voided_id = create_order_from_typed_rows(customer, "STATUS-VOID", row, status="saved", order_date="2026-07-10")
    void_order(voided_id, "测试作废")
    create_order_from_typed_rows(
        customer,
        "STATUS-RETURN",
        row,
        status="saved",
        order_date="2026-07-10",
        order_type="return",
    )

    html = create_app().test_client().get(
        "/orders/?start_date=2026-07-10&end_date=2026-07-10"
    ).data.decode("utf-8")

    assert "草稿" in html
    assert "正式保存" in html
    assert "已打印" in html
    assert "已作废" in html
    assert "销售单" in html
    assert "退货单" in html
    assert ">draft<" not in html
    assert ">saved<" not in html
    assert ">printed<" not in html
    assert ">void<" not in html
    assert 'class="badge bg-secondary"' in html
    assert 'class="badge bg-success"' in html
    assert 'class="badge bg-info text-dark"' in html
    assert 'class="badge bg-danger"' in html
