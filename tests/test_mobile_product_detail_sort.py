"""商品专属价详情页的**页内搜索与排序**（2026-10-24 用户第七张截图要求）。

用户原话：「商品专属价的详情页中，商品名在上下滑动的时候顶部固定，动画，字体样式
大小参考首页。然后，商品名的下面添加『搜索框 拼音 （正序倒序的上下）箭头』，
箭头的行为，也是回顶刷新。」

本文件锁三件事：
1. 顶部有**吸顶条**（`.pin` + `position:sticky`）包住商品名/搜索/排序，且商品名不再
   只放在 `.hd` 里跟着滚走 —— 判据是 HTML 结构钩子，不是肉眼。
2. 搜索 / 拼音 / 价格 / 方向都是**服务端 GET 参数**，刷新与返回后条件还在；
   排序必须真的改变结果顺序（不能只改按钮样式）。
3. 方向箭头带 `data-reset-scroll`，mobile.js 里对应的回顶信号在「新内容上屏后」消费
   （先滚会被浏览器的位置恢复盖掉，2026-10-23 客户/商品列表上踩过）。
"""
from __future__ import annotations

import pytest

from erp.db import init_db
from erp.mobile_app import create_mobile_app
from erp.services import accounting, readonly_queries
from mobile_fixtures import pair_client


@pytest.fixture()
def app():
    init_db()
    return create_mobile_app()


@pytest.fixture()
def paired_client(app):
    client = app.test_client()
    pair_client(client)
    return client


def _seed_three_prices() -> dict:
    """三个客户各有一个专属价：老李 262.78 / 德邦 260.73 / 速8 251.10。

    名字特意让「按拼音」与「按价格」顺序不同：
    拼音 debang < laoli < su8；价格 251.10 < 260.73 < 262.78
    → 两种排序的第一名不同（速8 vs 老李），断言才有区分度。
    """
    init_db()
    product_id = accounting.create_product("蝶阀DN80", "个")
    prices = {
        "老李五金店": 26278,
        "德邦物流园区": 26073,
        "速8酒店火车站店": 25110,
    }
    ids = {}
    for name, cents in prices.items():
        customer_id = accounting.create_customer(name)
        accounting.set_customer_price(customer_id, product_id, cents)
        ids[name] = customer_id
    return {"product_id": product_id, "customers": ids, "prices": prices}


# --------------------------------------------------------------------------- 页面结构

def test_detail_page_has_a_pinned_header_above_the_list(paired_client):
    """商品名 + 搜索 + 排序的「固定」是两层，判据要分开验。

    ① 商品名在 `.hd` 里，而 `.hd` 是滚动容器 `.body` 的**兄弟节点** —— 固定由结构保证，
       不靠 sticky（sticky 在滚动容器外面无从谈起）。动画由滚动量驱动：
       滑过 40px 打 `data-pin-compact`，标题 22px→16px、副标题淡出。
    ② 搜索/排序条用 `position:sticky` 贴在 `.body` 顶部。
    """
    from pathlib import Path

    data = _seed_three_prices()
    html = paired_client.get(f"/m/products/{data['product_id']}").get_data(as_text=True)

    # ① 结构：`.hd` 在 `.body` 之外（页头区块在 body 区块之前，两者是兄弟）。
    header_pos = html.index('class="hd"')
    body_pos = html.index('class="body"')
    assert header_pos < body_pos, "商品名（.hd）必须在滚动容器 .body 之前"
    assert 'class="pin"' in html, "搜索/排序条需要吸顶容器"
    pin_css = html.split(".pin{", 1)[1].split("}", 1)[0]
    assert "position:sticky" in pin_css and "top:0" in pin_css

    # ① 动画：折叠两态都写在 CSS 里，且能被「减少动态效果」关掉。
    assert 'html[data-pin-compact="1"] .hd h1{font-size:16px' in html
    assert "prefers-reduced-motion" in html
    # 折叠由 mobile.js 驱动（rAF 节流，不在 scroll 里直接写样式）。
    js = (Path(__file__).resolve().parents[1] / "app" / "erp" / "static" / "js" / "mobile" / "mobile.js").read_text(
        encoding="utf-8"
    )
    assert 'data-page="product"' in js
    assert "data-pin-compact" in js
    assert "requestAnimationFrame(applyPinState)" in js

    # ② 搜索框在吸顶条里，表单指向本页（GET，条件进 URL）。
    pin_block = html.split('class="pin"', 1)[1].split("</div>", 2)[0]
    assert 'name="q"' in pin_block
    assert f'action="/m/products/{data["product_id"]}"' in html
    assert ">拼音<" in html and ">价格<" in html
    assert 'data-reset-scroll' in html, "方向箭头要带「回顶」标记"
    # 商品名与字号 token：与首页页头同一套（--title-size = 22px / 字重 640）。
    assert "蝶阀DN80" in html
    css = (Path(__file__).resolve().parents[1] / "app" / "erp" / "static" / "css" / "mobile.css").read_text(
        encoding="utf-8"
    )
    assert "--title-size: 22px" in css
    assert "font-size: var(--title-size)" in css


