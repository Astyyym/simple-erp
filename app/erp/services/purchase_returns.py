"""Source-bound purchase returns; no receivable, payment or sale-price effects."""
from datetime import date
from decimal import Decimal
from erp.db import get_db
from erp.utils.order_numbering import next_nh_order_no
from erp.services.inventory import (_scaled_quantity, _date_value, _payload_hash,
    _purchase_customer_id, _outbound_cost_micro, _average_cost_micro, _price_cents,
    _subtotal_cents, post_typed_purchase_return)
from erp.utils.audit import log_action


def _checked_average(qty, cost):
    if not 0 <= qty <= 9223372036854775807 or not 0 <= cost <= 9223372036854775807:
        raise ValueError('库存数量或成本超出支持范围')
    average = _average_cost_micro(qty,cost)
    if average is not None and average > 9223372036854775807:
        raise ValueError('库存参考成本超出支持范围')
    return average


def _result(order):
    return {"order_id": int(order['id']), "order_no": order['order_no'],
            "total_amount_cents": int(order['total_amount_cents']),
            "status": order['status'], "version": int(order['version'])}


def _source(conn, customer_id, source_order_id):
    source = conn.execute('SELECT * FROM purchase_orders WHERE id=?', (source_order_id,)).fetchone()
    if source is None or source['customer_id'] is None:
        raise ValueError('历史拿货未关联往来对象，当前退拿货路径不支持')
    if source['customer_id'] != customer_id or source['status'] != 'saved' or source['deleted_at'] is not None:
        raise ValueError('原拿货单不存在、对象不符或已失效')
    rows = conn.execute('SELECT * FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id', (source_order_id,)).fetchall()
    if not rows or sum(row['subtotal_cents'] for row in rows) != source['total_amount_cents']:
        raise ValueError('原拿货明细不完整')
    for n, row in enumerate(rows, 1):
        key = str(source_order_id) if len(rows) == 1 else f'{source_order_id}:{n}'
        posting = conn.execute("SELECT * FROM inventory_postings WHERE source_type='purchase' AND source_id=? AND product_id=?", (key, row['product_id'])).fetchone()
        if posting is None or posting['quantity_delta_3dp'] != row['quantity_3dp'] or posting['cost_delta_micro'] < 0:
            raise ValueError('历史拿货缺可靠库存来源流水，当前路径不支持')
    return source, {row['id']: row for row in rows}


def _prepare(items):
    if not items:
        raise ValueError('退拿货至少需要一行商品')
    prepared = []
    for raw in items:
        iid = _purchase_customer_id(raw.get('source_item_id'))
        from erp.services.inventory import _decimal
        quantity = _decimal(raw.get('quantity'), '数量')
        if quantity > Decimal('9223372036854775.807'):
            raise ValueError('数量超出支持范围')
        qty = _scaled_quantity(quantity)
        if qty <= 0:
            raise ValueError('本次数量必须大于0')
        prepared.append({'source_item_id': iid, 'quantity_3dp': qty})
    if len({row['source_item_id'] for row in prepared}) != len(prepared):
        raise ValueError('原拿货行不能重复')
    return sorted(prepared, key=lambda row: row['source_item_id'])


def _prepare_typed(items):
    """Validate decoupled manual rows: 商品 + 数量 + 手输单价。"""
    if not items:
        raise ValueError('退拿货至少需要一行商品')
    from erp.services.inventory import _decimal
    prepared = []
    for raw in items:
        try:
            product_id = int(raw.get('product_id'))
        except (TypeError, ValueError):
            raise ValueError('必须选择具体商品') from None
        quantity = _decimal(raw.get('quantity'), '数量')
        if quantity > Decimal('9223372036854775.807'):
            raise ValueError('数量超出支持范围')
        qty = _scaled_quantity(quantity)
        if qty <= 0:
            raise ValueError('本次数量必须大于0')
        prepared.append({'product_id': product_id, 'quantity_3dp': qty,
                         'unit_price_cents': _price_cents(raw.get('unit_price_yuan'))})
    return prepared


def _typed_total(rows):
    return sum(_subtotal_cents(row['quantity_3dp'], row['unit_price_cents']) for row in rows)


