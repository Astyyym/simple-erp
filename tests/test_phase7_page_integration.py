from erp import create_app
from erp.config import save_config
from erp.db import init_db


def test_phase7_keeps_legacy_purchase_stats_on_orders_and_new_analytics_separate():
    init_db()
    client = create_app().test_client()

    orders_html = client.get("/orders/").get_data(as_text=True)
    analytics_html = client.get("/analytics/").get_data(as_text=True)

    assert "单据管理" in orders_html
    # 全店拿货统计已迁出单据管理，改由数据分析页的流水热力图承担。
    assert "全店拿货统计" not in orders_html
    assert "数据分析中心" not in orders_html

    assert "数据分析中心" in analytics_html
    # 往来对账入口已从数据分析页移除，改由账款管理的单据类型筛选承担
    assert "往来对账" not in analytics_html
    assert 'id="openRecon"' not in analytics_html
    assert "客户采购统计" not in analytics_html
    assert "全店拿货统计" not in analytics_html
    assert "流水热力图" in analytics_html


def test_phase7_shared_navigation_exposes_each_business_area_without_new_duplicate_entry():
    init_db()
    html = create_app().test_client().get("/").get_data(as_text=True)

    assert html.count('href="/orders/">单据管理</a>') == 1
    assert html.count('href="/accounts/">账款管理</a>') == 1
    assert html.count('href="/analytics/">数据分析</a>') == 1
    assert html.count('href="/products/">商品管理</a>') == 1
    assert html.count('href="/customers/">客户管理</a>') == 1


def test_phase7_orders_page_has_explicit_empty_state_for_empty_result():
    init_db()
    html = create_app().test_client().get(
        "/orders/",
        query_string={"date_mode": "range", "start_date": "2026-01-01", "end_date": "2026-01-02"},
    ).get_data(as_text=True)

    assert "当前筛选范围暂无单据" in html


def test_phase7_integrated_pages_keep_theme_contract_across_supported_modes():
    init_db()
    client = create_app().test_client()
    paths = "/", "/orders/", "/products/", "/customers/", "/purchases/new", "/analytics/", "/accounts/", "/recycle/", "/settings/"

    for theme in ("light", "dark", "system"):
        save_config({"ui_theme": theme})
        for path in paths:
            response = client.get(path)
            html = response.get_data(as_text=True)
            assert response.status_code == 200, path
            assert f'data-ui-theme="{theme}"' in html, (theme, path)

    home = client.get("/").get_data(as_text=True)
    assert "@media(max-width:760px)" in home
    assert "overflow-x:hidden;overflow-y:auto" in home