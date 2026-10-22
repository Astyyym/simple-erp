"""JSON 接口 `/m/api/*` 的契约测试。

这一层存在的意义是「原生客户端不算口径」，所以测试的重点不是「字段齐不齐」，
而是三条硬约束在 JSON 上同样成立：

1. **蓝图外仍然 404**（第二监听只挂 mobile_bp + mobile_api_bp）。
2. **只 GET**，唯一例外是 `POST /m/api/pair`，且它不写 `erp.db`。
3. **金额与口径由服务端给**：`*_text` 字段必须存在且与 `*_cents` 对得上，
   安卓端不需要自己格式化，也就没法自己算错。
"""
from __future__ import annotations

import pytest

from erp.db import db_path, init_db
from erp.mobile_app import create_mobile_app
from erp.services import mobile_pairing, readonly_queries
from mobile_fixtures import pair_client, seed_all


@pytest.fixture()
def seeded():
    return seed_all()


@pytest.fixture()
def app():
    init_db()
    return create_mobile_app()


@pytest.fixture()
def paired_client(app):
    client = app.test_client()
    pair_client(client)
    return client


# ---------------------------------------------------------------------------
# 约束 1：蓝图外 404（JSON 接口不得把桌面路由带出来）
# ---------------------------------------------------------------------------

def test_api_outside_blueprint_still_404(app):
    client = app.test_client()
    for path in ("/customers/", "/orders/", "/api/", "/m/api/", "/m/nope"):
        assert client.get(path).status_code == 404, path


# ---------------------------------------------------------------------------
# 约束 2：只 GET，配对是唯一例外且不写库
# ---------------------------------------------------------------------------

def test_api_rejects_non_get(app, seeded):
    client = app.test_client()
    for path in ("/m/api/customers", "/m/api/today", "/m/api/orders"):
        assert client.post(path).status_code == 405, path
        assert client.put(path).status_code == 405, path
        assert client.delete(path).status_code == 405, path


def test_api_pair_post_is_the_only_exception_and_does_not_touch_db(app, seeded):
    client = app.test_client()
    mobile_pairing.generate_pair_code()
    before = db_path().read_bytes()
    response = client.post("/m/api/pair", json={"code": "000000"})
    assert response.status_code == 400
    assert response.get_json()["ok"] is False
    assert db_path().read_bytes() == before
    assert mobile_pairing.store_path().exists()


def test_api_pair_success_sets_cookie_and_returns_json(app, seeded):
    client = app.test_client()
    code, error = mobile_pairing.generate_pair_code()
    assert error == ""
    response = client.post("/m/api/pair", json={"code": code})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True and body["paired"] is True
    assert mobile_pairing.COOKIE_NAME in response.headers.get("Set-Cookie", "")
    # 配对后立刻能取数据（cookie 已生效）
    assert client.get("/m/api/today").status_code == 200


# ---------------------------------------------------------------------------
# 约束 3：未配对 401 + needs_pairing（原生端据此切配对界面，不是 302）
# ---------------------------------------------------------------------------

def test_unpaired_api_returns_401_with_flag_not_redirect(app):
    client = app.test_client()
    response = client.get("/m/api/customers")
    assert response.status_code == 401
    body = response.get_json()
    assert body["ok"] is False
    assert body["needs_pairing"] is True


def test_pair_status_is_reachable_without_cookie(app):
    client = app.test_client()
    response = client.get("/m/api/pair/status")
    assert response.status_code == 200
    assert response.get_json()["paired"] is False


# ---------------------------------------------------------------------------
# 数据契约：金额文本由服务端给，且与 cents 对得上
# ---------------------------------------------------------------------------

def test_today_api_matches_query_layer(paired_client, seeded):
    from erp.utils.mobile_format import money_short

    body = paired_client.get("/m/api/today").get_json()
    assert body["ok"] is True
    today = body["today"]
    expected = readonly_queries.today_overview()
    assert today["document_count"] == expected["document_count"]
    assert [entry["type"] for entry in today["types"]] == [
        "sale", "return", "purchase", "purchase_return"
    ]
    # 服务端格式化：文本必须与 cents 一致，安卓端不用再算
    for entry in today["types"]:
        assert entry["amount_text"] == money_short(entry["amount_cents"])
    assert today["net_sale_text"] == money_short(today["net_sale_cents"])
    assert today["net_purchase_text"] == money_short(today["net_purchase_cents"])


