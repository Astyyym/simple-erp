"""C2 real isolated SQLite lifecycle regressions."""
import pytest
from erp.db import init_db, get_db
from erp.services.accounting import create_customer, create_product, create_order_from_typed_rows, void_order
from erp.services.inventory import initialize_product, create_purchase_order, void_purchase_order


def seed(quantity='3', price='10'):
    init_db()
    cid = create_customer('退拿货虚构往来')
    pid = create_product('退拿货虚构商品', 'S', '米', 2000)
    initialize_product(pid, '0', '0', '2026-10-02', '虚构', 'c2-init', confirm_zero=True)
    source = create_purchase_order('NH202610020001', '2026-10-02', [{'product_id': pid, 'quantity': quantity, 'unit_cost_yuan': price}], customer_id=cid, request_key='c2-source')
    with get_db() as conn:
        iid = conn.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=?', (source['order_id'],)).fetchone()[0]
    return cid, pid, source['order_id'], iid


def stock(pid):
    with get_db() as conn:
        state = tuple(conn.execute('SELECT quantity_3dp,cost_total_micro FROM product_inventory_state WHERE product_id=?', (pid,)).fetchone())
        sums = tuple(conn.execute('SELECT SUM(quantity_delta_3dp),SUM(cost_delta_micro) FROM inventory_postings WHERE product_id=?', (pid,)).fetchone())
        assert state == sums
        return state


@pytest.mark.parametrize('later', ['purchase', 'sale'])
def test_purchase_can_void_after_later_economic_event_was_voided(later):
    cid, pid, source, iid = seed()
    if later == 'purchase':
        second = create_purchase_order('NH202610020002', '2026-10-02', [{'product_id': pid, 'quantity': '1', 'unit_cost_yuan': '20'}], customer_id=cid, request_key='c2-second')
        void_purchase_order(second['order_id'], '更正后单')
    else:
        sale = create_order_from_typed_rows(cid, 'MD202610020001', [{'product_id': pid, 'product_name': '退拿货虚构商品', 'spec': 'S', 'unit': '米', 'quantity': '1', 'unit_price_yuan': '20'}], status='saved', order_date='2026-10-02', request_key='c2-sale')
        void_order(sale, '更正后单')
    void_purchase_order(source, '更正前单')
    assert stock(pid) == (0, 0)


def test_formal_partial_return_uses_source_amount_and_current_stock_cost():
    from erp.services import purchase_returns as service
    cid, pid, source, iid = seed()
    create_purchase_order('NH202610020002', '2026-10-02', [{'product_id': pid, 'quantity': '1', 'unit_cost_yuan': '30'}], customer_id=cid, request_key='c2-current-cost')
    result = service.create(customer_id=cid, source_order_id=source, business_date='2026-10-02', items=[{'source_item_id': iid, 'quantity': '1'}], request_key='c2-return', status='saved', notes='虚构备注')
    assert result['total_amount_cents'] == 1000
    assert result['order_no'] == 'NH202610020003'  # seed 与本次拿货单占用 0001、0002
    assert stock(pid) == (3000, 45000000)
    with get_db() as conn:
        row = conn.execute('SELECT * FROM purchase_return_items WHERE purchase_return_id=?', (result['order_id'],)).fetchone()
        assert row['source_item_id'] == iid
        assert row['cost_total_micro'] == 15000000
        assert row['subtotal_cents'] == 1000
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_draft_edit_current_rows_posts_atomically_and_locks_economics():
    from erp.services import purchase_returns as service
    cid, pid, source, iid = seed()
    args = dict(customer_id=cid, source_order_id=source, business_date='2026-10-02', items=[{'source_item_id':iid,'quantity':'1'}], request_key='draft-retry', status='draft', notes='初稿')
    result = service.create(**args)
    assert stock(pid) == (3000, 30000000)
    changed = service.update_draft(result['order_id'], expected_version=0, customer_id=cid, source_order_id=source, items=[{'source_item_id':iid,'quantity':'2'}], notes='当前输入', status='saved')
    assert changed['version'] == 1
    assert stock(pid) == (1000, 10000000)
    assert service.create(**args)['order_id'] == result['order_id']
    with get_db() as conn:
        stable = conn.execute('SELECT id FROM purchase_return_items').fetchone()[0]
    service.edit_notes(result['order_id'], '只改备注', expected_version=1)
    with get_db() as conn:
        assert conn.execute('SELECT id FROM purchase_return_items').fetchone()[0] == stable
    with pytest.raises(ValueError, match='锁定'):
        service.update_draft(result['order_id'], expected_version=2, customer_id=cid, source_order_id=source, items=[{'source_item_id':iid,'quantity':'1'}])
    with pytest.raises(ValueError, match='版本'):
        service.edit_notes(result['order_id'], '过期', expected_version=1)


