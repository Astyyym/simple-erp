"""账款项：拿货/退拿货并入客户视图与单据类型筛选。"""
from erp.db import get_db, init_db
from erp.services import accounting
from erp.services.inventory import create_purchase_order, initialize_product
from erp.services import purchase_returns


def _seed():
    init_db()
    cid = accounting.create_customer("并入拿货客户")
    pid = accounting.create_product("并入商品", "T1", "个", 1000)
    initialize_product(pid, "20", "10", "2026-10-01", "INTERNAL-INIT", "ledger-merge-init")
    accounting.create_order_from_typed_rows(
        cid, "MD202610010001",
        [{"product_id": pid, "product_name": "并入商品", "spec": "T1", "unit": "个",
          "quantity": "2", "unit_price_yuan": "100"}],
        status="saved", order_date="2026-10-01",
    )
    purchase = create_purchase_order(
        "NH202610010001", "2026-10-01",
        [{"product_id": pid, "quantity": "3", "unit_cost_yuan": "40"}],
        customer_id=cid, request_key="ledger-merge-purchase",
    )["order_id"]
    with get_db() as conn:
        purchase_item = conn.execute(
            "SELECT id FROM purchase_order_items WHERE purchase_order_id=?", (purchase,)
        ).fetchone()[0]
    pr = purchase_returns.create(
        customer_id=cid, source_order_id=purchase, business_date="2026-10-01",
        items=[{"source_item_id": purchase_item, "quantity": "1"}],
        request_key="ledger-merge-return",
    )["order_id"]
    return cid, purchase, pr


def test_ledger_includes_purchases_and_nets_balance():
    cid, purchase, pr = _seed()
    ledger = accounting.get_customer_account_ledger(cid)

    types = [entry["type"] for entry in ledger["entries"]]
    assert "purchase" in types and "purchase_return" in types
    assert ledger["current_balance_cents"] == 20000 - 12000 + 4000
    assert ledger["purchase_net_cents"] == -12000 + 4000
    # 余额与 service 口径一致
    assert accounting.customer_balance_cents(cid) == ledger["current_balance_cents"]


def test_type_filter_limits_rows_without_changing_balance():
    cid, purchase, pr = _seed()
    full = accounting.get_customer_account_ledger(cid)
    filtered = accounting.get_customer_account_ledger(cid, type_filter=["purchase", "purchase_return"])

    assert {entry["type"] for entry in filtered["entries"]} == {"purchase", "purchase_return"}
    assert filtered["current_balance_cents"] == full["current_balance_cents"]
    assert filtered["period"]["net_change_cents"] == full["period"]["net_change_cents"]


def test_accounts_page_type_filter_query_renders_purchase_rows():
    from erp import create_app
    cid, purchase, pr = _seed()
    client = create_app().test_client()

    html = client.get(f"/accounts/?customer_id={cid}&type=purchase&type=purchase_return").get_data(as_text=True)
    assert "拿货单" in html and "退拿货单" in html
    assert 'name="type"' in html

    only_sale = client.get(f"/accounts/?customer_id={cid}&type=sale").get_data(as_text=True)
    # 只筛销售时不应出现拿货摘要行
    assert "拿货退拿货净额" in only_sale  # 汇总卡仍在
