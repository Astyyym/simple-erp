"""拿货/退拿货开单页统一（第二轮）：对象自由输入、八列明细、单号/状态/当天历史、成功面板、一键带价。"""
import re
from datetime import date

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product
from erp.services.inventory import create_purchase_order, initialize_product


def _seed():
    init_db()
    cid = create_customer("统一页虚构对象")
    pid = create_product("统一页商品", "U1", "个", 2000)
    initialize_product(pid, "10", "10", "2026-10-02", "期初", "unified-init")
    return cid, pid


def test_purchase_pages_use_free_text_object_combobox():
    init_db()
    client = create_app().test_client()
    for path in ("/purchases/new", "/purchases/return/new"):
        html = client.get(path).get_data(as_text=True)
        assert 'id="customer_name" name="customer_name" role="combobox"' in html
        assert 'id="customer_id"' in html
        assert "fetchJson('/purchases/api/objects?q='" in html
        # 旧的对象下拉选择器已移除
        assert 'id="purchaseCustomer"' not in html
        assert 'id="returnParty"' not in html


def test_object_autocomplete_api_lists_matching_customers():
    init_db()
    create_customer("对象补全甲")
    client = create_app().test_client()
    response = client.get("/purchases/api/objects?q=补全")
    assert response.is_json
    names = [row["name"] for row in response.get_json()]
    assert "对象补全甲" in names


def test_purchase_create_autocreates_new_object_with_real_product():
    _seed()
    with get_db() as conn:
        pid = conn.execute("SELECT id FROM products WHERE name='统一页商品'").fetchone()["id"]
    client = create_app().test_client()
    response = client.post(
        "/purchases/create",
        data={
            "customer_name": "新自由输入对象",
            "business_date": "2026-10-02",
            "product_id": [str(pid)], "quantity": ["1"], "unit_cost_yuan": ["10"],
            "request_key": "free-object-2", "status": "saved",
        },
    )
    assert response.status_code == 302
    with get_db() as conn:
        created = conn.execute("SELECT id FROM customers WHERE name='新自由输入对象'").fetchone()
        assert created is not None
        order = conn.execute("SELECT customer_id FROM purchase_orders WHERE request_key='free-object-2'").fetchone()
    assert order["customer_id"] == created["id"]


def test_purchase_pages_have_unified_columns_and_readonly_cost():
    init_db()
    client = create_app().test_client()
    headers = ("序号", "产品名称", "单位", "数量", "单价(元)", "金额", "成本(元)", "操作")
    for path in ("/purchases/new", "/purchases/return/new"):
        html = client.get(path).get_data(as_text=True)
        for header in headers:
            assert header in html, (path, header)
        assert 'class="readonly-total unit-cost-display"' in html
        assert 'class="readonly-total line-total"' in html
        assert not re.search(r'<input\b[^>]*name="(?:unit_cost_micro|avg_cost|cost_total)', html)


def test_entry_pages_have_order_no_status_and_today_history():
    init_db()
    client = create_app().test_client()
    for path in ("/purchases/new", "/purchases/return/new"):
        html = client.get(path).get_data(as_text=True)
        assert 'id="orderNoInput"' in html
        assert 'name="status"' in html
        assert "当天历史开单" in html
        assert 'id="saveSuccessPanel"' in html
        assert "打开打印预览" in html


def test_next_order_no_preview_for_purchase_and_return():
    init_db()
    client = create_app().test_client()
    purchase = client.get("/purchases/api/next_order_no?kind=purchase&date=2026-10-02").get_json()
    assert purchase["order_no"] == "NH202610020001"
    ret = client.get("/purchases/api/next_order_no?kind=return&date=2026-10-02").get_json()
    # 拿货/退拿货共用同一天 NH 流水：无拿货单时退拿货也是 0001
    assert ret["order_no"] == "NH202610020001"


def test_purchase_today_history_api_and_section_list_saved_order():
    cid, pid = _seed()
    today = date.today().isoformat()
    order_no = "NH" + today.replace("-", "") + "0001"
    create_purchase_order(
        order_no, today, [{"product_id": pid, "quantity": "1", "unit_cost_yuan": "10"}],
        request_key="today-history-1", customer_id=cid,
    )
    client = create_app().test_client()
    data = client.get("/purchases/api/today_history").get_json()
    assert any(item["order_no"] == order_no for item in data["orders"])
    html = client.get("/purchases/new").get_data(as_text=True)
    assert order_no in html


