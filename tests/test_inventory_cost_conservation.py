"""Exact transaction costs and reversible inventory, isolated SQLite only."""
import pytest

from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows, void_order
from erp.services.inventory import create_purchase_order, finalize_purchase_order, initialize_product, update_purchase_draft, void_purchase_order


def _seed(quantity='0', cost='0'):
    init_db()
    cid = create_customer('成本守恒虚构对象')
    pid = create_product('成本守恒商品', 'S', '米', 2000)
    initialize_product(pid, quantity, cost, '2026-10-02', '虚构期初', 'cost-init', confirm_zero=quantity == '0')
    return cid, pid


def _state_and_postings(pid):
    with get_db() as conn:
        state = conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state WHERE product_id=?', (pid,)).fetchone()
        totals = conn.execute('SELECT SUM(quantity_delta_3dp),SUM(cost_delta_micro) FROM inventory_postings WHERE product_id=?', (pid,)).fetchone()
    assert tuple(state) == tuple(totals), 'Stored state and exact posting totals must agree'
    return tuple(state)


@pytest.mark.parametrize('path', ['saved', 'draft', 'edit'])
def test_purchase_inbound_cost_is_actual_rounded_line_amount(path):
    cid, pid = _seed()
    rows = [{'product_id': pid, 'quantity': '0.500', 'unit_cost_yuan': '0.01'}]
    result = create_purchase_order('NH202610020001', '2026-10-02', rows, customer_id=cid, request_key='fractional-inbound', status='saved' if path == 'saved' else 'draft')
    if path == 'draft':
        finalize_purchase_order(result['order_id'])
    elif path == 'edit':
        update_purchase_draft(result['order_id'], '2026-10-02', rows, customer_id=cid, status='saved')
    with get_db() as conn:
        item = conn.execute('SELECT subtotal_cents FROM purchase_order_items WHERE purchase_order_id=?', (result['order_id'],)).fetchone()
        assert item['subtotal_cents'] == 1
    assert _state_and_postings(pid) == (500, item['subtotal_cents'] * 10000)


@pytest.mark.parametrize('legacy', [False, True])
def test_purchase_void_reverses_saved_posting_not_recomputed_price(legacy):
    cid, pid = _seed('10', '10')
    result = create_purchase_order('NH202610020001', '2026-10-02', [{'product_id': pid, 'quantity': '0.500', 'unit_cost_yuan': '0.01'}], customer_id=cid, request_key='fractional-void')
    if legacy:
        # Construct a pre-fix saved event; do not restate its historical cost.
        with get_db() as conn:
            conn.execute("UPDATE inventory_postings SET cost_delta_micro=5000 WHERE source_type='purchase' AND source_id=?", (str(result['order_id']),))
            conn.execute('UPDATE product_inventory_state SET cost_total_micro=100005000 WHERE product_id=?', (pid,))
    before = _state_and_postings(pid)
    void_purchase_order(result['order_id'], '虚构末笔更正')
    assert _state_and_postings(pid) == (10000, 100000000)
    with get_db() as conn:
        original = conn.execute("SELECT quantity_delta_3dp,cost_delta_micro FROM inventory_postings WHERE source_type='purchase'").fetchone()
        reverse = conn.execute("SELECT quantity_delta_3dp,cost_delta_micro FROM inventory_postings WHERE source_type='purchase_void'").fetchone()
        assert tuple(reverse) == tuple(-value for value in original)
        assert original['cost_delta_micro'] == before[1] - 100000000


@pytest.mark.parametrize('quantities', [('3',), ('2', '1')])
def test_sale_clearance_and_void_conserve_exact_cost(quantities):
    cid, pid = _seed()
    for n, quantity, price in ((1, '1', '10'), (2, '2', '0')):
        create_purchase_order(f'NH20261002{n:04d}', '2026-10-02', [{'product_id': pid, 'quantity': quantity, 'unit_cost_yuan': price}], customer_id=cid, request_key=f'clearance-purchase-{n}')
    assert _state_and_postings(pid) == (3000, 10000000)
    sale_ids = []
    for n, quantity in enumerate(quantities, 1):
        sale_ids.append(create_order_from_typed_rows(cid, f'MD20261002{n:04d}', [{'product_id': pid, 'product_name': '成本守恒商品', 'spec': 'S', 'unit': '米', 'quantity': quantity, 'unit_price_yuan': '20'}], status='saved', order_date='2026-10-02', request_key=f'clearance-sale-{n}'))
        _state_and_postings(pid)
    assert _state_and_postings(pid) == (0, 0)
    with get_db() as conn:
        costs = [r[0] for r in conn.execute('SELECT cost_total_micro FROM order_items ORDER BY id')]
    assert sum(costs) == 10000000
    if len(quantities) == 2:
        assert costs == [6666667, 3333333]
    for sale_id in reversed(sale_ids):
        void_order(sale_id, '虚构末笔更正')
        _state_and_postings(pid)
    assert _state_and_postings(pid) == (3000, 10000000)