def test_void_return_releases_quota_then_source_void_and_restore_visibility_only():
    from erp.services import purchase_returns as service
    cid, pid, source, iid = seed()
    args = dict(customer_id=cid, source_order_id=source, business_date='2026-10-02', items=[{'source_item_id':iid,'quantity':'1'}], request_key='void-return')
    result = service.create(**args)
    with pytest.raises(ValueError):
        void_purchase_order(source, '存在后续退拿货')
    with pytest.raises(ValueError, match='原因'):
        service.void(result['order_id'], '', expected_version=0)
    service.void(result['order_id'], '错单更正', expected_version=0)
    assert stock(pid) == (3000, 30000000)
    void_purchase_order(source, '源单更正')
    assert stock(pid) == (0, 0)
    service.delete(result['order_id'], expected_version=1)
    service.restore(result['order_id'], expected_version=2)
    with get_db() as conn:
        order = conn.execute('SELECT * FROM purchase_return_orders').fetchone()
        assert order['status'] == 'void' and order['posted_once'] == 1 and order['deleted_at'] is None
        original = conn.execute("SELECT quantity_delta_3dp,cost_delta_micro FROM inventory_postings WHERE source_type='purchase_return'").fetchone()
        reverse = conn.execute("SELECT quantity_delta_3dp,cost_delta_micro FROM inventory_postings WHERE source_type='purchase_return_void'").fetchone()
        assert tuple(reverse) == tuple(-v for v in original)
    assert stock(pid) == (0, 0)


def test_seven_day_cleanup_removes_expired_recycled_sale():
    """2026-10-07 起：超过 7 天的回收站单据会被真正清掉（旧版对 void 单据恒不生效）。

    用户口径：不要了就清干净，不留历史。
    """
    from erp.db import purge_expired_recycle_bin
    from erp.services.accounting import delete_order
    cid, pid, source, iid = seed()
    sale = create_order_from_typed_rows(cid, 'MD202610020001', [{'product_id':pid,'product_name':'退拿货虚构商品','spec':'S','unit':'米','quantity':'1','unit_price_yuan':'20'}], status='saved', order_date='2026-10-02', request_key='purge-sale')
    void_order(sale, '更正')
    delete_order(sale)
    with get_db() as conn:
        conn.execute("UPDATE orders SET deleted_at='2000-01-01' WHERE id=?",(sale,))
        purge_expired_recycle_bin(conn)
        assert conn.execute('SELECT 1 FROM orders WHERE id=?',(sale,)).fetchone() is None
        assert conn.execute('SELECT COUNT(*) FROM order_items WHERE order_id=?',(sale,)).fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_seven_day_cleanup_keeps_sale_still_referenced_by_live_return():
    """被**未删除**的退货单引用时，销售单即使过期也不能清——那会破坏活单据的追溯。

    这种记录会一直保留（并在回收站显示原因），直到引用它的退货单也被删除。
    """
    from erp.db import purge_expired_recycle_bin
    from erp.services.accounting import create_return_order_from_source, delete_order
    cid, pid, source, iid = seed()
    sale = create_order_from_typed_rows(cid, 'MD202610020002', [{'product_id':pid,'product_name':'退拿货虚构商品','spec':'S','unit':'米','quantity':'1','unit_price_yuan':'20'}], status='saved', order_date='2026-10-02', request_key='purge-sale-ref')
    with get_db() as conn:
        sale_item = conn.execute('SELECT id FROM order_items WHERE order_id=?', (sale,)).fetchone()[0]
    # 活退货单指向这张销售单
    create_return_order_from_source(cid, 'MD202610020003', sale, [{'source_item_id': sale_item, 'quantity': '1'}], status='saved', order_date='2026-10-02')
    void_order(sale, '更正')
    delete_order(sale)
    with get_db() as conn:
        conn.execute("UPDATE orders SET deleted_at='2000-01-01' WHERE id=?", (sale,))
        purge_expired_recycle_bin(conn)
        assert conn.execute('SELECT 1 FROM orders WHERE id=?', (sale,)).fetchone() is not None


def test_draft_finalize_rechecks_live_quota_and_absorbs_amount_tail():
    from erp.services import purchase_returns as service
    cid, pid, source, iid = seed('0.500','0.01')
    base = dict(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'0.250'}])
    draft = service.create(**base,request_key='tail-draft',status='draft')
    first = service.create(**base,request_key='tail-first')
    assert first['total_amount_cents'] == 1
    final = service.finalize(draft['order_id'],expected_version=0)
    assert final['total_amount_cents'] == 0
    assert stock(pid) == (0,0)
    second_draft = service.create(**base,request_key='tail-exhausted',status='draft')
    with pytest.raises(ValueError,match='可退'):
        service.finalize(second_draft['order_id'],expected_version=0)
    assert stock(pid) == (0,0)


