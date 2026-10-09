"""Batch C：期初承接语义（成本来源 / 总金额反推 / 日期与单位提示）。"""

from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_product
from erp.services.inventory import initialize_product


def _state(product_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()


def _init(product_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM inventory_initializations WHERE product_id=?", (product_id,)).fetchone()


def test_known_cost_source_is_recorded():
    init_db()
    pid = create_product("已知成本商品", "A", "个", 1000)
    initialize_product(pid, "10", "8", "2026-10-01", "期初", "c-known", cost_source="known")
    assert _init(pid)["cost_source"] == "known"
    assert _state(pid)["quantity_3dp"] == 10000


def test_estimated_cost_source_is_recorded_and_auditable():
    init_db()
    pid = create_product("估算成本商品", "A", "个", 1000)
    initialize_product(pid, "5", "6", "2026-10-01", "期初", "c-est", cost_source="estimated")
    row = _init(pid)
    assert row["cost_source"] == "estimated"
    assert _state(pid)["cost_total_micro"] == 5 * 6 * 1_000_000


def test_zero_quantity_forces_zero_cost_source():
    init_db()
    pid = create_product("零成本商品", "A", "个", 1000)
    initialize_product(pid, "0", "", "2026-10-01", "期初", "c-zero", confirm_zero=True)
    row = _init(pid)
    assert row["cost_source"] == "zero"
    assert _state(pid)["cost_total_micro"] == 0


def test_total_cost_backs_out_unit_cost():
    init_db()
    pid = create_product("总金额商品", "A", "个", 1000)
    # 1000 元 / 8 件 = 125 元/件
    initialize_product(pid, "8", "", "2026-10-01", "期初", "c-total", cost_source="known", total_cost="1000")
    state = _state(pid)
    assert state["quantity_3dp"] == 8000
    assert state["cost_total_micro"] == 1000 * 1_000_000
    assert state["avg_cost_micro"] == 125 * 1_000_000


def test_quantity_without_cost_is_rejected():
    init_db()
    pid = create_product("缺成本商品", "A", "个", 1000)
    try:
        initialize_product(pid, "5", "", "2026-10-01", "期初", "c-missing")
        assert False, "有数量缺成本必须报错"
    except ValueError as exc:
        assert "成本" in str(exc)


def test_invalid_cost_source_rejected():
    init_db()
    pid = create_product("来源无效商品", "A", "个", 1000)
    try:
        initialize_product(pid, "5", "1", "2026-10-01", "期初", "c-bad", cost_source="guess")
        assert False
    except ValueError as exc:
        assert "成本来源" in str(exc)


def test_edit_page_exposes_cost_source_and_total_cost_and_hints():
    init_db()
    pid = create_product("界面期初商品", "A", "个", 1000)
    client = create_app().test_client()
    html = client.get(f"/products/{pid}/edit").get_data(as_text=True)
    assert 'name="cost_source"' in html
    assert 'name="total_cost"' in html
    assert "已知成本" in html and "用户确认估算" in html
    # C-3/C-4 提示
    assert "不得早于期初日期" in html
    assert "一个商品只有一个单位" in html


def test_web_initialize_with_estimated_source_records_it():
    init_db()
    pid = create_product("网页估算商品", "A", "个", 1000)
    client = create_app().test_client()
    resp = client.post(
        f"/products/{pid}/inventory/initialize",
        data={
            "quantity": "4",
            "unit_cost": "7",
            "business_date": "2026-10-01",
            "source": "迁移期初",
            "cost_source": "estimated",
            "request_key": "web-c-est",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert _init(pid)["cost_source"] == "estimated"
    assert "用户确认估算" in resp.get_data(as_text=True)
