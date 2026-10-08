"""Batch D：往来对账入口 + 死路由清理（2026-10-08 第二轮修复）。

覆盖审查证据 #8/#9：
- 账款管理页选中客户后出现「往来对账」按钮，携带客户与日期、且带 from=accounts
- 点击直达结果（自动建快照，快照行出现），返回按钮指向账款管理并携带原筛选
- 默认（无 from）返回数据分析
- 死路由 accounts.bulk_delete_orders 已删除（不再静默软删绕过自动冲回）
"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product


def _seed(spec="D", price=3000, when="2026-10-07"):
    pid = create_product("对账入口商品", spec, "个", price)
    cid = create_customer("对账入口客户", opening_balance_cents=0)
    create_order_from_typed_rows(
        cid, "MD202610070901",
        [{"product_id": pid, "product_name": "对账入口商品", "unit": "个",
          "unit_price_yuan": "50", "quantity": "2"}],
        status="saved", order_date=when,
    )
    return pid, cid


def test_accounts_page_has_reconciliation_entry_with_params():
    init_db()
    _, cid = _seed("ENTRY")
    html = create_app().test_client().get(
        f"/accounts/?customer_id={cid}&start_date=2026-10-01&end_date=2026-10-31"
    ).get_data(as_text=True)
    assert 'id="reconEntryLink"' in html
    assert "往来对账" in html
    assert f"customer_id={cid}" in html
    assert "from=accounts" in html


def test_reconciliation_entry_lands_on_results_and_returns_to_accounts():
    init_db()
    _, cid = _seed("LAND")
    client = create_app().test_client()
    response = client.get(
        f"/analytics/reconciliation?customer_id={cid}&start_date=2026-10-01&end_date=2026-10-31&from=accounts"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # 直达结果：快照已建，出现对账合计与单号。
    assert "交易对账净额" in html
    assert "MD202610070901" in html
    # 返回按钮切到账款管理并携带原客户与日期。
    assert "返回账款管理" in html
    assert f"/accounts/?customer_id={cid}" in html
    assert "start_date=2026-10-01" in html
    # 快照确实写入了库。
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM reconciliation_snapshots").fetchone()[0]
    assert count >= 1


def test_reconciliation_default_back_is_analytics():
    init_db()
    _, cid = _seed("DEFAULT")
    html = create_app().test_client().get(
        f"/analytics/reconciliation?customer_id={cid}"
    ).get_data(as_text=True)
    assert "返回数据分析" in html
    assert "返回账款管理" not in html


def test_accounts_bulk_delete_orders_dead_route_is_removed():
    init_db()
    client = create_app().test_client()
    # 旧死路由删除后，POST 落到全局 404 中文页，不再静默软删。
    response = client.post("/accounts/bulk_delete_orders", data={"ids": "1"})
    assert response.status_code == 404
