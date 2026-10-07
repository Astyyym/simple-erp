"""C3-A: real business services and isolated reconciliation/PDF contracts."""
import json
from pathlib import Path

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer, create_product, create_order_from_typed_rows,
    create_return_order_from_source,
)
from erp.services.inventory import initialize_product, create_purchase_order
from erp.services import purchase_returns, reconciliation as service
from erp.utils import pdf
from pdf_test_utils import assert_a4_portrait_pdf


def seed_four():
    init_db()
    cid = create_customer('四向虚构往来', opening_balance_cents=12345)
    pid = create_product('公开商品', '型号-A', '米', 2000)
    initialize_product(pid, '20', '10', '2026-10-01', 'INTERNAL-INIT', 'c3-init')
    sale = create_order_from_typed_rows(cid, 'MD202610010001', [
        {'product_id': pid, 'product_name': '公开商品', 'spec': '型号-A', 'unit': '米', 'quantity': '2', 'unit_price_yuan': '20'},
        {'product_id': pid, 'product_name': '公开商品', 'spec': '型号-A', 'unit': '米', 'quantity': '1', 'unit_price_yuan': '30'},
    ], status='saved', order_date='2026-10-01', notes='PUBLIC-SALE-NOTE')
    with get_db() as conn:
        sale_item = conn.execute('SELECT id FROM order_items WHERE order_id=? ORDER BY id', (sale,)).fetchone()[0]
    ret = create_return_order_from_source(cid, 'MD202610010002', sale,
        [{'source_item_id': sale_item, 'quantity': '1'}], status='saved', order_date='2026-10-01')
    purchase = create_purchase_order('NH202610010001', '2026-10-01',
        [{'product_id': pid, 'quantity': '3', 'unit_cost_yuan': '10'}],
        customer_id=cid, request_key='c3-purchase', source_notes='INTERNAL-PURCHASE-NOTE')['order_id']
    with get_db() as conn:
        purchase_item = conn.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=?', (purchase,)).fetchone()[0]
    pr = purchase_returns.create(customer_id=cid, source_order_id=purchase, business_date='2026-10-01',
        items=[{'source_item_id': purchase_item, 'quantity': '1'}], request_key='c3-return', notes='INTERNAL-RETURN-NOTE')['order_id']
    return cid, pid, sale, ret, purchase, pr


def funds_and_inventory():
    with get_db() as conn:
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in ('payments', 'adjustments', 'customers', 'product_inventory_state', 'inventory_postings', 'orders')}


def test_four_type_scope_has_source_namespaces_and_real_public_pdf(monkeypatch):
    cid, pid, sale, ret, purchase, pr = seed_four()
    assert (sale, purchase, pr) == (1, 1, 1)
    before = funds_and_inventory()
    sid = service.create_reconciliation_snapshot(cid, '2026-10-01', '2026-10-01')
    result = service.get_reconciliation_snapshot(sid)
    assert result['scope_version'] == 2
    assert result['scope_ids'] == ['order:1', 'order:2', 'purchase:1', 'purchase_return:1']
    assert result['selected_ids'] == result['scope_ids']
    assert result['selected_total_cents'] == 3000
    assert result['type_totals_cents'] == {'sale': 7000, 'customer_return': -2000, 'purchase': -3000, 'purchase_return': 1000}
    service.update_reconciliation_selection(sid, mode='all', excluded_ids=['purchase:1'])
    result = service.get_reconciliation_snapshot(sid)
    assert result['selected_ids'] == ['order:1', 'order:2', 'purchase_return:1']
    assert result['selected_total_cents'] == 6000
    # Capture only the real rendering boundary; the real WeasyPrint writer still runs.
    captured = {}
    original = pdf._render_reconciliation_template
    def capture(template, **context):
        captured.update(context)
        return original(template, **context)
    monkeypatch.setattr(pdf, '_render_reconciliation_template', capture)
    app = create_app()
    with app.app_context():
        path = pdf.generate_reconciliation_pdf(sid)
    assert_a4_portrait_pdf(path.read_bytes())
    assert captured['snapshot']['selected_total_cents'] == 6000
    assert len(captured['documents']) == 3
    assert len(captured['documents'][0]['items']) == 2
    serialized = json.dumps(captured, ensure_ascii=False, default=str)
    for forbidden in ('INTERNAL-PURCHASE-NOTE', 'INTERNAL-RETURN-NOTE', 'scope_hash', 'scope_json', 'unit_cost_micro', 'cost_total_micro', 'payload_hash', 'opening_balance_cents', 'image_path', 'source_notes'):
        assert forbidden not in serialized
    assert 'PUBLIC-SALE-NOTE' in serialized
    assert sum(doc['order']['total_amount_cents'] for doc in captured['documents']) == 6000
    assert funds_and_inventory() == before


