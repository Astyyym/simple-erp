"""Phase 4 / S1 — 缺陷 A：选中已有商品时提交的单位被静默丢弃（销售/退货）。

决策（计划 §4.1 + §六 #14）：选中与手输两条路径**都以提交的 unit 为准**，
同时写明细 + 同步 `products.unit`。

RED 预期：当前命中路径返回 `product["unit"]`，提交的「箱」被丢弃。
"""
import pytest

from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_product,
    create_order_from_typed_rows,
    update_order_from_typed_rows,
)


def _seed(product_name="缺陷A商品", unit="个", price=5000):
    init_db()
    pid = create_product(product_name, unit)
    cid = create_customer("缺陷A客户")
    return cid, pid


def test_selected_product_uses_submitted_unit_and_syncs_archive():
    """选中已有商品（带 product_id）提交不同 unit → 明细与档案都取提交值。"""
    cid, pid = _seed()
    create_order_from_typed_rows(
        cid, "MD202610130001",
        [{"product_id": str(pid), "product_name": "缺陷A商品", "unit": "箱",
          "unit_price_yuan": "50", "quantity": "2"}],
        status="saved", order_date="2026-10-13",
    )
    with get_db() as conn:
        item = conn.execute("SELECT unit FROM order_items ORDER BY id DESC LIMIT 1").fetchone()
        product = conn.execute("SELECT unit FROM products WHERE id=?", (pid,)).fetchone()
    assert item["unit"] == "箱", "选中路径必须采纳提交的单位"
    assert product["unit"] == "箱", "选中路径必须同步商品档案单位"


def test_selected_vs_typed_paths_yield_identical_unit():
    """同一商品走「命中」与「手输」两条路提交同一 unit，落库 unit 逐字段相同。"""
    cid, pid = _seed("两条路径商品", "个", 5000)

    create_order_from_typed_rows(
        cid, "MD202610130010",
        [{"product_id": str(pid), "product_name": "两条路径商品", "unit": "件",
          "unit_price_yuan": "50", "quantity": "1"}],
        status="saved", order_date="2026-10-13",
    )
    with get_db() as conn:
        hit_unit = conn.execute("SELECT unit FROM order_items ORDER BY id DESC LIMIT 1").fetchone()["unit"]

    # 手输路径：无 product_id，靠名称命中同一商品
    create_order_from_typed_rows(
        cid, "MD202610130011",
        [{"product_name": "两条路径商品", "unit": "件",
          "unit_price_yuan": "50", "quantity": "1"}],
        status="saved", order_date="2026-10-13",
    )
    with get_db() as conn:
        typed_unit = conn.execute("SELECT unit FROM order_items ORDER BY id DESC LIMIT 1").fetchone()["unit"]

    assert hit_unit == "件"
    assert typed_unit == "件"
    assert hit_unit == typed_unit, "两条路径的单位落库行为必须一致"


def test_selected_product_empty_unit_falls_back_to_archive():
    """提交单位为空 → 回落商品档案单位（不写空串）。"""
    cid, pid = _seed("空单位商品", "套", 3000)
    create_order_from_typed_rows(
        cid, "MD202610130020",
        [{"product_id": str(pid), "product_name": "空单位商品", "unit": "",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date="2026-10-13",
    )
    with get_db() as conn:
        item = conn.execute("SELECT unit FROM order_items ORDER BY id DESC LIMIT 1").fetchone()
    assert item["unit"] == "套"


def test_update_order_from_typed_rows_also_honors_submitted_unit():
    """重编辑路径（update_order_from_typed_rows）同样以提交 unit 为准。"""
    cid, pid = _seed("重编辑单位商品", "个", 5000)
    order_id = create_order_from_typed_rows(
        cid, "MD202610130030",
        [{"product_id": str(pid), "product_name": "重编辑单位商品", "unit": "个",
          "unit_price_yuan": "50", "quantity": "1"}],
        status="draft", order_date="2026-10-13",
    )
    update_order_from_typed_rows(
        order_id, cid, "MD202610130030",
        [{"product_id": str(pid), "product_name": "重编辑单位商品", "unit": "台",
          "unit_price_yuan": "50", "quantity": "1"}],
        status="draft", order_date="2026-10-13",
    )
    with get_db() as conn:
        item = conn.execute("SELECT unit FROM order_items WHERE order_id=?", (order_id,)).fetchone()
        product = conn.execute("SELECT unit FROM products WHERE id=?", (pid,)).fetchone()
    assert item["unit"] == "台"
    assert product["unit"] == "台"


def test_product_archive_unit_change_rescales_nothing():
    """改单位只改 unit 字段，不做任何数量/金额换算（§4.6）。

    旧断言读已删除的库存状态表的数量/成本两列——
    库存与成本系统已整体删除（2026-10-16 决策 ②/③），改为钉住仍然成立的那条：
    换单位（重编辑）只改 unit，明细的数量、单价、小计逐行不变，档案单位同步。
    """
    cid, pid = _seed("换单位商品", "个", 5000)

    order_id = create_order_from_typed_rows(
        cid, "MD202610130040",
        [{"product_id": str(pid), "product_name": "换单位商品", "unit": "个",
          "unit_price_yuan": "50", "quantity": "2"}],
        status="saved", order_date="2026-10-13",
    )
    with get_db() as conn:
        before = conn.execute(
            "SELECT unit, quantity, unit_price_cents, subtotal_cents FROM order_items WHERE order_id=?",
            (order_id,),
        ).fetchone()

    update_order_from_typed_rows(
        order_id, cid, "MD202610130040",
        [{"product_id": str(pid), "product_name": "换单位商品", "unit": "箱",
          "unit_price_yuan": "50", "quantity": "2"}],
        status="saved", order_date="2026-10-13",
    )
    with get_db() as conn:
        after = conn.execute(
            "SELECT unit, quantity, unit_price_cents, subtotal_cents FROM order_items WHERE order_id=?",
            (order_id,),
        ).fetchone()
        product_unit = conn.execute("SELECT unit FROM products WHERE id=?", (pid,)).fetchone()["unit"]

    assert after["unit"] == "箱" and before["unit"] == "个", "只有单位变了"
    assert after["quantity"] == before["quantity"]
    assert after["unit_price_cents"] == before["unit_price_cents"]
    assert after["subtotal_cents"] == before["subtotal_cents"]
    assert product_unit == "箱", "换单位同步商品档案"