def test_amplified_quantity_is_friendly_rejection_without_partial_write():
    from erp.services import purchase_returns as service
    cid,pid,source,iid = seed()
    with pytest.raises(ValueError,match='范围'):
        service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1e100000'}],request_key='overflow')
    assert stock(pid) == (3000,30000000)
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_return_orders').fetchone()[0] == 0


def test_recycle_purges_return_then_its_source_purchase_order():
    """2026-10-07 起：退拿货与它引用的拿货单都能被清掉。

    顺序：退拿货是引用方，先清它；清掉后拿货单不再被引用，也能清掉。
    """
    from erp.services import purchase_returns as service
    from erp.db import purge_expired_recycle_bin
    from erp import create_app
    cid,pid,source,iid = seed()
    result = service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='recycle-return')
    service.void(result['order_id'],'更正',expected_version=0)
    service.delete(result['order_id'],expected_version=1)
    client = create_app().test_client()
    # 退拿货可清（它是引用方）
    assert client.post(f"/recycle/purchase_return/{result['order_id']}/purge",data={'version':'2'}).status_code == 302
    with get_db() as conn:
        assert conn.execute('SELECT 1 FROM purchase_return_orders WHERE id=?',(result['order_id'],)).fetchone() is None
        # 退拿货清掉后，拿货单也过期可清
        conn.execute("UPDATE purchase_orders SET deleted_at='2000-01-01'")
        purge_expired_recycle_bin(conn)
        assert conn.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_recycle_keeps_purchase_order_still_referenced_by_live_return():
    """被**未删除**的退拿货单引用时，拿货单即使过期也不能清（会破坏活单据追溯）。"""
    from erp.services import purchase_returns as service
    from erp.db import purge_expired_recycle_bin
    cid,pid,source,iid = seed()
    # 活退拿货单指向拿货单
    service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='recycle-live-return')
    with get_db() as conn:
        conn.execute("UPDATE purchase_orders SET deleted_at='2000-01-01'")
        purge_expired_recycle_bin(conn)
        assert conn.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0] == 1


def test_typed_entry_http_current_input_save_detail_and_object_isolation():
    from erp import create_app
    cid,pid,source,iid=seed()
    client=create_app().test_client()
    page=client.get('/purchases/return/new')
    assert page.status_code==200 and '退拿货明细'.encode() in page.data
    assert 'id="productCatalog"'.encode() in page.data
    draft=client.post('/purchases/return/create',data={'customer_id':cid,'business_date':'2026-10-02','product_id':pid,'quantity':'1','unit_price_yuan':'10.00','request_key':'http-return','status':'draft','notes':'页面草稿'})
    assert draft.status_code==302 and '/purchases/return/' in draft.location
    assert stock(pid)==(3000,30000000)
    with get_db() as conn:
        oid=conn.execute('SELECT id FROM purchase_return_orders').fetchone()[0]
    saved=client.post(f'/purchases/return/{oid}/edit',data={'customer_id':cid,'product_id':pid,'quantity':'2','unit_price_yuan':'10.00','version':'0','status':'saved','notes':'当前输入','business_date':'2000-01-01'})
    assert saved.status_code==302
    assert stock(pid)==(1000,10000000)
    detail=client.get(saved.location)
    assert detail.status_code==200 and '本店退给对方'.encode() in detail.data
    assert '手工开单'.encode() in detail.data
    with get_db() as conn:
        assert conn.execute('SELECT business_date FROM purchase_return_orders').fetchone()[0]=='2026-10-02'


def test_http_lifecycle_list_typed_keys_recycle_and_restore():
    from erp import create_app
    from erp.services import purchase_returns as service
    cid,pid,source,iid = seed()
    result = service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='http-life',status='draft')
    oid = result['order_id']
    client = create_app().test_client()
    assert client.post(f'/purchases/return/{oid}/finalize',data={'version':'0','quantity':'2'}).status_code == 400
    assert client.post(f'/purchases/return/{oid}/finalize',data={'version':'0'}).status_code == 302
    assert client.post(f'/purchases/return/{oid}/notes',data={'version':'1','notes':'详情备注'}).status_code == 302
    page = client.get('/orders/?start_date=2026-10-02&end_date=2026-10-02&order_type=purchase_return')
    assert b'data-document-key="purchase_return:1"' in page.data
    assert '退拿货单'.encode() in page.data
    assert client.post(f'/purchases/return/{oid}/void',data={'version':'2','reason':'页面更正'}).status_code == 302
    assert client.post(f'/purchases/return/{oid}/delete',data={'version':'3'}).status_code == 302
    page = client.get('/recycle/')
    assert f'/recycle/purchase_return/{oid}/restore'.encode() in page.data
    assert client.post(f'/recycle/purchase_return/{oid}/restore',data={'version':'4'}).status_code == 302
    assert stock(pid) == (3000,30000000)
    with get_db() as conn:
        assert conn.execute('SELECT status,notes,version FROM purchase_return_orders').fetchone()[:] == ('void','详情备注',5)