LEGACY_SNAPSHOT_SQL = """CREATE TABLE reconciliation_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    start_date TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '',
    order_type_filter TEXT NOT NULL DEFAULT 'all' CHECK(order_type_filter IN ('all','sale','return')),
    scope_json TEXT NOT NULL, selected_json TEXT NOT NULL, scope_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
); CREATE INDEX idx_reconciliation_snapshots_customer ON reconciliation_snapshots(customer_id,created_at);
CREATE INDEX synthetic_recon_hash ON reconciliation_snapshots(scope_hash);"""


def downgrade_synthetic_snapshots(conn):
    conn.execute('DROP TABLE reconciliation_snapshots')
    conn.executescript(LEGACY_SNAPSHOT_SQL)
    conn.execute('DELETE FROM schema_version WHERE version>11')
    conn.execute("INSERT OR IGNORE INTO schema_version(version,notes) VALUES(11,'synthetic pre-C3')")


def test_empty_legacy_snapshot_migration_preserves_deleted_id_high_water():
    from erp import db
    cid, *_ = seed_four()
    with get_db() as conn:
        downgrade_synthetic_snapshots(conn)
        conn.execute("INSERT INTO reconciliation_snapshots(id,customer_id,scope_json,selected_json,scope_hash) VALUES(77,?,'[]','[]','old-hash')", (cid,))
        conn.execute('DELETE FROM reconciliation_snapshots')
    init_db()
    sid = service.create_reconciliation_snapshot(cid)
    assert sid == 78  # Never reuse a snapshot identity held by another window.
    with get_db() as conn:
        assert conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] == 13
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='synthetic_recon_hash'").fetchone()
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_selection_never_silently_ignores_invalid_supplied_keys():
    cid, *_ = seed_four()
    sid = service.create_reconciliation_snapshot(cid)
    before = service.get_reconciliation_snapshot(sid)['selected_ids']
    with pytest.raises(ValueError, match='来源键'):
        service.update_reconciliation_selection(sid, mode='all', selected_ids=['sale:1'])
    with pytest.raises(ValueError, match='来源键'):
        service.update_reconciliation_selection(sid, mode='explicit', selected_ids=['order:1'], excluded_ids=['1'])
    assert service.get_reconciliation_snapshot(sid)['selected_ids'] == before


def test_pdf_template_context_cannot_receive_flask_globals_or_secret_config(monkeypatch):
    import jinja2
    from erp.config import load_config, save_config
    cid, *_ = seed_four()
    sid = service.create_reconciliation_snapshot(cid)
    settings = load_config()
    settings['private_test_secret'] = 'SECRET-CONFIG-SENTINEL'
    save_config(settings)
    captured = []
    original = jinja2.Template.render
    def capture(template, *args, **kwargs):
        captured.append(dict(*args, **kwargs))
        return original(template, *args, **kwargs)
    monkeypatch.setattr(jinja2.Template, 'render', capture)
    app = create_app()
    with app.app_context():
        pdf.generate_reconciliation_pdf(sid)
    assert len(captured) == 1
    context = captured[0]
    assert 'erp_config' not in context
    assert 'request' not in context and 'g' not in context and 'session' not in context
    assert set(context) == {'snapshot', 'documents', 'config', 'cents_to_yuan', 'format_quantity'}
    assert set(context['config']) == {'shop_name'}
    assert 'SECRET-CONFIG-SENTINEL' not in json.dumps(context, default=str)


