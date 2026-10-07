"""C1: unified stable counterparties, synthetic databases only."""
import sqlite3
import re

import pytest

from erp import create_app
from erp import db
from erp.services.accounting import create_customer, create_product
from erp.services.inventory import create_purchase_order, initialize_product, finalize_purchase_order, update_purchase_draft


def _seed():
    db.init_db()
    customer_id = create_customer('双向测试对象', opening_balance_cents=12345)
    product_id = create_product('C1测试商品', '4kg', '个', 2000)
    initialize_product(product_id, '10', '10', '2026-10-01', '测试期初', 'c1-init')
    return customer_id, product_id


@pytest.mark.parametrize('invalid', [None, '', 0, True, 1.0, '1.0', '双向测试对象', 999999])
@pytest.mark.parametrize('status', ['draft', 'saved'])
def test_new_purchase_requires_explicit_stable_customer_id_and_rolls_back(invalid, status):
    cid, pid = _seed()
    with pytest.raises(ValueError, match='往来对象'):
        create_purchase_order('NH202610010902', '2026-10-01', [{'product_id':pid, 'quantity':'1', 'unit_cost_yuan':'14'}], customer_id=invalid, request_key='c1-invalid', status=status)
    with db.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM purchase_order_items').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings WHERE source_type='purchase'").fetchone()[0] == 0
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (10000,100000000)


def test_purchase_persists_stable_counterparty_snapshot_and_does_not_change_balance():
    cid, pid = _seed()
    rows = [{'product_id':pid, 'quantity':'2', 'unit_cost_yuan':'14'}]
    result = create_purchase_order('NH202610010901', '2026-10-01', rows, customer_id=cid, request_key='c1-create')
    with db.get_db() as conn:
        order = conn.execute('SELECT * FROM purchase_orders WHERE id=?', (result['order_id'],)).fetchone()
        assert (order['customer_id'], order['customer_name']) == (cid, '双向测试对象')
        conn.execute("UPDATE customers SET name='已改名对象' WHERE id=?", (cid,))
    assert create_purchase_order('NH202610010901', '2026-10-01', rows, customer_id=cid, request_key='c1-create') == result
    with db.get_db() as conn:
        assert conn.execute('SELECT customer_name FROM purchase_orders').fetchone()[0] == '双向测试对象'
        assert conn.execute('SELECT opening_balance_cents FROM customers').fetchone()[0] == 12345
        assert conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM customer_prices').fetchone()[0] == 0
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (12000,128000000)


def test_legacy_draft_must_bind_before_posting_and_edit_posts_current_values_atomically():
    cid, pid = _seed()
    with db.get_db() as conn:
        oid = conn.execute("INSERT INTO purchase_orders(order_no,business_date,total_amount_cents,status,source_notes,request_key,payload_hash) VALUES ('NH202610010903','2026-10-01',1400,'draft','不推断','legacy-draft','legacy')").lastrowid
        conn.execute("INSERT INTO purchase_order_items(purchase_order_id,product_id,product_name,spec,unit,quantity_3dp,unit_cost_cents,subtotal_cents) VALUES (?,?,'旧快照','4kg','个',1000,1400,1400)", (oid,pid))
    with pytest.raises(ValueError, match='往来对象'):
        finalize_purchase_order(oid)
    with db.get_db() as conn:
        assert conn.execute('SELECT status,customer_id FROM purchase_orders').fetchone()[:] == ('draft',None)
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (10000,100000000)
    update_purchase_draft(oid,'2026-10-02',[{'product_id':pid,'quantity':'3','unit_cost_yuan':'15'}],customer_id=cid,source_notes='当前填写',status='saved')
    assert finalize_purchase_order(oid)['order_no'] == 'NH202610010903'
    with db.get_db() as conn:
        order = conn.execute('SELECT * FROM purchase_orders').fetchone()
        assert (order['order_no'],order['business_date'],order['status'],order['customer_id'],order['customer_name'],order['total_amount_cents'],order['source_notes']) == ('NH202610010903','2026-10-01','saved',cid,'双向测试对象',4500,'当前填写')
        assert conn.execute('SELECT quantity_3dp,unit_cost_cents FROM purchase_order_items').fetchone()[:] == (3000,1500)
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings WHERE source_type='purchase'").fetchone()[0] == 1
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (13000,145000000)


