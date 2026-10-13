"""Phase 3 S4：客户匹配点加拼音（8 处）与「三处纯筛选条件保持不动」。

计划 §13.2 S4：`customers.py:65/:79/:222`、`orders.py:229/:455/:1332`、`purchases.py:617`、
`pdf.py:350` 全部要能按拼音首字母命中；`orders.py:350/:361/:639` 三处**仅作筛选条件**，
保持现状（用户是在筛选框输完整名称，不是联想选词）。

本文件按「真实入口 → 真的能搜到」逐个断言，不只看 SQL 文本。
"""
from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer


def _seed_customers():
    init_db()
    create_customer("陈紫坤测试客户")
    create_customer("消防器材张老板")


def _json_names(response):
    payload = response.get_json()
    return [row["name"] if isinstance(row, dict) else row for row in payload]


def test_customer_list_search_matches_pinyin():
    """customers.py:65 列表搜索。"""
    _seed_customers()
    html = create_app().test_client().get("/customers/?q=czk").get_data(as_text=True)
    assert "陈紫坤测试客户" in html
    assert "消防器材张老板" not in html


def test_customer_list_suggestions_match_pinyin():
    """customers.py:79 列表 suggestions。"""
    _seed_customers()
    html = create_app().test_client().get("/customers/?q=xfqcz").get_data(as_text=True)
    assert "消防器材张老板" in html


def test_customer_suggestions_api_matches_pinyin():
    """customers.py:222 API。"""
    _seed_customers()
    response = create_app().test_client().get("/customers/api/suggestions?q=czk")
    assert response.is_json
    assert "陈紫坤测试客户" in response.get_json()


def test_orders_api_customers_matches_pinyin():
    """orders.py:1332 开单页客户模糊匹配 API。"""
    _seed_customers()
    response = create_app().test_client().get("/orders/api/customers?q=xfqcz")
    assert response.is_json
    names = [row["name"] for row in response.get_json()]
    assert "消防器材张老板" in names


def test_order_list_customer_suggestions_api_matches_pinyin():
    """orders.py:455 单据管理客户建议源（Phase 3 S6 起由前端调用 /orders/api/customers）。

    决策变更（Phase 3 S6）：单据管理页联想改自有 popover，名称不再服务端渲染；
    拼音能力由该 API 提供，故断 API 命中 + 页面确实接线到该 API。
    """
    _seed_customers()
    client = create_app().test_client()
    response = client.get("/orders/api/customers?q=xfqcz")
    assert response.is_json
    names = [row["name"] for row in response.get_json()]
    assert "消防器材张老板" in names
    page = client.get("/orders/").get_data(as_text=True)
    assert "/orders/api/customers?q=" in page


def test_purchases_api_objects_matches_pinyin():
    """purchases.py:617 拿货页对象联想 API。"""
    _seed_customers()
    response = create_app().test_client().get("/purchases/api/objects?q=xfqcz")
    assert response.is_json
    names = [row["name"] for row in response.get_json()]
    assert "消防器材张老板" in names


def test_analytics_customer_scope_matches_pinyin():
    """orders.py:229 分析页客户作用域匹配（_resolve_customer_scope）。"""
    _seed_customers()
    html = create_app().test_client().get("/analytics/?customer=xfqcz").get_data(as_text=True)
    assert "消防器材张老板" in html


def test_summary_pdf_customer_scope_matches_pinyin():
    """pdf.py:350 客户货款汇总按客户名模糊查找。"""
    from erp.utils.pdf import resolve_summary_customer_ids

    _seed_customers()
    with get_db() as conn:
        expected = conn.execute("SELECT id FROM customers WHERE name='陈紫坤测试客户'").fetchone()["id"]
    assert resolve_summary_customer_ids("czk", "", "") == [int(expected)]


def test_filter_only_conditions_stay_name_based():
    """三处纯筛选条件（orders.py:350/:361/:639）保持现状，不被误扩大为拼音查询。

    `_resolve_customer_scope` 命中拼音后走 customer_id 作用域，这三处只是
    「未能唯一解析客户」时的回落条件 —— 保持按名称匹配，避免改变筛选语义。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "erp" / "routes" / "orders.py").read_text(encoding="utf-8")
    assert 'conditions.append("c.name LIKE ?")' in source
    assert 'conditions.append("customer_name LIKE ?")' in source
    assert "c.pinyin_initials" not in source.split("def order_side_conditions")[1].split("def purchase_side_conditions")[0]