def test_purchase_fetch_save_print_returns_json():
    cid, pid = _seed()
    client = create_app().test_client()
    response = client.post(
        "/purchases/create",
        data={
            "customer_id": str(cid), "customer_name": "统一页虚构对象",
            "business_date": "2026-10-02",
            "product_id": [str(pid)], "quantity": ["1"], "unit_cost_yuan": ["10"],
            "request_key": "fetch-print-purchase", "status": "saved", "save_action": "save_print",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200 and response.is_json
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["order_no"].startswith("NH20261002")
    assert payload["pdf_url"].endswith("/pdf")
    assert payload["detail_url"].startswith("/purchases/")
    assert payload["next_url"] == "/purchases/new"


def test_return_fetch_save_print_returns_json():
    cid, pid = _seed()
    client = create_app().test_client()
    response = client.post(
        "/purchases/return/create",
        data={
            "customer_id": str(cid), "business_date": "2026-10-02",
            "product_id": [str(pid)], "quantity": ["1"], "unit_price_yuan": ["12"],
            "request_key": "fetch-print-return", "status": "saved", "save_action": "save_print",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200 and response.is_json
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["order_no"].startswith("NH20261002")
    assert payload["next_url"] == "/purchases/return/new"


def test_entry_pages_expose_regular_and_customer_price_quick_fill():
    init_db()
    client = create_app().test_client()
    for path in ("/purchases/new", "/purchases/return/new"):
        html = client.get(path).get_data(as_text=True)
        assert "price-quick" in html
        assert "/orders/api/products" in html
        assert "常规价" in html and "客户价" in html


def test_purchase_fetch_save_error_returns_json_message_not_html_page():
    _seed()
    client = create_app().test_client()
    response = client.post(
        "/purchases/create",
        data={
            "customer_id": "1", "business_date": "2026-10-02",
            "product_id": [""], "quantity": ["1"], "unit_cost_yuan": ["10"],
            "request_key": "fetch-error-purchase", "status": "saved", "save_action": "save_print",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 400 and response.is_json
    payload = response.get_json()
    assert payload["ok"] is False and payload["message"]


def test_return_fetch_save_error_returns_json_message():
    _seed()
    client = create_app().test_client()
    response = client.post(
        "/purchases/return/create",
        data={
            "customer_id": "1", "business_date": "2026-10-02",
            "product_id": [""], "quantity": ["1"], "unit_price_yuan": ["10"],
            "request_key": "fetch-error-return", "status": "saved", "save_action": "save_print",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 400 and response.is_json
    assert response.get_json()["ok"] is False


def test_purchase_and_return_share_one_daily_nh_sequence():
    """拿货/退拿货共用同一天 NH 流水，和「销售+退货共用 MD」同一套规则。"""
    cid, pid = _seed()
    create_purchase_order(
        "NH202610050001", "2026-10-05",
        [{"product_id": pid, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=cid, request_key="shared-nh-1",
    )
    client = create_app().test_client()
    purchase = client.get("/purchases/api/next_order_no?kind=purchase&date=2026-10-05").get_json()
    ret = client.get("/purchases/api/next_order_no?kind=return&date=2026-10-05").get_json()
    assert purchase["order_no"] == "NH202610050002"
    assert ret["order_no"] == "NH202610050002"  # 同一号池，不各走一套流水
    response = client.post(
        "/purchases/return/create",
        data={
            "customer_id": str(cid), "business_date": "2026-10-05",
            "product_id": [str(pid)], "quantity": ["1"], "unit_price_yuan": ["12"],
            "request_key": "shared-nh-return", "status": "saved",
        },
    )
    assert response.status_code == 302
    with get_db() as conn:
        order_no = conn.execute(
            "SELECT order_no FROM purchase_return_orders ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        reserved = conn.execute(
            "SELECT last_sequence FROM purchase_number_sequences WHERE business_date='2026-10-05'"
        ).fetchone()[0]
    assert order_no == "NH202610050002"
    assert reserved == 2


def test_legacy_tn_number_is_never_handed_out_again_after_prefix_change():
    """历史 TN 单号不重写，但新 NH 取号必须先让过同一天已用掉的 TN 流水。"""
    cid, pid = _seed()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO purchase_return_orders(customer_id,customer_name,order_no,business_date,"
            "total_amount_cents,status,request_key,creation_payload_hash) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, "历史对象", "TN202610070003", "2026-10-07", 100, "saved", "legacy-tn", "h"),
        )
    html = create_app().test_client().get("/purchases/return/new").get_data(as_text=True)
    assert 'name="order_no"' in html
    assert "系统按 NH+所选日期+流水 自动生成" in html
    assert "系统按 TN+所选日期+流水 自动生成" not in html
    client = create_app().test_client()
    payload = client.get("/purchases/api/next_order_no?kind=return&date=2026-10-07").get_json()
    assert payload["order_no"] == "NH202610070004"