def test_real_purchase_form_create_error_refill_edit_and_detail_use_same_counterparty():
    cid, pid = _seed()
    other = create_customer('当前选择的对象')
    client = create_app().test_client()
    html = client.get('/purchases/new').get_data(as_text=True)
    assert 'name="customer_id"' in html and 'id="customer_name"' in html and '往来对象' in html
    assert 'role="combobox"' in html
    data = {'customer_id':str(cid), 'business_date':'2026-10-01', 'product_id':str(pid),'quantity':'2','unit_cost_yuan':'14','source_notes':'原草稿备注','request_key':'c1-web','status':'draft'}
    response = client.post('/purchases/create',data=data)
    assert response.status_code == 302
    location = response.headers['Location']
    oid = int(location.rsplit('/',1)[1])
    edit = client.get(location+'/edit').get_data(as_text=True)
    assert re.search(rf'id="customer_id"[^>]*value="{cid}"', edit)
    assert '双向测试对象' in edit
    assert 'value="2026-10-01"' in edit and 'readonly' in edit and '原草稿备注' in edit
    assert '/finalize"' not in edit  # current form values must not bypass edit persistence
    failure = client.post(location+'/edit',data={**data,'customer_id':str(other),'quantity':'bad','source_notes':'错误回填备注','status':'saved'})
    assert failure.status_code == 400
    error_html = failure.get_data(as_text=True)
    assert re.search(rf'id="customer_id"[^>]*value="{other}"', error_html)
    assert '当前选择的对象' in error_html
    # 货源备注已从拿货开单页移除，错误回填不再包含该字段。
    assert '货源备注' not in error_html
    response = client.post(location+'/edit',data={**data,'customer_id':str(other),'business_date':'2026-10-02','quantity':'3','unit_cost_yuan':'15','source_notes':'当前输入已入库','status':'saved'},follow_redirects=True)
    assert response.status_code == 200
    detail = response.get_data(as_text=True)
    assert '当前选择的对象' in detail and '当前输入已入库' in detail and '45.00' in detail
    with db.get_db() as conn:
        assert conn.execute('SELECT customer_id,business_date,total_amount_cents,status FROM purchase_orders WHERE id=?',(oid,)).fetchone()[:] == (other,'2026-10-01',4500,'saved')
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (13000,145000000)
        conn.execute("UPDATE customers SET name='改名不得篡改历史' WHERE id=?",(other,))
    client.post(f'/customers/{other}/delete')
    assert '当前选择的对象' in client.get(location).get_data(as_text=True)
    assert '改名不得篡改历史' not in client.get(location).get_data(as_text=True)


def test_finalize_endpoint_does_not_silently_ignore_unsaved_form_values():
    cid, pid = _seed()
    result = create_purchase_order('NH202610010906','2026-10-01',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'14'}],customer_id=cid,request_key='c1-unsaved',status='draft')
    response = create_app().test_client().post(f"/purchases/{result['order_id']}/finalize",data={'customer_id':str(cid),'product_id':str(pid),'quantity':'3','unit_cost_yuan':'15'})
    assert response.status_code == 400
    assert '编辑' in response.get_data(as_text=True)
    with db.get_db() as conn:
        assert conn.execute('SELECT status,total_amount_cents FROM purchase_orders').fetchone()[:] == ('draft',1400)
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (10000,100000000)


