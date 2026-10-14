"""Phase 4 / S4 — 四页开单模板：单位可编辑 + 候选池渲染 + 提交 name="unit"。

决策（计划 §4.3/§4.4，2026-10-13 P2 修订）：单位列由「select + input 双控件」改为单控件
= 输入框（name="unit"）+ 候选浮层；候选池仍由服务端渲染（默认三项 ∪ 库内单位），
以 `data-unit-options` 交给共享组件 `ErpOrderEntry.bindUnitCell`。
"""
import re

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product

ENTRY_PAGES = ("/orders/new", "/orders/return/new", "/purchases/new", "/purchases/return/new")

UNIT_POOL_RE = re.compile(r'data-unit-options="([^"]*)"')


def _unit_pool(html):
    """取出模板渲染的单位候选池（'|' 分隔）。"""
    match = UNIT_POOL_RE.search(html)
    return match.group(1) if match else ""


def test_all_four_entry_pages_have_single_unit_control():
    """四页单位列都是单控件：可提交的 unit 输入框 + 候选浮层，不再有并排 select。"""
    init_db()
    client = create_app().test_client()
    for path in ENTRY_PAGES:
        html = client.get(path).get_data(as_text=True)
        assert 'name="unit"' in html, f"{path} 缺少可提交的 unit 字段"
        assert "unit-cell" in html, f"{path} 缺少单位单元格容器"
        assert "unit-suggestions" in html, f"{path} 缺少单位候选浮层"
        assert "unit-select" not in html, f"{path} 仍是 select + input 双控件"
        assert "data-unit-options" in html, f"{path} 缺少服务端渲染的单位候选池"


def test_purchase_pages_no_longer_use_readonly_unit_display():
    """拿货/退拿货页的单位列不再是只读 div（转为可编辑）。"""
    init_db()
    client = create_app().test_client()
    for path in ("/purchases/new", "/purchases/return/new"):
        html = client.get(path).get_data(as_text=True)
        assert 'class="readonly-total unit-display"' not in html, f"{path} 单位仍只读"


def test_unit_options_rendered_from_candidate_pool():
    """四页渲染的候选池包含库内已有单位（不止默认三项）。"""
    init_db()
    create_product("候选池渲染商品", "桶", 1000)
    client = create_app().test_client()
    for path in ENTRY_PAGES:
        html = client.get(path).get_data(as_text=True)
        pool = _unit_pool(html)
        assert pool, f"{path} 没有渲染单位候选池"
        for unit in ("个", "套", "件", "桶"):
            assert unit in pool.split("|"), (path, unit, pool)


def test_sales_page_unit_options_come_from_pool_not_hardcoded():
    """销售/退货页原先把默认三项硬编码；改为读取候选池后，库内单位必须出现。"""
    init_db()
    create_product("销售候选池商品", "打", 1000)
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert "打" in _unit_pool(html).split("|")
