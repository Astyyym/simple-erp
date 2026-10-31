"""全量快照 `/m/api/snapshot` 的契约（2026-10-24）。

这个端点是「关电脑前让手机拉一遍」的唯一入口，它一旦不准，离线看到的就是错数据 ——
比「什么都没缓存」更危险。所以测试盯四件事：

1. **覆盖得全**：列表、详情、今日、客户、商品都要有，特别是**详情**
   （二级页面刷不出来就是这个覆盖不全造成的）。
2. **规范化 path 与真实端点逐字一致**：客户端拿这个串当缓存键，对不上就查不到缓存。
   这里直接拿 `entries` 的键去请求同一个 app，断言 200 —— 这是端到端的一致性证明。
3. **上限生效**：不能因为某个客户有几千张单把一次同步拖垮。
4. **不许偷懒写进 erp.db**：快照只写 `mobile_devices.json` 的 `last_sync_at`。
"""
from __future__ import annotations

import json

import pytest

from erp.db import init_db
from erp.mobile_app import create_mobile_app
from erp.services import mobile_pairing
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
    pair_client(client, label="快照测试手机")
    return client


def _snapshot(client):
    response = client.get("/m/api/snapshot")
    assert response.status_code == 200
    return response.get_json()


def test_snapshot_requires_pairing(app):
    client = app.test_client()
    response = client.get("/m/api/snapshot")
    assert response.status_code == 401
    assert response.get_json()["needs_pairing"] is True


def test_snapshot_covers_lists_and_details(paired_client, seeded):
    data = _snapshot(paired_client)
    counts = data["counts"]

    assert counts["orders"] >= 4, "四类单据都该出现在快照里"
    assert counts["order_details"] >= 4, "**每张单据的详情也要有**，否则断网点进去白屏"
    assert counts["customers"] >= 2
    assert counts["customer_details"] >= 2
    assert counts["products"] >= 1
    assert counts["product_details"] >= 1
    assert data["entry_count"] == len(data["entries"])


def test_snapshot_entries_resolve_to_real_endpoints(paired_client, seeded):
    """核心断言：`entries` 的每个键拿去请求，都必须 200 且 `ok`。

    这条把「客户端缓存键」和「服务端真实路由」钉在一起。任何一侧改了参数拼法
    （多一个默认值、少一个空格）这里就会红 —— 而不是等到用户断网才发现查不到。
    """
    data = _snapshot(paired_client)
    for path in data["entries"]:
        response = paired_client.get(path)
        assert response.status_code == 200, path
        body = response.get_json()
        assert body["ok"] is True, path


def test_snapshot_entries_are_parseable_json(paired_client, seeded):
    data = _snapshot(paired_client)
    for path, raw in data["entries"].items():
        parsed = json.loads(raw)
        assert parsed["ok"] is True, path
        assert parsed["schema"] == data["schema"], path


def test_snapshot_marks_device_synced(paired_client, seeded):
    """快照成功要写下 `last_sync_at` —— 桌面端关闭提醒显示的就是这个时间。"""
    before = mobile_pairing.list_devices()[0]["last_sync_at"]
    assert before == ""
    data = _snapshot(paired_client)
    assert data["synced_at"]
    after = mobile_pairing.list_devices()[0]["last_sync_at"]
    assert after, "快照成功后必须记下同步时间"
    assert after != before


def test_snapshot_canonical_path_sorts_query_keys(paired_client, seeded):
    """规范化键按字母序拼 query：`date_mode=…&month=…&year=…`。

    与客户端 `viewModel.loadOrders()` 的 `toSortedMap()` 同一个约定；
    这里只钉「同一批参数只有一个键」，不钉客户端实现细节。
    """
    data = _snapshot(paired_client)
    order_keys = [key for key in data["entries"] if key.startswith("/m/api/orders?")]
    assert order_keys, "单据列表至少要有几档日期筛选"
    for key in order_keys:
        if "&" in key:
            params = key.split("?", 1)[1].split("&")
            names = [part.split("=", 1)[0] for part in params]
            assert names == sorted(names), key
            assert len(names) == len(set(names)), key