def test_return_recycle_bulk_restore_requires_each_version_and_is_atomic():
    from erp import create_app
    from erp.services import purchase_returns as service
    cid,pid,source,iid = seed()
    ids=[]
    for n in range(2):
        r=service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key=f'bulk-{n}',status='draft')
        ids.append(r['order_id']);service.delete(r['order_id'],expected_version=0)
    client=create_app().test_client()
    assert client.post('/recycle/bulk/purchase_return/restore',data={'ids':ids,'versions':['1','0']}).status_code == 400
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_return_orders WHERE deleted_at IS NOT NULL').fetchone()[0] == 2
    assert client.post('/recycle/bulk/purchase_return/restore',data={'ids':ids,'versions':['1','1']}).status_code == 302
    service.delete(ids[0],expected_version=2)
    assert client.post(f'/recycle/purchase_return/{ids[0]}/purge',data={'version':'1'}).status_code == 400
    assert client.post(f'/recycle/purchase_return/{ids[0]}/purge',data={'version':'3'}).status_code == 302


def test_purged_draft_number_is_never_reused():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    args=dict(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],status='draft')
    first=service.create(**args,request_key='reserved-first')
    service.delete(first['order_id'],expected_version=0)
    service.recycle_batch([first['order_id']],[1],action='purge')
    second=service.create(**args,request_key='reserved-second')
    assert second['order_no']=='NH202610020003'


@pytest.mark.parametrize('mode',['same_key','different_keys','quota','stock'])
def test_two_real_threads_serialize_keys_numbers_quota_and_stock(mode):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    if mode == 'stock':
        create_order_from_typed_rows(cid,'MD202610020001',[{'product_id':pid,'product_name':'退拿货虚构商品','spec':'S','unit':'米','quantity':'2','unit_price_yuan':'20'}],status='saved',order_date='2026-10-02',request_key='race-sale')
    barrier=Barrier(2)
    def work(n):
        barrier.wait()
        try:
            return service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'2' if mode=='quota' else '1'}],request_key='race' if mode=='same_key' else f'race-{n}')
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(work,range(2)))
    successes=[r for r in results if isinstance(r,dict)]
    if mode == 'same_key':
        assert len(successes)==2 and len({r['order_id'] for r in successes})==1
    elif mode == 'different_keys':
        assert {r['order_no'] for r in successes}=={'NH202610020002','NH202610020003'}
    else:
        assert len(successes)==1 and len([r for r in results if isinstance(r,str)])==1
    stock(pid)
    with get_db() as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_multi_line_shortage_rolls_back_earlier_line_and_number():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    second=create_product('缺货行虚构商品','Z','个',100)
    initialize_product(second,'0','0','2026-10-02','虚构','second-init',confirm_zero=True)
    source2=create_purchase_order('NH202610020002','2026-10-02',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'0'},{'product_id':second,'quantity':'1','unit_cost_yuan':'0'}],customer_id=cid,request_key='multi-source')['order_id']
    sale=create_order_from_typed_rows(cid,'MD202610020001',[{'product_id':second,'product_name':'缺货行虚构商品','spec':'Z','unit':'个','quantity':'1','unit_price_yuan':'1'}],status='saved',order_date='2026-10-02',request_key='deplete')
    with get_db() as conn:
        ids=[r[0] for r in conn.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id',(source2,))]
    before=stock(pid)
    with pytest.raises(ValueError,match='库存不足'):
        service.create(customer_id=cid,source_order_id=source2,business_date='2026-10-02',items=[{'source_item_id':i,'quantity':'1'} for i in ids],request_key='shortage')
    assert stock(pid)==before and stock(second)==(0,0)
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_return_orders').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM purchase_number_sequences').fetchone()[0]==0
    void_order(sale,'返还库存')
    result=service.create(customer_id=cid,source_order_id=source2,business_date='2026-10-02',items=[{'source_item_id':i,'quantity':'1'} for i in ids],request_key='shortage')
    assert result['order_no']=='NH202610020003'  # 源单占用 0001、0002
    assert stock(second)==(0,0)
    service.void(result['order_id'],'多行更正',expected_version=0)
    assert stock(pid)==before and stock(second)==(1000,0)


