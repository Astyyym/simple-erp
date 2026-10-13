"""Phase 4 / S4 — 四页开单模板：单位可编辑 + 候选池渲染 + 提交 name="unit"。

决策（计划 §4.3/§4.4）：四页单位控件可编辑；服务端渲染候选池（默认三项 ∪ 库内单位）。
"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product

ENTRY_PAGES = ("/orders/new", "/orders/return/new", "/purchases/new", "/purchases/return/new")


def test_all_four_entry_pages_have_editable_unit_control():
    """四页都必须有 unit 输入 + unit-select 下拉（不再只读）。"""
    init_db()
    client = create_app().test_client()
    for path in ENTRY_PAGES:
        html = client.get(path).get_data(as_text=True)
        assert 'name="unit"' in html, f"{path} 缺少可提交的 unit 字段"
        assert "unit-select" in html, f"{path} 缺少单位下拉"
        assert "unit-cell" in html, f"{path} 缺少单位单元格容器"


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
        for unit in ("个", "套", "件", "桶"):
            assert f'<option value="{unit}"' in html, (path, unit)


def test_sales_page_unit_options_come_from_pool_not_hardcoded():
    """销售/退货页原先把默认三项硬编码；改为读取候选池后，库内单位必须出现。"""
    init_db()
    create_product("销售候选池商品", "打", 1000)
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert '<option value="打"' in html