def test_legacy_snapshot_migration_is_byte_preserving_orders_only_and_retryable(tmp_path):
    import sqlite3
    from erp import db
    cid, *_ = seed_four()
    before_business = funds_and_inventory()
    with get_db() as conn:
        rows = service._scope_rows(conn, cid, '2026-10-01', '2026-10-01', 'all')
        old_hash = service._fingerprint(rows)
        downgrade_synthetic_snapshots(conn)
        conn.execute("""INSERT INTO reconciliation_snapshots
            (id,customer_id,start_date,end_date,order_type_filter,scope_json,selected_json,scope_hash,created_at,updated_at)
            VALUES(41,?,'2026-10-01','2026-10-01','all','[1, 2]','[2]',?,'2026-10-01 12:34:56','2026-10-01 13:45:56')""", (cid,old_hash))
        old = tuple(conn.execute('SELECT * FROM reconciliation_snapshots').fetchone())
    # Authorizer fails after the table copy, not before any migration work.
    conn = sqlite3.connect(db.db_path()); conn.row_factory = sqlite3.Row
    conn.set_authorizer(lambda action,a,b,d,t: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE and a == 'reconciliation_snapshots_v1' else sqlite3.SQLITE_OK)
    with pytest.raises(sqlite3.DatabaseError):
        with conn:
            db.apply_schema(conn)
    conn.set_authorizer(None)
    assert tuple(conn.execute('SELECT * FROM reconciliation_snapshots').fetchone()) == old
    assert 'scope_version' not in {row['name'] for row in conn.execute('PRAGMA table_info(reconciliation_snapshots)')}
    assert conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] == 11
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='reconciliation_snapshots_v1'").fetchone() is None
    with conn: db.apply_schema(conn)
    with conn: db.apply_schema(conn)
    assert tuple(conn.execute('SELECT * FROM reconciliation_snapshots').fetchone()) == (*old,1)
    assert conn.execute('SELECT COUNT(*) FROM schema_version WHERE version=13').fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND tbl_name='reconciliation_snapshots'").fetchone()[0] == 2
    conn.close()
    result = service.get_reconciliation_snapshot(41)
    assert result['scope_ids'] == [1,2] and result['selected_ids'] == [2]
    assert result['scope_count'] == 2 and result['selected_total_cents'] == -2000
    assert service.validate_reconciliation_snapshot(41) is None
    client = create_app().test_client()
    assert client.get('/analytics/reconciliation?snapshot_id=41&page=2').status_code == 200
    assert client.get('/analytics/reconciliation/41/order/1').get_json()['order']['order_type'] == 'sale'
    assert client.get('/analytics/reconciliation/41/document/purchase:1').status_code == 404
    response = client.get('/analytics/reconciliation/pdf/41')
    assert response.status_code == 200
    assert_a4_portrait_pdf(response.data)
    assert client.post('/analytics/reconciliation/toggle/41/1', data={'selected':'1'}).status_code == 200
    assert service.get_reconciliation_snapshot(41)['selected_ids'] == [1,2]
    assert funds_and_inventory() == before_business


@pytest.mark.parametrize('key,table,items,owner,oid', [
    ('order:1','orders','order_items','order_id',1),
    ('order:2','orders','order_items','order_id',2),
    ('purchase:1','purchase_orders','purchase_order_items','purchase_order_id',1),
    ('purchase_return:1','purchase_return_orders','purchase_return_items','purchase_return_id',1),
])
@pytest.mark.parametrize('mutation', ['product_name','spec','unit','quantity','unit_price','subtotal','business_date','total','status','customer','deleted'])
def test_unselected_candidate_business_changes_block_every_print_and_detail(key,table,items,owner,oid,mutation):
    cid, *_ = seed_four()
    sid = service.create_reconciliation_snapshot(cid)
    service.toggle_reconciliation_selection(sid, key, False)
    with get_db() as conn:
        stored_selection = conn.execute('SELECT selected_json FROM reconciliation_snapshots WHERE id=?',(sid,)).fetchone()[0]
        if mutation in {'product_name','spec','unit'}:
            conn.execute(f"UPDATE {items} SET {mutation}=? WHERE {owner}=?", ('CHANGED-IN-SAME-SECOND',oid))
        elif mutation in {'quantity','unit_price','subtotal'}:
            column = {'quantity':'quantity' if table=='orders' else 'quantity_3dp', 'unit_price':'unit_cost_cents' if table=='purchase_orders' else 'unit_price_cents', 'subtotal':'subtotal_cents'}[mutation]
            conn.execute(f'UPDATE {items} SET {column}={column}+1 WHERE {owner}=?',(oid,))
        elif mutation == 'business_date':
            column = 'order_date' if table=='orders' else 'business_date'
            conn.execute(f"UPDATE {table} SET {column}='2026-10-02' WHERE id=?",(oid,))
        elif mutation == 'total':
            conn.execute(f'UPDATE {table} SET total_amount_cents=total_amount_cents+1 WHERE id=?',(oid,))
        elif mutation == 'status':
            conn.execute(f"UPDATE {table} SET status='void' WHERE id=?",(oid,))
        elif mutation == 'customer':
            other = conn.execute("INSERT INTO customers(name) VALUES('不属本范围')").lastrowid
            conn.execute(f'UPDATE {table} SET customer_id=? WHERE id=?',(other,oid))
        else:
            conn.execute(f"UPDATE {table} SET deleted_at='2026-10-03' WHERE id=?",(oid,))
    assert service.validate_reconciliation_snapshot(sid) == '查询结果已变化，请重新查询后打印'
    client = create_app().test_client()
    response = client.get(f'/analytics/reconciliation/pdf/{sid}')
    assert response.status_code == 400 and '重新查询' in response.get_data(as_text=True)
    assert client.get(f'/analytics/reconciliation/{sid}/document/order:1').status_code == 404
    with get_db() as conn:
        assert conn.execute('SELECT selected_json FROM reconciliation_snapshots WHERE id=?',(sid,)).fetchone()[0] == stored_selection