def test_product_purge_with_unposted_purchase_reference_is_friendly_and_preserves_fk():
    from erp import create_app
    from erp.db import purge_expired_recycle_bin
    init_db()
    cid=create_customer('草稿保护虚构对象');pid=create_product('草稿引用商品','','个',0)
    create_purchase_order('NH202610020001','2026-10-02',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'0'}],customer_id=cid,request_key='unposted-source',status='draft')
    client=create_app().test_client()
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at='2000-01-01' WHERE id=?",(pid,))
    assert client.post(f'/recycle/product/{pid}/purge').status_code==400
    assert client.post('/recycle/not-a-kind/1/restore').status_code==400
    with get_db() as conn:
        purge_expired_recycle_bin(conn)
        assert conn.execute('SELECT id FROM products WHERE id=?',(pid,)).fetchone()
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_schema10_return_migration_rolls_back_all_new_tables_then_retries(tmp_path):
    import sqlite3
    from erp import db
    conn=sqlite3.connect(tmp_path/'schema10.db');conn.row_factory=sqlite3.Row
    old_sql=';'.join(s for s in db.SCHEMA_SQL.split(';') if 'purchase_return' not in s)+';'
    conn.executescript(old_sql)
    conn.execute('INSERT INTO schema_version(version) VALUES (10)');conn.commit()
    def authorize(action,arg1,arg2,database,trigger):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TABLE and arg1=='purchase_return_items' else sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    with pytest.raises(sqlite3.DatabaseError):
        with conn: db.apply_schema(conn)
    conn.set_authorizer(None)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'purchase_return%'").fetchall()==[]
    assert conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]==10
    with conn: db.apply_schema(conn)
    with conn: db.apply_schema(conn)
    assert conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]==13
    assert conn.execute('SELECT COUNT(*) FROM schema_version WHERE version=13').fetchone()[0]==1
    assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    conn.close()


def test_purchase_detail_source_entry_quantity_and_void_recycle():
    from erp import create_app
    cid,pid,source,iid=seed()
    client=create_app().test_client()
    page=client.get(f'/purchases/{source}').get_data(as_text=True)
    assert f'/purchases/return/new?customer_id={cid}' in page
    assert 'source_order_id' not in page
    assert 'class="page-danger-action"' not in page and '>3.000<' not in page
    # 正式拿货单也能直接移入回收站（内部自动冲回库存）。
    assert client.post(f'/purchases/{source}/delete').status_code == 302
    assert client.post(f'/recycle/purchase_order/{source}/restore').status_code == 302
    with get_db() as conn:
        assert conn.execute('SELECT status FROM purchase_orders WHERE id=?',(source,)).fetchone()[0]=='void'
    assert stock(pid)==(0,0)


def test_void_numeric_overflow_rejects_with_no_reversal_or_state_change():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    r=service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='void-overflow')
    with get_db() as conn:
        conn.execute('UPDATE product_inventory_state SET quantity_3dp=9223372036854775807 WHERE product_id=?',(pid,))
        before=tuple(conn.execute('SELECT * FROM product_inventory_state').fetchone())
    with pytest.raises(ValueError,match='范围'):
        service.void(r['order_id'],'数值范围测试',expected_version=0)
    with get_db() as conn:
        assert tuple(conn.execute('SELECT * FROM product_inventory_state').fetchone())==before
        assert conn.execute("SELECT COUNT(*) FROM inventory_postings WHERE source_type='purchase_return_void'").fetchone()[0]==0


@pytest.mark.parametrize('invalid',['other_customer','foreign_row','duplicate_row','void_source','deleted_source','null_customer','missing_trace','broken_trace','missing_items','early_date','future_date','nan','precision'])
def test_invalid_source_and_quantity_never_write_partial_return(invalid):
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    args=dict(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='invalid')
    if invalid=='other_customer':args['customer_id']=create_customer('其他虚构对象')
    elif invalid=='foreign_row':args['items'][0]['source_item_id']=iid+1000
    elif invalid=='duplicate_row':args['items']*=2
    elif invalid=='early_date':args['business_date']='2026-10-01'
    elif invalid=='future_date':args['business_date']='9999-12-31'
    elif invalid=='nan':args['items'][0]['quantity']='NaN'
    elif invalid=='precision':args['items'][0]['quantity']='0.0001'
    else:
        with get_db() as conn:
            if invalid=='void_source':conn.execute("UPDATE purchase_orders SET status='void'")
            elif invalid=='deleted_source':conn.execute("UPDATE purchase_orders SET deleted_at=CURRENT_TIMESTAMP")
            elif invalid=='null_customer':conn.execute('UPDATE purchase_orders SET customer_id=NULL')
            elif invalid=='missing_trace':conn.execute("DELETE FROM inventory_postings WHERE source_type='purchase'")
            elif invalid=='broken_trace':conn.execute("UPDATE inventory_postings SET quantity_delta_3dp=1 WHERE source_type='purchase'")
            elif invalid=='missing_items':conn.execute('DELETE FROM purchase_order_items')
    with get_db() as conn: before=tuple(conn.execute('SELECT * FROM product_inventory_state').fetchone())
    with pytest.raises(ValueError):service.create(**args)
    with get_db() as conn:
        assert tuple(conn.execute('SELECT * FROM product_inventory_state').fetchone())==before
        assert conn.execute('SELECT COUNT(*) FROM purchase_return_orders').fetchone()[0]==0