def test_incomplete_second_form_line_rejects_entire_purchase_and_refills_counterparty():
    cid, pid = _seed()
    response = create_app().test_client().post('/purchases/create',data={'customer_id':str(cid),'business_date':'2026-10-01','product_id':[str(pid),str(pid)],'quantity':['1','2'],'unit_cost_yuan':['14'],'request_key':'c1-incomplete','status':'saved'})
    assert response.status_code == 400
    assert re.search(rf'id="customer_id"[^>]*value="{cid}"', response.get_data(as_text=True))
    with db.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM purchase_order_items').fetchone()[0] == 0
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (10000,100000000)


@pytest.mark.parametrize('path', ['single','bulk','automatic'])
def test_customer_purge_preserves_purchase_and_reconciliation_references_null_safely(path):
    """有引用的客户删不掉时必须报出原因；能删的照常删掉。

    2026-10-07 起：批量不再「整体中止」，也不再静默无效果——能删的删、删不掉的报原因。
    """
    cid, pid = _seed()
    free = create_customer('可清理对象')
    recon = create_customer('仅对账快照引用对象')
    purchase = create_purchase_order('NH202610010904','2026-10-01',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'14'}],customer_id=cid,request_key='c1-protected')
    with db.get_db() as conn:
        conn.execute("INSERT INTO purchase_orders(order_no,business_date,status,request_key,payload_hash) VALUES ('NH202610010905','2026-10-01','saved','legacy-null','legacy')")
        conn.execute("INSERT INTO reconciliation_snapshots(customer_id,scope_json,selected_json,scope_hash) VALUES (?,'[]','[]','synthetic')",(recon,))
    client = create_app().test_client()
    client.post(f'/customers/{cid}/delete')
    client.post('/customers/bulk_delete',data={'ids':[str(free),str(recon)]})
    assert '双向测试对象' in client.get(f"/purchases/{purchase['order_id']}").get_data(as_text=True)
    with db.get_db() as conn:
        conn.execute("UPDATE customers SET deleted_at='2020-01-01 00:00:00'")
    if path == 'automatic':
        db.init_db()
    elif path == 'bulk':
        # 干净的 free 会被删掉；有引用的 cid / recon 删不掉 → 400 并报出原因。
        response = client.post('/recycle/bulk/customer/purge',data={'ids':[str(cid),str(recon),str(free)]})
        assert response.status_code == 400
        body = response.get_data(as_text=True)
        assert '只能保留' in body
    else:
        # 单条：有引用的报 400，干净的仍能删。
        assert client.post(f'/recycle/customer/{cid}/purge').status_code == 400
        assert client.post(f'/recycle/customer/{recon}/purge').status_code == 400
        assert client.post(f'/recycle/customer/{free}/purge').status_code == 302
    with db.get_db() as conn:
        assert {r['id'] for r in conn.execute('SELECT id FROM customers')} == {cid,recon}
        assert conn.execute('SELECT customer_id,customer_name FROM purchase_orders WHERE id=?',(purchase['order_id'],)).fetchone()[:] == (cid,'双向测试对象')
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_schema9_migration_keeps_legacy_purchases_and_balances(tmp_path):
    # Construct the pre-C1 schema, never copy a business database.
    old_sql = (';'.join(s for s in db.SCHEMA_SQL.split(';') if 'purchase_return' not in s) + ';').replace(
        "    customer_id INTEGER REFERENCES customers(id),\n    customer_name TEXT NOT NULL DEFAULT '',\n", ""
    )
    with db.get_db() as conn:
        conn.executescript(old_sql)
        conn.execute("INSERT INTO schema_version(version, notes) VALUES (9, 'synthetic v9')")
        cid = conn.execute("INSERT INTO customers(name, opening_balance_cents) VALUES ('旧对象', 4321)").lastrowid
        conn.execute("INSERT INTO orders(order_no,customer_id,order_date,total_amount_cents,status) VALUES ('MD202610010001',?,'2026-10-01',5678,'saved')", (cid,))
        for n, status in enumerate(('draft', 'saved'), 1):
            conn.execute("INSERT INTO purchase_orders(order_no,business_date,total_amount_cents,status,source_notes,request_key,payload_hash) VALUES (?,'2026-10-01',1234,?,'旧对象：不得推断',?,'legacy')", (f'NH20261001000{n}', status, f'legacy-{n}'))
        before = [tuple(row) for row in conn.execute("SELECT * FROM purchase_orders ORDER BY id")]
        before_balance = conn.execute("SELECT opening_balance_cents+(SELECT SUM(total_amount_cents) FROM orders WHERE customer_id=?) FROM customers WHERE id=?", (cid,cid)).fetchone()[0]
    db.init_db()
    db.init_db()
    with db.get_db() as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 13
        assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=13").fetchone()[0] == 1
        columns = {r['name']: r for r in conn.execute('PRAGMA table_info(purchase_orders)')}
        assert columns['customer_id']['notnull'] == 0
        assert columns['customer_name']['notnull'] == 1
        assert any(r['from']=='customer_id' and r['table']=='customers' for r in conn.execute('PRAGMA foreign_key_list(purchase_orders)'))
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert [tuple(row)[:len(before[0])] for row in conn.execute("SELECT * FROM purchase_orders ORDER BY id")] == before
        assert [(r['customer_id'],r['customer_name']) for r in conn.execute('SELECT * FROM purchase_orders')] == [(None,''),(None,'')]
        assert conn.execute("SELECT opening_balance_cents+(SELECT SUM(total_amount_cents) FROM orders WHERE customer_id=?) FROM customers WHERE id=?", (cid,cid)).fetchone()[0] == before_balance == 9999
        assert conn.execute('SELECT COUNT(*) FROM inventory_postings').fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE purchase_orders SET customer_id=999999 WHERE id=1")


