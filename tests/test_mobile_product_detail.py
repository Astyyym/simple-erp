"""商品详情（客户价全景）+ 客户详情「专属价」段。

对拍是硬要求（计划 §九 停止条件）：查询结果必须与桌面端商品编辑页
「客户专属价」卡片同源，不允许手机端自创口径。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from erp.db import get_db, init_db
from erp.mobile_app import create_mobile_app
from erp.services import accounting, readonly_queries
from mobile_fixtures import pair_client, seed_all


@pytest.fixture()
def app():
    init_db()
    return create_mobile_app()


@pytest.fixture()
def paired_client(app):
    client = app.test_client()
    pair_client(client)
    return client


def _seed_prices() -> dict:
    """三个客户有专属价（50 / 30 / 20 元），另有一个客户完全没有价。

    `seed_all()` 本身已学出两个价：
    - 手机端测试客户：最后一单退货 30 元 → 3000
    - 手机端第二客户：跨月销售 50 元 → 5000
    """
    data = seed_all()
    third = accounting.create_customer("第三客户")
    fourth = accounting.create_customer("无价客户")
    accounting.set_customer_price(third, data["product_id"], 2000)
    return {**data, "third_customer_id": third, "no_price_customer_id": fourth}


def test_product_customer_prices_sorted_and_equal_to_desktop_card():
    data = _seed_prices()
    result = readonly_queries.product_customer_prices(data["product_id"])

    assert result["product"]["name"] == "手机端测试商品 MFZ/ABC4"
    assert [price["price_cents"] for price in result["prices"]] == [5000, 3000, 2000]
    assert [price["customer_name"] for price in result["prices"]] == [
        "手机端第二客户", "手机端测试客户", "第三客户",
    ]

    # 桌面端商品编辑页（routes/products.py:_render_product_edit）的原 SQL
    with get_db() as conn:
        desktop = conn.execute(
            """
            SELECT c.name AS customer_name, cp.price_cents, cp.updated_at
            FROM customer_prices AS cp
            JOIN customers AS c ON c.id = cp.customer_id
            WHERE cp.product_id=?
            ORDER BY c.name COLLATE NOCASE ASC, c.id ASC
            """,
            (data["product_id"],),
        ).fetchall()
    assert {(row["customer_name"], row["price_cents"], row["updated_at"]) for row in desktop} == {
        (price["customer_name"], price["price_cents"], price["updated_at"])
        for price in result["prices"]
    }


def test_updated_label_is_local_not_utc_raw_and_last_deal_matches_product_page_rule():
    data = _seed_prices()
    result = readonly_queries.product_customer_prices(data["product_id"])
    by_name = {price["customer_name"]: price for price in result["prices"]}

    first = by_name["手机端测试客户"]
    raw = first["updated_at"]
    # 库里是 UTC（CURRENT_TIMESTAMP），页面必须是本机时间
    moment = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
    assert first["updated_label"] == f"{moment.month}月{moment.day}日 {moment:%H:%M}"

    # 最近成交价：与 product_prices() 同一口径（该客户最近一张有效销售/退货单）
    assert first["last_price_cents"] == 3000  # 退货单 30 元，id 更大
    assert by_name["手机端第二客户"]["last_price_cents"] == 5000
    assert by_name["第三客户"]["last_price_cents"] is None


def test_customers_without_price_counts_live_customers_only():
    data = _seed_prices()
    result = readonly_queries.product_customer_prices(data["product_id"])
    missing = result["customers_without_price"]
    assert missing["count"] == 1
    assert missing["names"] == ["无价客户"]

    # 已删客户不计入「没有专属价」，也不从有价列表里消失（桌面端也不过滤）
    with get_db() as conn:
        conn.execute(
            "UPDATE customers SET deleted_at='2026-10-17 00:00:00' WHERE id=?",
            (data["no_price_customer_id"],),
        )
    after = readonly_queries.product_customer_prices(data["product_id"])
    assert after["customers_without_price"]["count"] == 0


def test_product_detail_page_shows_every_customer_price(paired_client):
    data = _seed_prices()
    response = paired_client.get(
        f"/m/products/{data['product_id']}?customer_id={data['customer_id']}"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "手机端测试商品 MFZ/ABC4" in html
    assert "客户专属价" in html
    assert html.count('class="row"') == 3, "3 个客户价必须正好 3 行"
    names = ["手机端第二客户", "手机端测试客户", "第三客户"]
    assert [html.index(name) for name in names] == sorted(html.index(name) for name in names), "价格降序"
    for name in names:
        assert name in html, name
    for money in ("¥50", "¥30", "¥20"):
        assert money in html, money
    assert f"/m/customers/{data['third_customer_id']}" in html
    assert "看这个客户的全部专属价" in html
    # 没有价的客户不是「不存在」：要点名，否则店主会以为只有这几家买过
    assert "另有 1 个客户没有这个商品的专属价" in html
    assert "无价客户" in html


def test_product_without_customer_prices_has_empty_state_and_back_link(paired_client):
    data = _seed_prices()
    product_id = accounting.create_product("没有专属价的商品", "个")
    html = paired_client.get(
        f"/m/products/{product_id}?customer_id={data['customer_id']}"
    ).get_data(as_text=True)

    assert "这个商品还没有客户专属价" in html
    assert "看这个客户的全部专属价" in html
    # 4 个未删客户都没有这个商品的价
    assert "另有 4 个客户没有这个商品的专属价" in html


def test_deleted_customer_price_is_listed_but_not_linked(paired_client):
    data = _seed_prices()
    with get_db() as conn:
        conn.execute(
            "UPDATE customers SET deleted_at='2026-10-17 00:00:00' WHERE id=?",
            (data["third_customer_id"],),
        )
    result = readonly_queries.product_customer_prices(data["product_id"])
    row = next(p for p in result["prices"] if p["customer_id"] == data["third_customer_id"])
    assert row["customer_deleted"] is True

    html = paired_client.get(f"/m/products/{data['product_id']}").get_data(as_text=True)
    assert "已删除" in html
    assert f"/m/customers/{data['third_customer_id']}\"" not in html


def test_product_detail_is_get_only_and_outside_blueprint_is_404(paired_client):
    data = _seed_prices()
    assert paired_client.get("/m/products/999999").status_code == 404
    assert paired_client.post(f"/m/products/{data['product_id']}").status_code == 405
    # 桌面端商品路由在第二监听上仍然天然 404
    assert paired_client.get("/products/").status_code == 404


def test_product_list_cards_link_to_detail(paired_client):
    data = _seed_prices()
    html = paired_client.get("/m/products").get_data(as_text=True)
    assert f'/m/products/{data["product_id"]}"' in html
    # 商品优先：链接不再带 customer_id
    assert f'/m/products/{data["product_id"]}?customer_id=' not in html


def test_customer_price_list_is_reverse_of_product_query():
    data = _seed_prices()
    reverse = readonly_queries.customer_price_list(data["customer_id"])
    row = next(p for p in reverse["prices"] if p["product_id"] == data["product_id"])
    product_view = readonly_queries.product_customer_prices(data["product_id"])
    mine = next(
        p for p in product_view["prices"] if p["customer_id"] == data["customer_id"]
    )
    assert row["price_cents"] == mine["price_cents"] == 3000
    assert row["product_name"] == product_view["product"]["name"]
    assert readonly_queries.customer_price_list(999999) is None


def test_customer_detail_shows_top_three_prices_and_a_see_all_link(paired_client):
    data = _seed_prices()
    extra_products = [
        accounting.create_product(f"专属价商品{index}", "个") for index in (1, 2, 3)
    ]
    for offset, product_id in enumerate(extra_products):
        accounting.set_customer_price(data["customer_id"], product_id, 1000 + offset)

    html = paired_client.get(f"/m/customers/{data['customer_id']}").get_data(as_text=True)
    assert "专属价" in html
    # 4 个专属价 → 只列前 3 条，给「看全部」
    assert "看全部 4 个专属价" in html
    assert "专属价商品3" in html and "专属价商品2" in html
    assert "专属价商品1" not in html
    assert "手机端测试商品 MFZ/ABC4" in html

    # 点进去的商品详情，价格与客户详情里那一行一致
    detail_html = paired_client.get(
        f"/m/products/{extra_products[2]}?customer_id={data['customer_id']}"
    ).get_data(as_text=True)
    assert "¥10.02" in detail_html


def test_product_price_summary_counts_range_and_search():
    data = _seed_prices()
    result = readonly_queries.product_price_summary("")
    product = next(p for p in result["products"] if p["id"] == data["product_id"])
    assert product["price_count"] == 3
    assert (product["min_price_cents"], product["max_price_cents"]) == (2000, 5000)

    searched = readonly_queries.product_price_summary("手机端测试商品")
    assert [p["id"] for p in searched["products"]] == [data["product_id"]]

    empty_id = accounting.create_product("没有价汇总商品", "个")
    empty = readonly_queries.product_price_summary("没有价汇总")
    assert empty["products"][0]["id"] == empty_id
    assert empty["products"][0]["price_count"] == 0
    assert empty["products"][0]["min_price_cents"] is None


def test_product_list_is_product_first_and_shows_summary(paired_client):
    data = _seed_prices()
    html = paired_client.get("/m/products").get_data(as_text=True)
    assert "搜商品名" in html
    assert 'class="customer-switch"' not in html
    assert "3 个客户" in html
    assert "¥20 – ¥50" in html


def test_customer_detail_see_all_opens_customer_prices_page(paired_client):
    data = _seed_prices()
    extra = [accounting.create_product(f"专属价商品{index}", "个") for index in (1, 2, 3)]
    for offset, product_id in enumerate(extra):
        accounting.set_customer_price(data["customer_id"], product_id, 1000 + offset)

    detail_html = paired_client.get(f"/m/customers/{data['customer_id']}").get_data(as_text=True)
    assert f'/m/customers/{data["customer_id"]}/prices' in detail_html

    response = paired_client.get(f"/m/customers/{data['customer_id']}/prices")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "全部专属价" in html
    assert "4 个商品" in html
    for index in (1, 2, 3):
        assert f"专属价商品{index}" in html
    assert f'/m/products/{extra[2]}?customer_id={data["customer_id"]}' in html


def test_product_detail_from_global_has_no_customer_only_link(paired_client):
    data = _seed_prices()
    html = paired_client.get(f"/m/products/{data['product_id']}").get_data(as_text=True)
    assert "看这个客户的全部专属价" not in html
    assert 'href="/m/products"' in html
