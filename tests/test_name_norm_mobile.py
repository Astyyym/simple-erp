"""手机端（第二监听 5001）的搜索必须与桌面端同步认「归一副本 + 全拼」。

2026-10-20：桌面端修了「带括号/空格/全角商品名点选后专属价带不出来」，
根因是 `name` 列未归一、拼音列按原始名称生成。手机端走的是另一套入口
（`create_mobile_app()` 只挂 `mobile_bp`，且**不调 init_db()**），
所以这里单独断言一遍，避免以后只在桌面端改动、手机端悄悄退化。

注意：手机端有设备门（未配对 302 到配对页），测试必须走真实配对流程拿 cookie，
否则每个请求都是 404/302，看起来像「搜不到」其实是没进门。
"""
from __future__ import annotations

from urllib.parse import quote

import pytest

from erp.db import init_db
from erp.mobile_app import create_mobile_app
from erp.services import mobile_pairing
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
)
from erp.utils.pinyin import pinyin_full

AWKWARD_PRODUCT = "PVC管（厚）"
AWKWARD_CUSTOMER = "星辰宾馆（总店）"


@pytest.fixture()
def paired_client():
    """走真实配对流程：生成配对码 → 校验 → 把 cookie 装进测试客户端。"""
    init_db()
    code, _expires = mobile_pairing.generate_pair_code()
    token, _message = mobile_pairing.verify_pair_code(code, label="pytest", ip="127.0.0.1")
    app = create_mobile_app()
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie(mobile_pairing.COOKIE_NAME, token)
    return client


@pytest.fixture()
def seeded(paired_client):
    customer_id = create_customer(AWKWARD_CUSTOMER)
    create_product(AWKWARD_PRODUCT, "个")
    create_order_from_typed_rows(
        customer_id,
        "MB0001",
        [{"product_name": AWKWARD_PRODUCT, "unit": "个", "unit_price_yuan": "77", "quantity": "1"}],
        status="saved",
    )
    return paired_client


def test_mobile_customer_search_accepts_full_pinyin(seeded):
    html = seeded.get("/m/customers?q=" + quote(pinyin_full(AWKWARD_CUSTOMER))).get_data(as_text=True)
    assert AWKWARD_CUSTOMER in html


def test_mobile_customer_search_accepts_initials_and_chinese(seeded):
    for query in ("xc", "星辰"):
        html = seeded.get("/m/customers?q=" + quote(query)).get_data(as_text=True)
        assert AWKWARD_CUSTOMER in html, query


def test_mobile_product_search_accepts_full_pinyin(seeded):
    html = seeded.get("/m/products?q=" + quote(pinyin_full(AWKWARD_PRODUCT))).get_data(as_text=True)
    assert AWKWARD_PRODUCT in html


def test_mobile_product_search_accepts_fullwidth_name(seeded):
    """用户会直接粘完整品名（含全角括号）来搜，这一条以前必然搜不到。"""
    html = seeded.get("/m/products?q=" + quote(AWKWARD_PRODUCT)).get_data(as_text=True)
    assert AWKWARD_PRODUCT in html


def test_mobile_document_list_still_works(seeded):
    html = seeded.get("/m/orders").get_data(as_text=True)
    assert "MB0001" in html