def _write_typed_items(conn, order_id, rows, business_date, *, post):
    for row in rows:
        if conn.execute('SELECT 1 FROM products WHERE id=? AND deleted_at IS NULL', (row['product_id'],)).fetchone() is None:
            raise ValueError('商品不存在或已删除')
    snapshots = (
        post_typed_purchase_return(conn, order_id, [{'product_id': r['product_id'], 'quantity_3dp': r['quantity_3dp']} for r in rows], business_date)
        if post else [{'unit_cost_micro': None, 'cost_total_micro': None, 'posting_seq': None} for _ in rows]
    )
    for row, snapshot in zip(rows, snapshots):
        product = conn.execute('SELECT name, spec, unit FROM products WHERE id=?', (row['product_id'],)).fetchone()
        conn.execute(
            '''INSERT INTO purchase_return_items(purchase_return_id,source_item_id,product_id,product_name,spec,unit,quantity_3dp,unit_price_cents,subtotal_cents,unit_cost_micro,cost_total_micro,posting_seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (order_id, None, row['product_id'], product['name'], product['spec'] or '', product['unit'],
             row['quantity_3dp'], row['unit_price_cents'], _subtotal_cents(row['quantity_3dp'], row['unit_price_cents']),
             snapshot['unit_cost_micro'], snapshot['cost_total_micro'], snapshot['posting_seq']),
        )


def _allocations(conn, customer_id, source_order_id, business_date, prepared, *, draft=False):
    if not prepared:
        raise ValueError('退拿货至少需要一行商品')
    source, rows = _source(conn, customer_id, source_order_id)
    if business_date < source['business_date'] or business_date > date.today().isoformat():
        raise ValueError('退拿货日期不能早于原单或晚于今天')
    result = []
    for item in prepared:
        original = rows.get(item['source_item_id'])
        if original is None:
            raise ValueError('原行不属于所选原拿货单')
        init = conn.execute('SELECT business_date FROM inventory_initializations WHERE product_id=?', (original['product_id'],)).fetchone()
        if init is None or business_date < init['business_date']:
            raise ValueError('商品未启用或日期早于商品期初')
        prior = conn.execute("""SELECT COALESCE(SUM(i.quantity_3dp),0),COALESCE(SUM(i.subtotal_cents),0)
            FROM purchase_return_items i JOIN purchase_return_orders o ON o.id=i.purchase_return_id
            WHERE i.source_item_id=? AND o.status='saved' AND o.deleted_at IS NULL""", (original['id'],)).fetchone()
        if draft:
            prior = (0, 0)
        cumulative = int(prior[0]) + item['quantity_3dp']
        if cumulative > original['quantity_3dp']:
            raise ValueError('本次数量超过原行剩余可退数量')
        amount = (2 * original['subtotal_cents'] * cumulative + original['quantity_3dp']) // (2 * original['quantity_3dp']) - prior[1]
        result.append(dict(original) | item | {'subtotal_cents': amount})
    return source, result


def _write_items(conn, order_id, rows, business_date, *, post):
    for row in rows:
        cost = avg = seq = None
        if post:
            state = conn.execute('SELECT * FROM product_inventory_state WHERE product_id=? AND enabled=1', (row['product_id'],)).fetchone()
            if state is None:
                raise ValueError('商品尚未启用库存')
            cost = _outbound_cost_micro(row['quantity_3dp'], state['quantity_3dp'], state['cost_total_micro'])
            avg = state['avg_cost_micro']
            qty = state['quantity_3dp'] - row['quantity_3dp']
            total = state['cost_total_micro'] - cost
            new_average = _checked_average(qty,total)
            cur = conn.execute("INSERT INTO inventory_postings(product_id,source_type,source_id,quantity_delta_3dp,cost_delta_micro,business_date) VALUES (?,'purchase_return',?,?,?,?)", (row['product_id'], f"{order_id}:{row['source_item_id']}", -row['quantity_3dp'], -cost, business_date))
            seq = cur.lastrowid
            conn.execute('UPDATE product_inventory_state SET quantity_3dp=?,cost_total_micro=?,avg_cost_micro=?,last_posting_seq=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE product_id=?', (qty,total,new_average,seq,row['product_id']))
            conn.execute('UPDATE inventory_initializations SET locked=1 WHERE product_id=?', (row['product_id'],))
        conn.execute('''INSERT INTO purchase_return_items(purchase_return_id,source_item_id,product_id,product_name,spec,unit,quantity_3dp,unit_price_cents,subtotal_cents,unit_cost_micro,cost_total_micro,posting_seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (order_id,row['source_item_id'],row['product_id'],row['product_name'],row['spec'],row['unit'],row['quantity_3dp'],row['unit_cost_cents'],row['subtotal_cents'],avg,cost,seq))


def create(*, customer_id, business_date, items, request_key, status='saved', notes='', source_order_id=None):
    """Create a draft/saved purchase return.

    Decoupled manual rows (no ``source_order_id``) accept 商品 + 数量 + 手输单价 and
    consume inventory at the current moving-average cost; passing a
    ``source_order_id`` keeps the legacy source-bound path for old data.
    """
    customer_id = _purchase_customer_id(customer_id)
    business_date = _date_value(business_date)
    if status not in {'draft','saved'} or not request_key or not str(request_key).strip():
        raise ValueError('状态或幂等键无效')
    notes = (notes or '').strip()
    decoupled = not (source_order_id and str(source_order_id).strip())
    if decoupled:
        prepared = _prepare_typed(items)
        if business_date > date.today().isoformat():
            raise ValueError('退拿货日期不能晚于今天')
        digest = _payload_hash({'kind':'manual','customer_id':customer_id,'business_date':business_date,'items':prepared,'status':status,'notes':notes})
    else:
        source_order_id = _purchase_customer_id(source_order_id)
        prepared = _prepare(items)
        digest = _payload_hash({'kind':'source','customer_id':customer_id,'source_order_id':source_order_id,'business_date':business_date,'items':prepared,'status':status,'notes':notes})
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM purchase_return_orders WHERE request_key=?', (request_key,)).fetchone()
        if existing is not None:
            if existing['creation_payload_hash'] != digest:
                raise ValueError('幂等键内容不一致')
            return _result(existing)
        if decoupled:
            customer = conn.execute('SELECT name FROM customers WHERE id=? AND deleted_at IS NULL', (customer_id,)).fetchone()
            if customer is None:
                raise ValueError('往来对象不存在')
            customer_name = customer['name']
            total_amount = _typed_total(prepared)
        else:
            source, rows = _allocations(conn,customer_id,source_order_id,business_date,prepared,draft=status=='draft')
            customer_name = source['customer_name']
            total_amount = sum(r['subtotal_cents'] for r in rows)
        # 拿货/退拿货共用同一天 NH 流水（取号时会让过历史 TN 流水，旧单号不重写）。
        order_no = next_nh_order_no(conn, business_date)
        cur = conn.execute('''INSERT INTO purchase_return_orders(customer_id,customer_name,source_order_id,order_no,business_date,total_amount_cents,status,notes,request_key,creation_payload_hash,posted_once) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (customer_id,customer_name,None if decoupled else source_order_id,order_no,business_date,total_amount,status,notes,request_key,digest,int(status=='saved')))
        order_id = cur.lastrowid
        if decoupled:
            _write_typed_items(conn,order_id,prepared,business_date,post=status=='saved')
        else:
            _write_items(conn,order_id,rows,business_date,post=status=='saved')
        log_action('create_purchase_return','purchase_return',order_id,'新增退拿货 '+order_no,conn=conn)
        return _result(conn.execute('SELECT * FROM purchase_return_orders WHERE id=?',(order_id,)).fetchone())


def _locked_order(conn, order_id, expected_version, *, deleted=False):
    order = conn.execute('SELECT * FROM purchase_return_orders WHERE id=?', (order_id,)).fetchone()
    if order is None or (order['deleted_at'] is not None) != deleted:
        raise ValueError('退拿货单不存在或可见状态不符')
    if expected_version is None or _version(expected_version) != order['version']:
        raise ValueError('版本冲突，请刷新后重试')
    return order


def _version(value):
    text = str(value)
    if not text.isascii() or not text.isdigit() or int(text) > 9223372036854775807:
        raise ValueError('版本冲突，请刷新后重试')
    return int(text)


def _update_draft(conn, order, customer_id, source_order_id, prepared, notes, status):
    if order['status'] != 'draft' or order['posted_once']:
        raise ValueError('正式退拿货经济字段已锁定')
    if status not in {'draft','saved'}:
        raise ValueError('状态无效')
    source, rows = _allocations(conn, customer_id, source_order_id, order['business_date'], prepared,draft=status=='draft')
    conn.execute('DELETE FROM purchase_return_items WHERE purchase_return_id=?', (order['id'],))
    _write_items(conn, order['id'], rows, order['business_date'], post=status=='saved')
    conn.execute('UPDATE purchase_return_orders SET customer_id=?,customer_name=?,source_order_id=?,total_amount_cents=?,notes=?,status=?,posted_once=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (customer_id, source['customer_name'],source_order_id,sum(r['subtotal_cents'] for r in rows),(notes or '').strip(),status,int(status=='saved'),order['id']))
    log_action('update_purchase_return','purchase_return',order['id'],'保存退拿货当前输入 '+status,conn=conn)
    return _result(conn.execute('SELECT * FROM purchase_return_orders WHERE id=?',(order['id'],)).fetchone())


def _update_draft_typed(conn, order, customer_id, prepared, notes, status):
    if order['status'] != 'draft' or order['posted_once']:
        raise ValueError('正式退拿货经济字段已锁定')
    if status not in {'draft','saved'}:
        raise ValueError('状态无效')
    if not prepared:
        raise ValueError('退拿货至少需要一行商品')
    customer = conn.execute('SELECT name FROM customers WHERE id=? AND deleted_at IS NULL', (customer_id,)).fetchone()
    if customer is None:
        raise ValueError('往来对象不存在')
    conn.execute('DELETE FROM purchase_return_items WHERE purchase_return_id=?', (order['id'],))
    _write_typed_items(conn, order['id'], prepared, order['business_date'], post=status=='saved')
    conn.execute('UPDATE purchase_return_orders SET customer_id=?,customer_name=?,source_order_id=NULL,total_amount_cents=?,notes=?,status=?,posted_once=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (customer_id,customer['name'],_typed_total(prepared),(notes or '').strip(),status,int(status=='saved'),order['id']))
    log_action('update_purchase_return','purchase_return',order['id'],'保存退拿货当前输入 '+status,conn=conn)
    return _result(conn.execute('SELECT * FROM purchase_return_orders WHERE id=?',(order['id'],)).fetchone())


def update_draft(order_id, *, expected_version, customer_id, items, notes='', status='draft', source_order_id=None):
    customer_id = _purchase_customer_id(customer_id)
    decoupled = not (source_order_id and str(source_order_id).strip())
    if decoupled:
        prepared = _prepare_typed(items)
    else:
        source_order_id = _purchase_customer_id(source_order_id)
        prepared = _prepare(items)
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _locked_order(conn, order_id, expected_version)
        if decoupled:
            return _update_draft_typed(conn, order, customer_id, prepared, notes, status)
        return _update_draft(conn,order,customer_id,source_order_id,prepared,notes,status)


def edit_notes(order_id, notes, *, expected_version):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _locked_order(conn,order_id,expected_version)
        conn.execute('UPDATE purchase_return_orders SET notes=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', ((notes or '').strip(),order_id))
        log_action('edit_purchase_return_notes','purchase_return',order_id,'修改退拿货备注',conn=conn)
        return _result(conn.execute('SELECT * FROM purchase_return_orders WHERE id=?',(order_id,)).fetchone())


def finalize(order_id, *, expected_version):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _locked_order(conn, order_id, expected_version)
        if order['source_order_id'] is None:
            items = conn.execute('SELECT product_id,quantity_3dp,unit_price_cents FROM purchase_return_items WHERE purchase_return_id=? ORDER BY id',(order_id,)).fetchall()
            prepared = [{'product_id': int(i['product_id']), 'quantity_3dp': int(i['quantity_3dp']), 'unit_price_cents': int(i['unit_price_cents'])} for i in items]
            return _update_draft_typed(conn, order, order['customer_id'], prepared, order['notes'], 'saved')
        rows = conn.execute('SELECT source_item_id,quantity_3dp FROM purchase_return_items WHERE purchase_return_id=? ORDER BY source_item_id',(order_id,)).fetchall()
        return _update_draft(conn,order,order['customer_id'],order['source_order_id'],[dict(r) for r in rows],order['notes'],'saved')


def void(order_id, reason, *, expected_version):
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('作废原因不能为空')
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _locked_order(conn,order_id,expected_version)
        if order['status'] != 'saved':
            raise ValueError('只有正式退拿货可以作废')
        rows = conn.execute('SELECT p.* FROM purchase_return_items i JOIN inventory_postings p ON p.posting_seq=i.posting_seq WHERE i.purchase_return_id=? ORDER BY p.posting_seq DESC',(order_id,)).fetchall()
        count = conn.execute('SELECT COUNT(*) FROM purchase_return_items WHERE purchase_return_id=?',(order_id,)).fetchone()[0]
        if not rows or len(rows) != count:
            raise ValueError('退拿货库存流水不完整')
        # 「必须是该商品最后一笔库存业务」的旧限制已按用户决策移除（2026-10-07）。
        # 下面每条流水自身的类型/方向校验保留，那是真实性校验而非顺序限制。
        for row in rows:
            if row['source_type'] != 'purchase_return' or row['quantity_delta_3dp'] >= 0 or row['cost_delta_micro'] > 0:
                raise ValueError('退拿货原流水无效')
            state = conn.execute('SELECT * FROM product_inventory_state WHERE product_id=?',(row['product_id'],)).fetchone()
            qty = state['quantity_3dp'] - row['quantity_delta_3dp']
            cost = state['cost_total_micro'] - row['cost_delta_micro']
            average = _checked_average(qty,cost)
            cur = conn.execute("INSERT INTO inventory_postings(product_id,source_type,source_id,quantity_delta_3dp,cost_delta_micro,business_date) VALUES (?,'purchase_return_void',?,?,?,?)",(row['product_id'],row['source_id']+':void',-row['quantity_delta_3dp'],-row['cost_delta_micro'],order['business_date']))
            conn.execute('UPDATE product_inventory_state SET quantity_3dp=?,cost_total_micro=?,avg_cost_micro=?,last_posting_seq=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE product_id=?',(qty,cost,average,cur.lastrowid,row['product_id']))
        conn.execute("UPDATE purchase_return_orders SET status='void',void_reason=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",(reason,order_id))
        log_action('void_purchase_return','purchase_return',order_id,reason,conn=conn)
        return _result(conn.execute('SELECT * FROM purchase_return_orders WHERE id=?',(order_id,)).fetchone())


def delete(order_id, *, expected_version, reason='用户删除'):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _locked_order(conn,order_id,expected_version)
        if order['status'] not in {'draft','void'}:
            raise ValueError('正式退拿货必须先作废再删除')
        conn.execute('UPDATE purchase_return_orders SET deleted_at=CURRENT_TIMESTAMP,delete_reason=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',((reason or '用户删除').strip(),order_id))
        log_action('delete_purchase_return','purchase_return',order_id,'退拿货移入回收站',conn=conn)


def restore(order_id, *, expected_version):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _locked_order(conn,order_id,expected_version,deleted=True)
        conn.execute("UPDATE purchase_return_orders SET deleted_at=NULL,delete_reason='',version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
        log_action('restore_purchase_return','purchase_return',order_id,'仅恢复可见性',conn=conn)


def recycle_batch(ids, versions, *, action):
    from erp.db import physically_deletable_order
    if action not in {'restore','purge'} or not ids or len(ids) != len(versions) or len(set(ids)) != len(ids):
        raise ValueError('批量操作必须提供每张单据及对应版本')
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for order_id, version in zip(ids,versions):
            _locked_order(conn,order_id,version,deleted=True)
            if action == 'purge':
                if not physically_deletable_order(conn,'purchase_return',order_id):
                    raise ValueError('有生效历史或来源引用的退拿货不能永久删除')
                conn.execute('DELETE FROM purchase_return_orders WHERE id=?',(order_id,))
            else:
                conn.execute("UPDATE purchase_return_orders SET deleted_at=NULL,delete_reason='',version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
            log_action(action+'_purchase_return','purchase_return',order_id,'回收站 '+action,conn=conn)


def void_then_delete(order_id, *, expected_version, reason='列表删除（自动冲回）'):
    """Delete a 退拿货 from the list, voiding it first when it is live.

    ``void`` reverses the outbound postings (conservation-checked); ``delete`` then
    moves the document to the recycle bin. Errors from either step propagate.
    """
    with get_db() as conn:
        order = conn.execute('SELECT status,version,deleted_at FROM purchase_return_orders WHERE id=?',(order_id,)).fetchone()
    if order is None or order['deleted_at'] is not None:
        raise ValueError('退拿货不存在或已在回收站')
    if order['status'] == 'saved':
        void(order_id,reason,expected_version=expected_version)
        with get_db() as conn:
            current = conn.execute('SELECT version FROM purchase_return_orders WHERE id=?',(order_id,)).fetchone()['version']
        delete(order_id,expected_version=current,reason=reason)
        return
    delete(order_id,expected_version=expected_version,reason=reason)
