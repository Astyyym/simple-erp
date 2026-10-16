"""Phase 3 S5：商品侧两处「只按名称」的建议接口补拼音。

计划 §13.2 S5：`routes/products.py` 的列表 suggestions（`_product_list_context`）与
`/products/suggestions` API 原先只按 `name LIKE`，列表页搜索框输拼音无候选；
`/orders/api/products` 与商品列表主体查询早已含 `p.pinyin_initials LIKE`（Phase 2 后保留）。

本文件锁「两条建议接口都能按拼音首字母命中，中文子串仍命中」。
"""
from __future__ import annotations

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_product
from erp.utils.pinyin import pinyin_initials


def _seed_products():
    init_db()
    create_product("闸阀DN50", "个", pinyin_initials("闸阀"))
    create_product("灭火器4kg", "个", pinyin_initials("灭火器"))


def test_product_list_suggestions_match_pinyin():
    _seed_products()
    html = create_app().test_client().get("/products/?q=zf").get_data(as_text=True)
    assert "闸阀DN50" in html


def test_product_suggestions_api_matches_pinyin():
    _seed_products()
    response = create_app().test_client().get("/products/api/suggestions?q=mhq")
    assert response.is_json
    assert "灭火器4kg" in response.get_json()


def test_product_suggestions_still_match_chinese_substring():
    _seed_products()
    response = create_app().test_client().get("/products/api/suggestions?q=闸阀")
    assert "闸阀DN50" in response.get_json()
