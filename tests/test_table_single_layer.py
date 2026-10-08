"""Batch G：表格容器叠加修复（单层容器原则，2026-10-08 第二轮修复）。

证据 #21：`.table-responsive`/`.table-shell` 与 `table.table:not(.order-table)` 各自都带
一套玻璃，凡「容器 > 表格」结构即双层叠加，暗色下浑浊。

本文件只锁「实现契约」（CSS 规则存在、裸表格未被误伤、类名/结构不变）；
真实视觉验收（逐页探针复扫 blur 深度 + 浅色/暗色截图）走浏览器，不在此文件替代。
"""
from erp import create_app
from erp.db import init_db

# 必须去玻璃的「容器内表格」上下文。
CONTAINER_TABLE_SELECTORS = [
    ".table-responsive>table.table:not(.order-table)",
    ".table-shell>table.table:not(.order-table)",
    ".card table.table:not(.order-table)",
    ".today-history table.table:not(.order-table)",
    ".source-preview table.table:not(.order-table)",
    ".order-details-card table.table:not(.order-table)",
    ".analytics-panel table.table",
    ".recon-panel table.table",
    ".recycle-card table.table",
]

# 玻璃卡片内的中间容器：透明 + 1px 素边框。
MID_CONTAINER_SELECTORS = [
    ".today-history .table-responsive",
    ".source-preview .table-responsive",
    ".order-details-card .table-responsive",
    ".analytics-panel .table-responsive",
    ".analytics-panel .table-shell",
    ".recon-panel .table-responsive",
    ".recycle-card .table-responsive",
    # 玻璃卡片内的玻璃子卡（.source-preview 嵌在 .order-meta 内）：扁平化。
    ".order-meta .source-preview",
]


def _base_html():
    init_db()
    return create_app().test_client().get("/orders/new").get_data(as_text=True)


def test_single_layer_rules_present_for_container_tables():
    html = _base_html()
    for selector in CONTAINER_TABLE_SELECTORS:
        assert selector in html, f"缺少单层规则：{selector}"


def test_mid_container_becomes_flat_border():
    html = _base_html()
    for selector in MID_CONTAINER_SELECTORS:
        assert selector in html, f"缺少中间容器扁平化规则：{selector}"


def test_bare_table_glass_rule_is_not_globally_removed():
    """裸表格（单据列表/客户列表/单据详情）必须保持卡片外观 → 全局玻璃规则仍在。"""
    html = _base_html()
    assert "table.table:not(.order-table){" in html
    # 全局玻璃规则里仍带 backdrop-filter（未被去玻璃规则覆盖成 none）。
    marker = "table.table:not(.order-table){"
    block = html[html.index(marker): html.index(marker) + 400]
    assert "backdrop-filter:blur" in block


def test_container_internal_tables_declared_transparent():
    html = _base_html()
    # 去玻璃块必须把背景置为透明（而不是又一层实色/玻璃）。
    marker = ".table-responsive>table.table:not(.order-table),"
    block = html[html.index(marker): html.index(marker) + 1200]
    assert "background:transparent" in block
    assert "backdrop-filter:none" in block


def test_pages_still_render_expected_container_classes():
    """只改 CSS → 验收要求这些关键容器类名/结构保持不变。"""
    init_db()
    client = create_app().test_client()
    recycle_html = client.get("/recycle/").get_data(as_text=True)
    assert "recycle-scroll" in recycle_html
    orders_new = client.get("/orders/new").get_data(as_text=True)
    assert "today-history" in orders_new
    # 开单明细表仍是被排除在玻璃规则外的 .order-table。
    assert "order-table" in orders_new
