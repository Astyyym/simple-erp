"""名称归一副本（`name_norm`）与「带括号/空格/全角」名称的检索回归。

背景（2026-10-20 用户实测）：用户选中一个**有专属价**的商品后，只有名称进了单元格，
单价始终空白。根因是两侧形态不一致：

  · 检索串进 SQL 前被 `normalize_name_query()` 归一（`PVC管（厚）` → `PVC管(厚)`）
  · 库里 `name` 仍原样保存（全角括号、含空格）
  → `name LIKE '%PVC管(厚)%'` 永远打不中，`/orders/api/products` 用**完整名称**回查时返回空

修复：新增 `name_norm` 归一副本列（写入时按同一规则归一），匹配改比这一列；
拼音两列改为基于 NFKC + 去括号后的名称生成，全拼不再带着全角括号。

本文件锁住三件事：
1. 派生列本身（`pinyin_full` / `name_norm`）的产物
2. 四条真实搜索入口对这类名称都搜得到（含全拼）
3. 点选后按完整名称回查**能取到单价**——即用户报的那个现象
"""
from __future__ import annotations

from urllib.parse import quote

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
)
from erp.utils.pinyin import normalize_name_for_store, pinyin_full

# 覆盖用户实际踩到的三类形态：全角括号、名称含空格、全角数字/字母
AWKWARD_NAMES = [
    "PVC管（厚）",
    "水龙头（铜）",
    "软密封闸阀 DN100",
    "４分弯头",
    "电缆ＹＪＶ２２",
    "螺栓 M8×40",
]


# ---------------------------------------------------------------- 派生列产物

def test_pinyin_full_drops_fullwidth_and_brackets():
    """括号在拼音里没有发音，留在派生列里会让 `pvcguanhou` 打不中。"""
    assert pinyin_full("PVC管（厚）") == "pvcguanhou"
    assert pinyin_full("水龙头（铜）") == "shuilongtoutong"
    # 半角括号同样去掉，两种写法收敛到同一个全拼
    assert pinyin_full("PVC管(厚)") == "pvcguanhou"
    # 名称里的空格不进全拼
    assert pinyin_full("软密封闸阀 DN100") == "ruanmifengzhafadn100"


def test_normalize_name_for_store_matches_query_rules():
    """`name_norm` 必须与检索串同一套归一，否则又会回到「库里一套、检索一套」。"""
    assert normalize_name_for_store("PVC管（厚）") == "PVC管(厚)"
    assert normalize_name_for_store("软密封闸阀 DN100") == "软密封闸阀DN100"
    assert normalize_name_for_store("４分弯头") == "4分弯头"
    assert normalize_name_for_store("  螺栓  ") == "螺栓"


# ---------------------------------------------------------------- 写入即归一

@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_product_write_stores_normalized_copy(name):
    init_db()
    create_product(name, "个")
    with get_db() as conn:
        row = conn.execute(
            "SELECT name, name_norm, pinyin_full FROM products WHERE name=?", (name,)
        ).fetchone()
    assert row is not None, f"商品未建档: {name}"
    assert row["name"] == name, "原始名称必须原样保留（打印/展示用）"
    assert row["name_norm"] == normalize_name_for_store(name)
    # 全拼里不应再出现全角括号
    assert "（" not in row["pinyin_full"] and "）" not in row["pinyin_full"]


def test_backfill_repairs_stale_rows():
    """老库升级：`name_norm` 为空、`pinyin_full` 是旧规则产物，都应在 init_db 时修好。"""
    init_db()
    create_product("PVC管（厚）", "个")
    with get_db() as conn:
        conn.execute("UPDATE products SET name_norm='', pinyin_full='pvcguan（hou）' WHERE name='PVC管（厚）'")
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT name_norm, pinyin_full FROM products WHERE name='PVC管（厚）'"
        ).fetchone()
    assert row["name_norm"] == "PVC管(厚)"
    assert row["pinyin_full"] == "pvcguanhou"


# ---------------------------------------------------------------- 搜索入口

@pytest.fixture()
def seeded():
    init_db()
    customer_id = create_customer("回归客户")
    for index, name in enumerate(AWKWARD_NAMES):
        create_product(name, "个")
        create_order_from_typed_rows(
            customer_id,
            f"NM{index:04d}",
            [{"product_name": name, "unit": "个", "unit_price_yuan": "88", "quantity": "1"}],
            status="saved",
        )
    return customer_id


@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_full_pinyin_finds_awkward_product_names(seeded, name):
    """用户诉求：全拼也要能搜到（不能只有首字母）。"""
    from erp.utils.pinyin import pinyin_full

    query = pinyin_full(name)
    client = create_app().test_client()
    payload = client.get("/orders/api/products?q=" + quote(query) + f"&customer_id={seeded}").get_json()
    assert name in [row["name"] for row in payload], f"全拼 {query!r} 搜不到 {name!r}"


@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_product_page_search_finds_awkward_names(seeded, name):
    client = create_app().test_client()
    html = client.get("/products/?q=" + quote(name)).get_data(as_text=True)
    assert name in html, f"商品档案页搜不到 {name!r}"


def test_customer_page_search_uses_full_pinyin():
    init_db()
    customer_name = "星辰宾馆（总店）"
    create_customer(customer_name)
    client = create_app().test_client()
    html = client.get("/customers/?q=" + quote(pinyin_full(customer_name))).get_data(as_text=True)
    assert customer_name in html


# ---------------------------------------------------------------- 用户报的现象

@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_picking_product_returns_customer_price(seeded, name):
    """核心回归：点选后前端用**完整名称**回查专属价，必须拿到钱。

    修复前这里返回空列表 → 页面表现就是「名称进了单元格、单价空白」。
    """
    client = create_app().test_client()
    payload = client.get("/orders/api/products?q=" + quote(name) + f"&customer_id={seeded}").get_json()
    hit = [row for row in payload if row["name"] == name]
    assert hit, f"按完整名称回查不到 {name!r}（点选后单价会空白）"
    assert hit[0]["unit_price"] == "88.00"
    assert hit[0]["is_customer_price"] is True


def test_reprice_after_customer_switch_returns_price(seeded):
    """换客户会重算所有已命中行，走的也是按名称回查——同样必须取到价。"""
    other_customer = create_customer("换客户-回归")
    with get_db() as conn:
        product_id = conn.execute("SELECT id FROM products WHERE name='PVC管（厚）'").fetchone()["id"]
    create_order_from_typed_rows(
        other_customer,
        "NM9001",
        [{"product_name": "PVC管（厚）", "unit": "个", "unit_price_yuan": "99", "quantity": "1"}],
        status="saved",
    )
    client = create_app().test_client()
    payload = client.get(
        f"/orders/api/products?q={quote('PVC管（厚）')}&customer_id={other_customer}"
    ).get_json()
    hit = [row for row in payload if row["id"] == product_id]
    assert hit and hit[0]["unit_price"] == "99.00"