def test_sort_links_keep_query_and_toggle_direction(paired_client):
    """排序/方向链接必须带上当前搜索词与另一维度的值（否则点一下就丢条件）。"""
    data = _seed_three_prices()
    url = f"/m/products/{data['product_id']}?q=店&sort=price&order=desc"
    html = paired_client.get(url).get_data(as_text=True)

    # 切到拼音：保留 q，方向保留。
    assert "sort=pinyin" in html and "order=desc" in html
    # 方向箭头：desc → asc，且保留 q / sort。
    assert "order=asc" in html
    assert "q=%E5%BA%97" in html or "q=店" in html


# --------------------------------------------------------------------------- 排序真的生效

def test_price_sort_default_is_descending_and_ascending_reverses_it():
    data = _seed_three_prices()
    desc = readonly_queries.product_customer_prices(data["product_id"])
    assert [p["price_cents"] for p in desc["prices"]] == [26278, 26073, 25110]

    asc = readonly_queries.product_customer_prices(data["product_id"], order="asc")
    assert [p["price_cents"] for p in asc["prices"]] == [25110, 26073, 26278]


def test_pinyin_sort_uses_customer_pinyin_not_price():
    data = _seed_three_prices()
    by_pinyin = readonly_queries.product_customer_prices(
        data["product_id"], sort="pinyin", order="asc"
    )
    names = [p["customer_name"] for p in by_pinyin["prices"]]
    # 首字母：debang(d) < laoli(l) < su8(s)
    assert names == ["德邦物流园区", "老李五金店", "速8酒店火车站店"]
    # 与价格序不同 —— 否则这条断言证明不了「排序真的换了」。
    assert names != ["老李五金店", "德邦物流园区", "速8酒店火车站店"]


def test_search_filters_by_name_and_pinyin():
    data = _seed_three_prices()
    by_name = readonly_queries.product_customer_prices(data["product_id"], q="德邦")
    assert [p["customer_name"] for p in by_name["prices"]] == ["德邦物流园区"]

    # 拼音首字母（db → 德邦）也要命中，与其它页同一套 name_match_clause。
    by_pinyin = readonly_queries.product_customer_prices(data["product_id"], q="db")
    assert [p["customer_name"] for p in by_pinyin["prices"]] == ["德邦物流园区"]

    empty = readonly_queries.product_customer_prices(data["product_id"], q="不存在的客户")
    assert empty["prices"] == []
    # 空结果不能让「还有 N 个客户没设价」变成 0 —— 那个数是**全量**口径，与搜索无关。
    assert empty["customers_without_price"]["count"] == 0  # 这里三个客户都有价


def test_unknown_sort_and_order_fall_back_to_defaults(paired_client):
    data = _seed_three_prices()
    result = readonly_queries.product_customer_prices(
        data["product_id"], sort=";DROP", order="sideways"
    )
    assert result["sort"] == "price"
    assert result["order"] == "desc"
    assert [p["price_cents"] for p in result["prices"]] == [26278, 26073, 25110]


# --------------------------------------------------------------------------- 回顶脚本

def test_mobile_js_consumes_reset_scroll_after_new_content_renders():
    """回顶信号必须在「恢复旧位置之后、新内容上屏之后」消费。

    顺序错的话浏览器会把滚动位置夹回旧值，用户看到的是「点了箭头，停在原行号」
    （2026-10-23 在客户/商品列表上踩过）。
    """
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "erp" / "static" / "js" / "mobile" / "mobile.js").read_text(
        encoding="utf-8"
    )
    assert "erp-mobile-reset-scroll" in js
    assert "consumeResetScroll()" in js
    # 在 onDocumentSettled 里、restoreScroll 之后调用（顺序就是语义）。
    settled = js.split("function onDocumentSettled() {", 1)[1].split("\n  }", 1)[0]
    assert settled.index("restoreScroll") < settled.index("consumeResetScroll")
    # 双帧等待：新内容排版完成再滚。
    body = js.split("function consumeResetScroll() {", 1)[1].split("\n  }", 1)[0]
    assert "requestAnimationFrame" in body
    assert "scrollTop = 0" in body


# --------------------------------------------------------------------------- 原生接口

def test_mobile_api_product_detail_accepts_query_and_sort(paired_client):
    data = _seed_three_prices()
    base = f"/m/api/products/{data['product_id']}"

    default = paired_client.get(base).get_json()
    assert default["ok"] is True
    assert [p["price_cents"] for p in default["prices"]] == [26278, 26073, 25110]
    assert default["sort"] == "price" and default["order"] == "desc"

    asc = paired_client.get(base + "?sort=price&order=asc").get_json()
    assert [p["price_cents"] for p in asc["prices"]] == [25110, 26073, 26278]

    pinyin = paired_client.get(base + "?sort=pinyin&order=asc").get_json()
    assert [p["customer_name"] for p in pinyin["prices"]] == [
        "德邦物流园区", "老李五金店", "速8酒店火车站店",
    ]

    searched = paired_client.get(base + "?q=db").get_json()
    assert [p["customer_name"] for p in searched["prices"]] == ["德邦物流园区"]
    assert searched["query"] == "db"