def test_customers_api_balance_text_matches_cents(paired_client, seeded):
    from erp.utils.mobile_format import money_short

    body = paired_client.get("/m/api/customers").get_json()
    assert body["ok"] is True
    assert body["customers"]
    for customer in body["customers"]:
        assert customer["balance_text"] == money_short(customer["balance_cents"])


def test_customer_detail_api_has_entries_documents_prices(paired_client, seeded):
    body = paired_client.get(f"/m/api/customers/{seeded['customer_id']}").get_json()
    assert body["ok"] is True
    customer = body["customer"]
    assert customer["id"] == seeded["customer_id"]
    assert customer["current_balance_text"].startswith(("¥", "-¥"))
    assert isinstance(body["entries"], list) and body["entries"]
    assert isinstance(body["documents"], list) and body["documents"]
    for document in body["documents"]:
        assert document["type_label"]
        assert document["amount_text"]


def test_customer_detail_api_404_for_missing(paired_client, seeded):
    response = paired_client.get("/m/api/customers/999999")
    assert response.status_code == 404
    assert response.get_json()["ok"] is False


def test_orders_api_date_filter_and_type_filter(paired_client, seeded):
    day = seeded["day"]
    body = paired_client.get(
        f"/m/api/orders?date_mode=day&start_date={day}&end_date={day}"
    ).get_json()
    assert body["ok"] is True
    assert len(body["documents"]) == 4
    assert {doc["order_type"] for doc in body["documents"]} == {
        "sale", "return", "purchase", "purchase_return"
    }

    only_sale = paired_client.get(
        f"/m/api/orders?date_mode=day&start_date={day}&end_date={day}&order_type=sale"
    ).get_json()
    assert {doc["order_type"] for doc in only_sale["documents"]} == {"sale"}

    # 全部时间不能比当天少
    everything = paired_client.get("/m/api/orders?date_mode=all").get_json()
    assert len(everything["documents"]) >= len(body["documents"])


def test_order_detail_api_has_items_with_text(paired_client, seeded):
    listing = paired_client.get("/m/api/orders?date_mode=all").get_json()
    target = listing["documents"][0]
    body = paired_client.get(
        f"/m/api/orders/{target['order_type']}/{target['id']}"
    ).get_json()
    assert body["ok"] is True
    document = body["document"]
    assert document["order_no"] == target["order_no"]
    assert document["total_text"].startswith(("¥", "-¥"))
    assert document["items"]
    for item in document["items"]:
        assert item["subtotal_text"].startswith(("¥", "-¥"))
        assert item["quantity_text"]


def test_order_detail_api_rejects_bad_type(paired_client, seeded):
    assert paired_client.get("/m/api/orders/bogus/1").status_code == 400


def test_products_api_and_detail(paired_client, seeded):
    listing = paired_client.get("/m/api/products").get_json()
    assert listing["ok"] is True
    assert listing["products"]
    product = listing["products"][0]
    detail = paired_client.get(f"/m/api/products/{product['id']}").get_json()
    assert detail["ok"] is True
    assert detail["product"]["id"] == product["id"]
    for price in detail["prices"]:
        assert price["price_text"]


def test_meta_api_reports_shop_and_types(paired_client, seeded):
    body = paired_client.get("/m/api/meta").get_json()
    assert body["ok"] is True
    assert body["schema"] == 1
    assert [item["type"] for item in body["order_types"]] == [
        "sale", "return", "purchase", "purchase_return"
    ]
    assert body["paired"] is True
    assert body["shop_name"]


# ---------------------------------------------------------------------------
# 只读：API 上同样没有任何写入口
# ---------------------------------------------------------------------------

def test_api_has_no_write_routes_beyond_pair(app):
    """把所有 `/m/api` 路由列出来，除配对提交外必须全是 GET。"""
    write_routes = set()
    for rule in app.url_map.iter_rules():
        if not str(rule.rule).startswith("/m/api"):
            continue
        methods = set(rule.methods) - {"HEAD", "OPTIONS"}
        if methods - {"GET"}:
            write_routes.add((str(rule.rule), tuple(sorted(methods))))
    assert write_routes == {("/m/api/pair", ("POST",))}