def test_hidden_source_entities_remain_returnable_without_changing_old_financial_facts():
    from erp.services import purchase_returns as service
    from erp.services.accounting import customer_balance_cents,add_payment,add_adjustment
    cid,pid,source,iid=seed()
    sale=create_order_from_typed_rows(cid,'MD202610020001',[{'product_id':pid,'product_name':'退拿货虚构商品','spec':'S','unit':'米','quantity':'1','unit_price_yuan':'20'}],status='saved',order_date='2026-10-02',request_key='history-sale')
    add_payment(cid,100,'2026-10-02');add_adjustment(cid,200,'虚构调整')
    with get_db() as conn:
        conn.execute("UPDATE customers SET is_active=0,deleted_at=CURRENT_TIMESTAMP")
        conn.execute("UPDATE products SET is_active=0,deleted_at=CURRENT_TIMESTAMP")
        before={t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in ['orders','order_items','payments','adjustments','customer_prices']}
    balance=customer_balance_cents(cid)
    r=service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='hidden-return')
    with get_db() as conn:
        assert {t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in before}==before
    # 退拿货已按用户决策并入客户视图，创建后余额按退拿货金额正向变化
    assert customer_balance_cents(cid)==balance + r['total_amount_cents']
    service.void(r['order_id'],'来源退回更正',expected_version=0)
    # 作废后不再计入余额，回到并入前的数值
    assert customer_balance_cents(cid)==balance
    void_order(sale,'撤销后续销售')
    assert stock(pid)==(3000,30000000)


def test_same_product_distinct_source_lines_have_independent_quota_and_stable_cost_order():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    void_purchase_order(source,'准备独立重复行来源')
    src=create_purchase_order('NH202610020002','2026-10-02',[{'product_id':pid,'quantity':'1','unit_cost_yuan':'10'},{'product_id':pid,'quantity':'2','unit_cost_yuan':'0'}],customer_id=cid,request_key='repeated-source')['order_id']
    with get_db() as conn: ids=[r[0] for r in conn.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id',(src,))]
    result=service.create(customer_id=cid,source_order_id=src,business_date='2026-10-02',items=[{'source_item_id':ids[1],'quantity':'2'},{'source_item_id':ids[0],'quantity':'1'}],request_key='repeated-return')
    assert stock(pid)==(0,0)
    with get_db() as conn:
        rows=conn.execute('SELECT source_item_id,cost_total_micro,subtotal_cents FROM purchase_return_items ORDER BY id').fetchall()
        assert [tuple(r) for r in rows]==[(ids[0],3333333,1000),(ids[1],6666667,0)]
    service.void(result['order_id'],'精确倒序恢复',expected_version=0)
    assert stock(pid)==(3000,10000000)


def test_return_recycle_has_versioned_bulk_controls_and_single_active_entry():
    import re
    from erp import create_app
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    r=service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='bulk-controls',status='draft')
    service.delete(r['order_id'],expected_version=0)
    client=create_app().test_client()
    page=client.get('/recycle/').get_data(as_text=True)
    # 合并列表：退拿货行走统一批量端点，版本号按 `version_<id>` 提交。
    assert 'action="/recycle/bulk"' in page and 'data-recycle-submit="restore"' in page
    assert 'data-recycle-version="1"' in page
    assert 'name="recycle_ids"' in page and f'value="purchase_return:{r["order_id"]}"' in page
    # 行内单条操作仍带版本号隐藏域（并发校验）。
    assert f'action="/recycle/purchase_return/{r["order_id"]}/restore"' in page
    entry=client.get('/purchases/return/new').get_data(as_text=True)
    assert len(re.findall(r'class="sidebar-sub active"',entry))==1


