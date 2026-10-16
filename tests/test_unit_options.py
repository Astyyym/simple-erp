"""Phase 4 / S3 — 单位候选池（零迁移）。

决策（计划 §4.4）：候选池 = 默认三项 ∪ 四表 DISTINCT unit。
不新建表、不升 schema 版本。四页服务端渲染候选 + 本页输入即时追加。
"""
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows
from erp.services.inventory import create_purchase_order, initialize_product
from erp.services.units import DEFAULT_UNITS, unit_options


def test_default_units_constant():
    assert DEFAULT_UNITS == ("个", "套", "件")


def test_unit_options_empty_library_returns_defaults():
    init_db()
    assert unit_options() == ["个", "套", "件"]


def test_unit_options_collects_from_all_four_tables():
    init_db()
    cid = create_customer("候选池客户")
    pid = create_product("候选池商品", "箱")
    initialize_product(pid, "10", "20", "2026-10-01", "期初", "p4-units-init")

    # products.unit = 箱（已由 create_product 写入）
    # order_items.unit = 台（销售单提交）
    create_order_from_typed_rows(
        cid, "MD202610130100",
        [{"product_id": str(pid), "product_name": "候选池商品", "unit": "台",
          "unit_price_yuan": "20", "quantity": "1"}],
        status="saved", order_date="2026-10-13",
    )
    # purchase_order_items.unit = 桶（拿货提交）
    create_purchase_order(
        "NH202610130100", "2026-10-13",
        [{"product_id": pid, "quantity": "1", "unit_cost_yuan": "20", "unit": "桶"}],
        customer_id=cid, request_key="p4-units-po", status="saved",
    )
    # products 初值「箱」，但销售单提交「台」后 §4.1 会把档案同步为「台」→「箱」不再存在
    # order_items.unit = 台；purchase_order_items.unit = 桶
    options = unit_options()
    for expected in ("个", "套", "件", "台", "桶"):
        assert expected in options, (expected, options)
    assert options[:3] == ["个", "套", "件"], "默认三项必须在前且保持声明顺序"


def test_unit_options_sorted_and_deduped():
    init_db()
    from erp.services.accounting import create_product as cp
    cp("排序商品A", "个")
    cp("排序商品B", "个")
    options = unit_options()
    assert options.count("个") == 1, "重复单位必须去重"
    assert options[:3] == ["个", "套", "件"], "默认三项在前且保持声明顺序"