@pytest.mark.parametrize('invalid', ['', '1', 1, True, 'sale:1', 'return:1', 'order:01', 'purchase:0', 'order:-1', 'purchase:1 ', 'other:1', 'purchase_return:999999'])
def test_invalid_keys_are_rejected_without_changing_selection(invalid):
    cid, *_ = seed_four()
    sid = service.create_reconciliation_snapshot(cid)
    before = service.get_reconciliation_snapshot(sid)['selected_ids']
    for mode in ('all','explicit'):
        with pytest.raises(ValueError):
            service.update_reconciliation_selection(sid,mode=mode,selected_ids=[invalid],excluded_ids=[invalid])
    with pytest.raises(ValueError):
        service.toggle_reconciliation_selection(sid,invalid,False)
    assert service.get_reconciliation_snapshot(sid)['selected_ids'] == before


def test_print_projection_has_one_read_transaction_during_real_concurrent_write(monkeypatch):
    from contextlib import contextmanager
    cid, *_ = seed_four()
    sid = service.create_reconciliation_snapshot(cid)
    original_get_db, original_read = service.get_db, service._read_scope
    trace, connections = [], []
    @contextmanager
    def traced_db():
        with original_get_db() as conn:
            connections.append(conn)
            conn.set_trace_callback(trace.append)
            yield conn
    def interleave(conn, snapshot):
        documents = original_read(conn, snapshot)
        assert conn.in_transaction
        # Actual WAL writer commits after validation, while the read txn lives.
        with original_get_db() as writer:
            writer.execute("UPDATE order_items SET product_name='NEW-CONCURRENT-TEXT' WHERE order_id=1")
        return documents
    monkeypatch.setattr(service,'get_db',traced_db)
    monkeypatch.setattr(service,'_read_scope',interleave)
    context = service.get_reconciliation_print_context(sid)
    assert len(connections) == 1
    assert trace[0] == 'BEGIN' and trace[-1] == 'COMMIT'
    assert context['documents'][0]['items'][0]['product_name'] == '公开商品'
    assert context['snapshot']['selected_total_cents'] == 3000
    monkeypatch.setattr(service,'_read_scope',original_read)
    assert service.validate_reconciliation_snapshot(sid) == '查询结果已变化，请重新查询后打印'


def test_two_windows_toggle_different_sources_without_lost_updates():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    cid, *_ = seed_four()
    sid = service.create_reconciliation_snapshot(cid)
    barrier = Barrier(2)
    def toggle(key):
        barrier.wait(timeout=10)
        service.toggle_reconciliation_selection(sid,key,False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(toggle,['order:1','purchase:1']))
    assert service.get_reconciliation_snapshot(sid)['selected_ids'] == ['order:2','purchase_return:1']
    with get_db() as conn:
        assert json.loads(conn.execute('SELECT selected_json FROM reconciliation_snapshots WHERE id=?',(sid,)).fetchone()[0]) == ['order:2','purchase_return:1']


def test_v1_legacy_duplicate_selection_bytes_remain_readable_without_rewriting_hash():
    cid, *_ = seed_four()
    sid = service.create_reconciliation_snapshot(cid,scope_version=1)
    with get_db() as conn:
        conn.execute("UPDATE reconciliation_snapshots SET selected_json='[2, 2]' WHERE id=?",(sid,))
        raw = tuple(conn.execute('SELECT selected_json,scope_json,scope_hash FROM reconciliation_snapshots WHERE id=?',(sid,)).fetchone())
    result = service.get_reconciliation_snapshot(sid)
    assert result['selected_ids'] == [2] and result['selected_count'] == 1
    assert result['selected_total_cents'] == -2000
    assert service.get_reconciliation_print_context(sid)['snapshot']['selected_count'] == 1
    with get_db() as conn:
        assert tuple(conn.execute('SELECT selected_json,scope_json,scope_hash FROM reconciliation_snapshots WHERE id=?',(sid,)).fetchone()) == raw


