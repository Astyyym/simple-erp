"""Phase 3 S4：客户匹配点加拼音（8 处）；2026-10-14 起筛选条件也按拼音匹配。

计划 §13.2 S4：`customers.py:65/:79/:222`、`orders.py:229/:455/:1332`、`purchases.py:617`、
`pdf.py:350` 全部要能按拼音首字母命中。

**决策变更（2026-10-14 用户答复「连筛选框也一起按全拼匹配」）**：原先「三处纯筛选条件
（`orders.py` 的 `order_side_conditions` / `purchase_side_conditions` 与导出明细）保持按名称匹配」，
现改为同样走 `name_match_clause()`（名称 + 首字母 + 全拼）。

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


def test_filter_conditions_match_pinyin_too():
    """决策变更（2026-10-14 用户答复）：「连筛选框也一起按全拼匹配」。

    原先这三处是「纯筛选条件、保持按名称匹配」（用户在筛选框输完整名称）；
    用户明确要求筛选框也按拼音匹配后，改走 `name_match_clause()`（名称 + 首字母 + 全拼）。
    这里按真实入口断言：单据管理筛选框输首字母与完整拼音都能筛出该客户的单据。
    """
    _seed_customers()
    from erp.services.accounting import create_order_from_typed_rows, create_product

    customer_id = get_db_customer_id("消防器材张老板")
    product_id = create_product("筛选拼音商品", "个")
    create_order_from_typed_rows(
        customer_id,
        "MD202610140009",
        [{"product_id": product_id, "product_name": "筛选拼音商品", "unit": "个",
          "quantity": "1", "unit_price_yuan": "10"}],
        status="saved",
        order_date="2026-10-14",
    )
    client = create_app().test_client()
    for query in ("xfqcz", "xiaofangqicai"):
        html = client.get(f"/orders/?customer={query}").get_data(as_text=True)
        assert "MD202610140009" in html, query

    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "erp" / "routes" / "orders.py").read_text(encoding="utf-8")
    order_side = source.split("def order_side_conditions")[1].split("def purchase_side_conditions")[0]
    purchase_side = source.split("def purchase_side_conditions")[1].split("order_where, order_params")[0]
    assert "name_match_clause" in order_side, "销售/退货侧筛选条件没接拼音"
    assert "name_match_clause" in purchase_side, "拿货/退拿货侧筛选条件没接拼音"


def get_db_customer_id(name: str) -> int:
    with get_db() as conn:
        return int(conn.execute("SELECT id FROM customers WHERE name=?", (name,)).fetchone()["id"])