def test_schema9_partial_migration_failure_rolls_back_columns_and_version(tmp_path):
    conn = sqlite3.connect(tmp_path / 'synthetic-v9-failure.db')
    conn.row_factory = sqlite3.Row
    conn.executescript((';'.join(s for s in db.SCHEMA_SQL.split(';') if 'purchase_return' not in s) + ';').replace("    customer_id INTEGER REFERENCES customers(id),\n    customer_name TEXT NOT NULL DEFAULT '',\n", ""))
    conn.execute("INSERT INTO schema_version(version) VALUES (9)")
    conn.commit()
    alters = []
    def authorize(action, arg1, arg2, database, trigger):
        if action == sqlite3.SQLITE_ALTER_TABLE and arg2 == 'purchase_orders':
            alters.append(arg2)
            if len(alters) == 2:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
        with conn:
            db.apply_schema(conn)
    conn.set_authorizer(None)
    assert len(alters) == 2
    assert 'customer_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(purchase_orders)')}
    assert conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] == 9
    with conn:
        db.apply_schema(conn)
    assert conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] == 13
    conn.close()


@pytest.mark.parametrize('status', ['draft','saved'])
def test_same_key_other_counterparty_conflicts_without_extra_orders_or_stock(status):
    cid, pid = _seed()
    other = create_customer('另一对象')
    rows = [{'product_id':pid,'quantity':'1','unit_cost_yuan':'14'}]
    first = create_purchase_order('NH202610010907','2026-10-01',rows,customer_id=cid,request_key='c1-cross-key',status=status)
    with db.get_db() as conn:
        state_before = tuple(conn.execute('SELECT * FROM product_inventory_state').fetchone())
    assert create_purchase_order('NH202610010907','2026-10-01',rows,customer_id=str(cid),request_key='c1-cross-key',status=status) == first
    with pytest.raises(ValueError, match='幂等键内容不一致'):
        create_purchase_order('NH202610010907','2026-10-01',rows,customer_id=other,request_key='c1-cross-key',status=status)
    with db.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0] == 1
        assert tuple(conn.execute('SELECT * FROM product_inventory_state').fetchone()) == state_before