def test_typed_api_filters_cross_object_dedupe_and_new_candidate_reset():
    cid,pid,*_ = seed_four()
    other = create_customer('接口外部对象')
    other_purchase = create_purchase_order('NH202610010002','2026-10-01',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'7'}],customer_id=other,request_key='api-other')['order_id']
    sid = service.create_reconciliation_snapshot(cid)
    client = create_app().test_client()
    purchase = client.get(f'/analytics/reconciliation/{sid}/document/purchase:1').get_json()
    assert purchase['order']['order_type'] == 'purchase'
    assert purchase['order']['total_amount_cents'] == -3000
    assert purchase['items'][0]['unit_price_cents'] == 1000 and purchase['items'][0]['subtotal_cents'] == -3000
    assert client.get(f'/analytics/reconciliation/{sid}/order/1').get_json()['order']['order_type'] == 'sale'
    assert client.get(f'/analytics/reconciliation/{sid}/document/purchase:{other_purchase}').status_code == 404
    rejected = client.post(f'/analytics/reconciliation/select/{sid}',data={'mode':'explicit','selected_ids':f'purchase:{other_purchase}'})
    assert rejected.status_code == 400
    rejected = client.post(f'/analytics/reconciliation/select/{sid}',data={'mode':'explicit','selected_ids':'1'})
    assert rejected.status_code == 400
    response = client.post(f'/analytics/reconciliation/select/{sid}',data={'mode':'explicit','selected_ids':['purchase_return:1','order:2','order:1','order:2'],'page':'2'})
    assert response.status_code == 302 and 'page=2' in response.headers['Location']
    assert service.get_reconciliation_snapshot(sid)['selected_ids'] == ['order:1','order:2','purchase_return:1']
    assert client.post(f'/analytics/reconciliation/toggle/{sid}/1',data={'selected':'0'}).status_code == 200
    assert service.get_reconciliation_snapshot(sid)['selected_ids'] == ['order:2','purchase_return:1']
    for kind,key in [('sale','order:1'),('return','order:2'),('customer_return','order:2'),('purchase','purchase:1'),('purchase_return','purchase_return:1')]:
        filtered = service.get_reconciliation_snapshot(service.create_reconciliation_snapshot(cid,order_type_filter=kind))
        assert filtered['scope_ids'] == [key]
    new = create_purchase_order('NH202610010003','2026-10-01',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'7'}],customer_id=cid,request_key='api-new')['order_id']
    assert client.get(f'/analytics/reconciliation/pdf/{sid}').status_code == 400
    with get_db() as conn:
        assert json.loads(conn.execute('SELECT selected_json FROM reconciliation_snapshots WHERE id=?',(sid,)).fetchone()[0]) == ['order:2','purchase_return:1']
    fresh = service.get_reconciliation_snapshot(service.create_reconciliation_snapshot(cid))
    assert fresh['scope_count'] == fresh['selected_count'] == 5
    assert f'purchase:{new}' in fresh['selected_ids']


def test_partial_return_print_preserves_allocated_cent_tail_and_business_price():
    cid,pid,*_ = seed_four()
    sale = create_order_from_typed_rows(cid,'MD202610010010',[{'product_id':pid,'product_name':'公开商品','spec':'型号-A','unit':'米','quantity':'0.500','unit_price_yuan':'0.01'}],status='saved',order_date='2026-10-01')
    purchase = create_purchase_order('NH202610010010','2026-10-01',[{'product_id':pid,'quantity':'0.500','unit_cost_yuan':'0.01'}],customer_id=cid,request_key='tail-purchase')['order_id']
    with get_db() as conn:
        si = conn.execute('SELECT id FROM order_items WHERE order_id=?',(sale,)).fetchone()[0]
        pi = conn.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=?',(purchase,)).fetchone()[0]
    for index in range(2):
        create_return_order_from_source(cid,f'MD20261001001{index+1}',sale,[{'source_item_id':si,'quantity':'0.250'}],status='saved',order_date='2026-10-01')
        purchase_returns.create(customer_id=cid,source_order_id=purchase,business_date='2026-10-01',items=[{'source_item_id':pi,'quantity':'0.250'}],request_key=f'tail-return-{index}')
    context = service.get_reconciliation_print_context(service.create_reconciliation_snapshot(cid))
    cr = [doc for doc in context['documents'] if doc['order']['order_type']=='customer_return'][-2:]
    pr = [doc for doc in context['documents'] if doc['order']['order_type']=='purchase_return'][-2:]
    assert [doc['items'][0]['subtotal_cents'] for doc in cr] == [-1,0]
    assert [doc['items'][0]['subtotal_cents'] for doc in pr] == [1,0]
    assert all(doc['items'][0]['unit_price_cents']==1 for doc in cr+pr)
    assert all(doc['items'][0]['quantity'] in ('0.250','0.25') for doc in cr+pr)
    assert context['snapshot']['selected_total_cents'] == 3000