def test_recycle_merged_bulk_endpoint_restores_and_purges_mixed_types():
    """统一批量端点：一次提交可混合四类单据，按类型各自套既有恢复/清理规则。"""
    from erp import create_app
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    draft=service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='bulk-mixed-return',status='draft')
    service.delete(draft['order_id'],expected_version=0)
    from erp.services.accounting import create_customer, create_order_from_typed_rows
    sale_customer=create_customer('批量混合销售对象')
    sale=create_order_from_typed_rows(sale_customer,'MD202610020900',[{'product_name':'批量混合商品','unit':'个','unit_price_yuan':'10','quantity':'1'}],status='draft',order_date='2026-10-02')
    from erp.services.accounting import delete_order
    delete_order(sale,'批量混合测试')
    client=create_app().test_client()

    # 混合提交：销售草稿 + 退拿货草稿，一次恢复。
    response=client.post('/recycle/bulk',data={
        'action':['restore'],
        'ids':[f'sale:{sale}',f'purchase_return:{draft["order_id"]}'],
        f'version_{draft["order_id"]}':['1'],
    })
    assert response.status_code==302
    with get_db() as conn:
        assert conn.execute('SELECT deleted_at FROM orders WHERE id=?',(sale,)).fetchone()['deleted_at'] is None
        assert conn.execute('SELECT deleted_at FROM purchase_return_orders WHERE id=?',(draft['order_id'],)).fetchone()['deleted_at'] is None

    # 版本号错 → 整批拒绝，不部分生效。
    delete_order(sale,'批量混合二次')
    service.delete(draft['order_id'],expected_version=2)
    bad=client.post('/recycle/bulk',data={
        'action':['restore'],
        'ids':[f'sale:{sale}',f'purchase_return:{draft["order_id"]}'],
        f'version_{draft["order_id"]}':['99'],
    })
    assert bad.status_code==400
    with get_db() as conn:
        assert conn.execute('SELECT deleted_at FROM purchase_return_orders WHERE id=?',(draft['order_id'],)).fetchone()['deleted_at'] is not None


def test_recycle_bulk_endpoint_rejects_unknown_kind_and_empty_selection():
    from erp import create_app
    client=create_app().test_client()
    assert client.post('/recycle/bulk',data={'action':['restore'],'ids':['bogus:1']}).status_code==400
    assert client.post('/recycle/bulk',data={'action':['restore']}).status_code==400
    assert client.post('/recycle/bulk',data={'action':['explode'],'ids':['sale:1']}).status_code==400


def test_corrupt_empty_draft_cannot_be_finalized():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    r=service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='empty-draft',status='draft')
    with get_db() as conn:conn.execute('DELETE FROM purchase_return_items')
    with pytest.raises(ValueError,match='至少'):
        service.finalize(r['order_id'],expected_version=0)
    with get_db() as conn:assert conn.execute('SELECT status FROM purchase_return_orders').fetchone()[0]=='draft'


def test_default_list_finds_purchase_returns_without_sales_and_customer_rename():
    from erp import create_app
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    service.create(customer_id=cid,source_order_id=source,business_date='2026-10-02',items=[{'source_item_id':iid,'quantity':'1'}],request_key='default-list')
    with get_db() as conn:conn.execute("UPDATE customers SET name='新名字虚构对象' WHERE id=?",(cid,))
    client=create_app().test_client()
    assert b'data-document-key="purchase_return:1"' in client.get('/orders/').data
    assert b'data-document-key="purchase_return:1"' in client.get('/orders/?customer=新名字虚构对象&order_type=purchase_return').data


def test_newer_schema_is_rejected_before_older_code_changes_business_data():
    from erp import db
    cid, pid, source, iid = seed()
    with get_db() as conn:
        conn.execute("INSERT INTO schema_version(version,notes) VALUES (999,'synthetic future schema')")
        before = {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table)]
                  for table in ('schema_version', 'purchase_orders', 'purchase_order_items', 'product_inventory_state', 'inventory_postings')}
    with pytest.raises(RuntimeError, match='不支持'):
        db.init_db()
    with get_db() as conn:
        assert {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table)] for table in before} == before


def test_manual_return_saved_consumes_moving_average_and_void_restores():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    r=service.create(customer_id=cid,business_date='2026-10-02',items=[{'product_id':pid,'quantity':'2','unit_price_yuan':'12.50'}],request_key='manual-saved')
    assert r['total_amount_cents']==2500
    assert r['order_no']=='NH202610020002'  # 与拿货单共用当天 NH 流水
    assert stock(pid)==(1000,10000000)
    with get_db() as conn:
        row=conn.execute('SELECT * FROM purchase_return_items WHERE purchase_return_id=?',(r['order_id'],)).fetchone()
        assert row['source_item_id'] is None and row['unit_price_cents']==1250 and row['subtotal_cents']==2500
        assert row['cost_total_micro']==20000000 and row['posting_seq'] is not None
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    service.void(r['order_id'],'手工更正',expected_version=0)
    assert stock(pid)==(3000,30000000)