@pytest.mark.parametrize('disabled', ['hidden','inactive'])
def test_hidden_counterparty_blocks_new_business_but_preserves_history(disabled):
    cid, pid = _seed()
    rows = [{'product_id':pid,'quantity':'1','unit_cost_yuan':'14'}]
    draft = create_purchase_order('NH202610010908','2026-10-01',rows,customer_id=cid,request_key='c1-hidden-draft',status='draft')
    with db.get_db() as conn:
        if disabled == 'hidden':
            conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",(cid,))
        else:
            conn.execute('UPDATE customers SET is_active=0 WHERE id=?',(cid,))
    for status in ('draft','saved'):
        with pytest.raises(ValueError,match='往来对象'):
            create_purchase_order('NH202610010909','2026-10-01',rows,customer_id=cid,request_key='c1-hidden-new',status=status)
    with pytest.raises(ValueError,match='往来对象'):
        finalize_purchase_order(draft['order_id'])
    with pytest.raises(ValueError,match='往来对象'):
        update_purchase_draft(draft['order_id'],'2026-10-01',rows,customer_id=cid)
    html = create_app().test_client().get(f"/purchases/{draft['order_id']}").get_data(as_text=True)
    assert '双向测试对象' in html
    with db.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0] == 1
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (10000,100000000)


def test_failed_draft_edit_keeps_original_object_rows_and_inventory():
    cid, pid = _seed()
    other = create_customer('失败不得替换对象')
    unknown = create_product('未启用测试商品','x','个',100)
    draft = create_purchase_order('NH202610010910','2026-10-01',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'14'}],customer_id=cid,request_key='c1-failed-edit',status='draft')
    with db.get_db() as conn:
        before = tuple(conn.execute('SELECT * FROM purchase_orders').fetchone())
        before_items = [tuple(row) for row in conn.execute('SELECT * FROM purchase_order_items')]
    with pytest.raises(ValueError,match='尚未启用库存'):
        update_purchase_draft(draft['order_id'],'2026-10-01',[{'product_id':pid,'quantity':'2','unit_cost_yuan':'15'},{'product_id':unknown,'quantity':'1','unit_cost_yuan':'10'}],customer_id=other,status='saved')
    with db.get_db() as conn:
        assert tuple(conn.execute('SELECT * FROM purchase_orders').fetchone()) == before
        assert [tuple(row) for row in conn.execute('SELECT * FROM purchase_order_items')] == before_items
        assert conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state').fetchone()[:] == (10000,100000000)


def test_legacy_formal_null_order_is_readable_locked_and_not_forced_to_bind():
    cid, pid = _seed()
    with db.get_db() as conn:
        oid = conn.execute("INSERT INTO purchase_orders(order_no,business_date,total_amount_cents,status,source_notes,request_key,payload_hash) VALUES ('NH202610010911','2026-10-01',1234,'saved','旧货源保留','legacy-saved','legacy')").lastrowid
        before = tuple(conn.execute('SELECT * FROM purchase_orders WHERE id=?',(oid,)).fetchone())
    client = create_app().test_client()
    html = client.get(f'/purchases/{oid}').get_data(as_text=True)
    assert '历史拿货未关联往来对象，需人工确认' in html and '旧货源保留' in html and '12.34' in html
    assert client.get(f'/purchases/{oid}/edit').status_code == 400
    assert finalize_purchase_order(oid)['status'] == 'saved'
    with db.get_db() as conn:
        assert tuple(conn.execute('SELECT * FROM purchase_orders WHERE id=?',(oid,)).fetchone()) == before
        assert conn.execute('SELECT opening_balance_cents FROM customers').fetchone()[0] == 12345


def test_service_has_no_implicit_counterparty_default():
    import inspect
    for service in (create_purchase_order,update_purchase_draft):
        assert inspect.signature(service).parameters['customer_id'].default is inspect.Parameter.empty
