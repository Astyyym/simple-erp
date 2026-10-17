"""手机端的「只读」保证：决策文档 §4 第 7 条要求的 6 条 pytest。

不写这 6 条，「只读」就只是代码里的几个 `if`：任何人一次重构都能破掉，
而且破掉之后不会有任何测试失败。
"""
from __future__ import annotations

import re
import sqlite3

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


def test_1_outside_blueprint_is_404_not_200_or_403(app):
    """独立 app 只挂 mobile_bp：桌面端路由在第二监听上必须天然 404。"""
    client = app.test_client()
    for path in ("/customers/", "/orders/", "/products/", "/", "/accounts/", "/settings/"):
        response = client.get(path)
        assert response.status_code == 404, path


def test_2_non_get_is_rejected_but_pair_post_is_the_only_exception(app, seeded):
    client = app.test_client()
    assert client.post("/m/customers").status_code == 405
    assert client.put("/m/orders").status_code == 405
    assert client.delete("/m/").status_code == 405

    # 配对提交是唯一例外：码不对时是 400（业务拒绝），**不是 405**
    mobile_pairing.generate_pair_code()
    before = db_path().read_bytes()
    response = client.post("/m/pair", data={"code": "000000"})
    assert response.status_code == 400
    # 它只写 mobile_devices.json，不写 erp.db
    assert db_path().read_bytes() == before
    assert mobile_pairing.store_path().exists()


def test_3_unpaired_redirects_to_pair_page(app):
    client = app.test_client()
    for path in ("/m/", "/m/customers", "/m/orders"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/m/pair" in response.headers["Location"], path


def test_4_paired_pages_render_and_expose_no_write_controls(paired_client, seeded):
    for path in ("/m/", "/m/customers", f"/m/customers/{seeded['customer_id']}",
                 "/m/orders", "/m/products", "/m/settings"):
        response = paired_client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        # 不能有可点的写入口：任何 <a>/<button> 里都不该出现 删除/作废/编辑 动作
        assert not re.findall(r"<(?:a|button)[^>]*>[^<]*(?:删除|作废|编辑)", html), path
        assert "method=\"post\"" not in html, path
        assert "/delete" not in html and "/void" not in html, path


def test_4b_pair_page_is_reachable_and_has_the_only_post_form(app):
    client = app.test_client()
    response = client.get("/m/pair")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'method="post"' in html


def test_5_batch_balance_matches_authoritative_ledger(seeded):
    from erp.services import accounting

    ids = [seeded["customer_id"], seeded["other_customer_id"]]
    batch = readonly_queries.customer_balances(ids)
    for customer_id in ids:
        assert batch[customer_id] == accounting.get_customer_account_ledger(
            customer_id
        )["current_balance_cents"]


def test_6_connection_is_physically_read_only(seeded):
    with readonly_queries.connect_readonly() as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO orders(order_no, customer_id, order_date, total_amount_cents)"
                " VALUES ('MD-FAKE', ?, '2026-10-17', 1)",
                (seeded["customer_id"],),
            )
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE mobile_should_not_write (id INTEGER)")


def test_today_page_shows_four_groups_and_two_nets(paired_client, seeded):
    html = paired_client.get("/m/").get_data(as_text=True)
    for label in ("销售单", "退货单", "拿货单", "退拿货单", "卖货净额", "进货净额"):
        assert label in html, label
    # 禁止四类相加的总计：页面上不出现「合计」这种把收支混加的数
    assert "合计" not in html


def test_order_list_filters_by_type_and_date(paired_client, seeded):
    html = paired_client.get("/m/orders?date_mode=day").get_data(as_text=True)
    assert "共 4 张" in html

    sale_only = paired_client.get("/m/orders?order_type=sale&date_mode=day").get_data(as_text=True)
    assert "共 1 张" in sale_only
    assert "拿货单" not in sale_only

    all_time = paired_client.get("/m/orders?date_mode=all").get_data(as_text=True)
    assert "共 5 张" in all_time


def test_product_page_shows_customer_price_side_by_side(paired_client, seeded):
    html = paired_client.get(f"/m/products?customer_id={seeded['customer_id']}").get_data(as_text=True)
    assert "专属价" in html and "最近成交" in html
    assert "手机端测试商品" in html


def test_customer_detail_shows_exact_money_and_no_cost(paired_client, seeded):
    html = paired_client.get(f"/m/customers/{seeded['customer_id']}").get_data(as_text=True)
    assert "当前欠款" in html
    # 精确到分（详情才给精确金额）
    assert "¥160.00" in html
    # 成本/进价一律不出现
    for forbidden in ("成本", "进价", "利润"):
        assert forbidden not in html


def test_revoked_device_falls_back_to_pair_page(app):
    client = app.test_client()
    cookie_value = pair_client(client)
    assert client.get("/m/").status_code == 200

    device = mobile_pairing.device_from_cookie(cookie_value)
    mobile_pairing.revoke_device(device["id"])
    response = client.get("/m/")
    assert response.status_code == 302
    assert "/m/pair" in response.headers["Location"]