def test_manual_return_draft_edit_finalize_locks_economics():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    args=dict(customer_id=cid,business_date='2026-10-02',items=[{'product_id':pid,'quantity':'1','unit_price_yuan':'10'}],request_key='manual-draft',status='draft',notes='初稿')
    draft=service.create(**args)
    assert stock(pid)==(3000,30000000)
    changed=service.update_draft(draft['order_id'],expected_version=0,customer_id=cid,items=[{'product_id':pid,'quantity':'2','unit_price_yuan':'10'}],notes='当前输入',status='saved')
    assert changed['version']==1
    assert stock(pid)==(1000,10000000)
    assert service.create(**args)['order_id']==draft['order_id']
    with pytest.raises(ValueError,match='锁定'):
        service.update_draft(draft['order_id'],expected_version=1,customer_id=cid,items=[{'product_id':pid,'quantity':'1','unit_price_yuan':'10'}])


def test_manual_return_rejects_shortage_and_unknown_product_without_partial_write():
    from erp.services import purchase_returns as service
    cid,pid,source,iid=seed()
    before=stock(pid)
    with pytest.raises(ValueError,match='库存不足'):
        service.create(customer_id=cid,business_date='2026-10-02',items=[{'product_id':pid,'quantity':'4','unit_price_yuan':'10'}],request_key='manual-shortage')
    with pytest.raises(ValueError,match='商品不存在'):
        service.create(customer_id=cid,business_date='2026-10-02',items=[{'product_id':999999,'quantity':'1','unit_price_yuan':'10'}],request_key='manual-missing')
    assert stock(pid)==before
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM purchase_return_orders').fetchone()[0]==0
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_purchase_return_decoupling_migration_relaxes_source_columns():
    """Old NOT NULL/UNIQUE source coupling is rebuilt in place, rows preserved."""
    import sqlite3
    from erp import db
    path=db.db_path()
    old_schema=(db.SCHEMA_SQL
        .replace('source_order_id INTEGER REFERENCES purchase_orders(id),','source_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),')
        .replace('source_item_id INTEGER REFERENCES purchase_order_items(id),','source_item_id INTEGER NOT NULL REFERENCES purchase_order_items(id),'))
    conn=sqlite3.connect(path)
    conn.executescript(old_schema)
    conn.execute("INSERT INTO customers(id,name) VALUES (1,'迁移对象')")
    conn.execute("INSERT INTO products(id,name,unit,default_price_cents) VALUES (1,'迁移商品','个',0)")
    conn.execute("INSERT INTO purchase_orders(id,order_no,business_date,status,request_key,payload_hash,customer_id) VALUES (1,'NH202610010001','2026-10-01','saved','k1','h',1)")
    conn.execute("INSERT INTO purchase_order_items(id,purchase_order_id,product_id,product_name,unit,quantity_3dp,unit_cost_cents,subtotal_cents) VALUES (1,1,1,'迁移商品','个',1000,0,0)")
    conn.execute("INSERT INTO purchase_return_orders(id,customer_id,customer_name,source_order_id,order_no,business_date,total_amount_cents,status,request_key,creation_payload_hash) VALUES (1,1,'迁移对象',1,'TN202610010001','2026-10-01',1000,'saved','r1','h2')")
    conn.execute("INSERT INTO purchase_return_items(id,purchase_return_id,source_item_id,product_id,product_name,unit,quantity_3dp,unit_price_cents,subtotal_cents) VALUES (1,1,1,1,'迁移商品','个',1000,1000,1000)")
    conn.commit();conn.close()
    db.migrate_purchase_return_decoupling()
    conn=sqlite3.connect(path)
    orders_cols={row[1]:row for row in conn.execute('PRAGMA table_info(purchase_return_orders)')}
    items_cols={row[1]:row for row in conn.execute('PRAGMA table_info(purchase_return_items)')}
    assert orders_cols['source_order_id'][3]==0
    assert items_cols['source_item_id'][3]==0
    assert tuple(conn.execute('SELECT source_order_id,order_no FROM purchase_return_orders').fetchone())==(1,'TN202610010001')
    assert tuple(conn.execute('SELECT source_item_id,unit_price_cents FROM purchase_return_items').fetchone())==(1,1000)
    # A manual (source-less) row is now accepted and foreign keys stay consistent.
    conn.execute("INSERT INTO purchase_return_orders(id,customer_id,customer_name,source_order_id,order_no,business_date,status,request_key,creation_payload_hash) VALUES (2,1,'迁移对象',NULL,'TN202610010002','2026-10-01','draft','r2','h3')")
    conn.execute("INSERT INTO purchase_return_items(id,purchase_return_id,source_item_id,product_id,product_name,unit,quantity_3dp,unit_price_cents,subtotal_cents) VALUES (2,2,NULL,1,'迁移商品','个',500,1000,500)")
    conn.commit()
    assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    conn.close()