def test_snapshot_same_day_variants_are_distinct_keys(paired_client, seeded):
    """`date_mode=day` 与带起止的 `date_mode=day&start_date=…` 是**两个**键。

    客户端首次进入只发前者、点「今天」chip 发后者 —— 少一份就有一档断网空白。
    """
    data = _snapshot(paired_client)
    keys = set(data["entries"])
    assert "/m/api/orders?date_mode=day" in keys
    # 规范化键按字母序拼：date_mode → end_date → start_date。
    assert any(
        key.startswith("/m/api/orders?date_mode=day&end_date=") and "&start_date=" in key
        for key in keys
    )


def test_snapshot_skips_empty_query_values(app, seeded):
    """空值不进键：`start_date=` 这种空串若进键，就会与「没传」分成两份缓存。"""
    from erp.routes.mobile_api import _canonical_path

    assert _canonical_path("/m/api/orders", {"date_mode": "day", "start_date": ""}) == (
        "/m/api/orders?date_mode=day"
    )
    assert _canonical_path("/m/api/today", {}) == "/m/api/today"


def test_snapshot_contains_order_detail_for_each_listed_document(paired_client, seeded):
    """列表里出现的每一张单（在前 120 张以内）都必须有对应详情条目。"""
    data = _snapshot(paired_client)
    entries = data["entries"]
    missing = []
    for key, raw in entries.items():
        if not key.startswith("/m/api/orders?") or "?" not in key:
            continue
        body = json.loads(raw)
        for document in body["documents"]:
            detail_key = f"/m/api/orders/{document['order_type']}/{document['id']}"
            if detail_key not in entries:
                missing.append(detail_key)
    # 上限 120 张详情；测试数据只有 5 张，所以这里应该是空。
    assert missing == [], f"这些单在列表里但没有详情缓存：{missing[:5]}"


def test_snapshot_does_not_write_business_tables(paired_client, seeded):
    """快照是只读的：跑完之后单据/客户/商品数量不变。"""
    from erp.db import get_db
    from erp.services import readonly_queries

    before = readonly_queries.list_documents(limit=200)
    with get_db() as conn:
        before_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    _snapshot(paired_client)
    after = readonly_queries.list_documents(limit=200)
    with get_db() as conn:
        after_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    assert len(before["documents"]) == len(after["documents"])
    assert before_customers == after_customers


def test_snapshot_counts_match_entries(paired_client, seeded):
    data = _snapshot(paired_client)
    counts = data["counts"]
    keys = data["entries"]
    detail_keys = [key for key in keys if key.startswith("/m/api/orders/")]
    assert counts["order_details"] == len(detail_keys)
    assert counts["customer_details"] == len(
        [key for key in keys if key.startswith("/m/api/customers/")]
    )
    assert counts["product_details"] == len(
        [key for key in keys if key.startswith("/m/api/products/")]
    )


def test_snapshot_covers_every_customer_not_just_the_first_forty(paired_client):
    """客户详情要覆盖**全部客户**（2026-10-31 从 40 提到 1000）。

    40 的上限会让第 41 个之后的客户断网时点开是空白，而「查谁欠多少钱」正是手机端
    最高频的用法 —— 用户不可能知道自己想看的那位排在第几个。
    """
    from erp.services import accounting

    for index in range(45):
        accounting.create_customer(f"批量客户{index:02d}")

    data = _snapshot(paired_client)
    entries = data["entries"]
    listing = paired_client.get("/m/api/customers").get_json()["customers"]
    assert len(listing) >= 45, "客户列表本身也要全量，否则详情无从遍历"
    missing = [
        customer["id"]
        for customer in listing
        if f"/m/api/customers/{customer['id']}" not in entries
    ]
    assert missing == [], f"这些客户在列表里但没有详情缓存：{missing[:5]}"


def test_snapshot_covers_products_beyond_the_old_sixty_cap(paired_client):
    """商品详情也要覆盖全部商品（2026-10-31 从 60 提到 300）。

    消防器材店商品常见 100~300 个；60 的上限会让商品页和快照都只覆盖前 60 个，
    而且页面上没有任何提示。
    """
    from erp.services import accounting

    for index in range(65):
        accounting.create_product(f"批量商品{index:02d}", "个")

    data = _snapshot(paired_client)
    entries = data["entries"]
    listing = paired_client.get("/m/api/products").get_json()["products"]
    assert len(listing) >= 65, "商品列表本身也要越过 60，否则详情无从遍历"
    missing = [
        product["id"]
        for product in listing
        if f"/m/api/products/{product['id']}" not in entries
    ]
    assert missing == [], f"这些商品在列表里但没有详情缓存：{missing[:5]}"
