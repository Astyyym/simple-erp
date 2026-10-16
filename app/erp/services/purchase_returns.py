"""Source-bound purchase returns; no receivable, payment or sale-price effects."""
from datetime import date
from decimal import Decimal
from erp.db import get_db
from erp.utils.order_numbering import next_nh_order_no
from erp.services.purchases import (_scaled_quantity, _date_value, _payload_hash,
    _purchase_customer_id, _price_cents, _subtotal_cents, _decimal)
from erp.utils.audit import log_action


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
    return source, {row['id']: row for row in rows}


def _prepare(items):
    """整备**源绑定**退拿货行：数量 + 可选单位覆盖。

    §4.3 / #20：单位默认沿用源拿货单快照（由 `_allocations` 的 `dict(original) | item` 合并得到），
    若表单提交了 unit 则以提交值为准。
    """
    if not items:
        raise ValueError('退拿货至少需要一行商品')
    prepared = []
    for raw in items:
        iid = _purchase_customer_id(raw.get('source_item_id'))
        quantity = _decimal(raw.get('quantity'), '数量')
        if quantity > Decimal('9223372036854775.807'):
            raise ValueError('数量超出支持范围')
        qty = _scaled_quantity(quantity)
        if qty <= 0:
            raise ValueError('本次数量必须大于0')
        row = {'source_item_id': iid, 'quantity_3dp': qty}
        raw_unit = str(raw.get('unit') or '').strip()
        if raw_unit:
            row['unit'] = raw_unit
        prepared.append(row)
    if len({row['source_item_id'] for row in prepared}) != len(prepared):
        raise ValueError('原拿货行不能重复')
    return sorted(prepared, key=lambda row: row['source_item_id'])


def _prepare_typed(items):
    """Validate decoupled manual rows: 商品 + 数量 + 手输单价（+ 可选单位）。"""
    if not items:
        raise ValueError('退拿货至少需要一行商品')
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
                         'unit_price_cents': _price_cents(raw.get('unit_price_yuan')),
                         'unit': str(raw.get('unit') or '').strip()})
    return prepared


def _all_rows_bound(items) -> bool:
    """明细行是否**全部**绑了原拿货行；只要有一行手输就返回 False（走手输路径）。"""
    items = list(items or [])
    return bool(items) and all(str(raw.get('source_item_id') or '').strip() for raw in items)


def _assert_trace_source(conn, source_order_id, customer_id):
    """原拿货单只作追溯：存在、已正式保存、属于同一往来对象即可，不约束明细行。"""
    row = conn.execute(
        "SELECT id, customer_id FROM purchase_orders WHERE id=? AND deleted_at IS NULL AND status='saved'",
        (source_order_id,),
    ).fetchone()
    if row is None:
        raise ValueError('原拿货单不存在或未正式保存')
    if int(row['customer_id']) != int(customer_id):
        raise ValueError('原拿货单必须属于同一往来对象')


def _typed_total(rows):
    return sum(_subtotal_cents(row['quantity_3dp'], row['unit_price_cents']) for row in rows)


def _write_typed_items(conn, order_id, rows, business_date):
    """写入手输（解耦）退拿货明细。

    v15 起不再有成本快照与库存流水（用户决策 ②/③）：明细只存名称/单位/数量/单价/小计，
    不再有 `unit_cost_micro` / `cost_total_micro` / `posting_seq`。
    """
    for row in rows:
        if conn.execute('SELECT 1 FROM products WHERE id=? AND deleted_at IS NULL', (row['product_id'],)).fetchone() is None:
            raise ValueError('商品不存在或已删除')
    for row in rows:
        product = conn.execute('SELECT name, unit FROM products WHERE id=?', (row['product_id'],)).fetchone()
        # 手工退拿货无源单：提交空单位回落商品档案单位；不回写 products.unit。
        row_unit = str(row.get('unit') or '').strip() or str(product['unit'] or '')
        conn.execute(
            '''INSERT INTO purchase_return_items(purchase_return_id,source_item_id,product_id,product_name,unit,quantity_3dp,unit_price_cents,subtotal_cents) VALUES (?,?,?,?,?,?,?,?)''',
            (order_id, None, row['product_id'], product['name'], row_unit,
             row['quantity_3dp'], row['unit_price_cents'], _subtotal_cents(row['quantity_3dp'], row['unit_price_cents'])),
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


def _write_items(conn, order_id, rows, business_date):
    """写入源绑定（有原拿货单）退拿货明细。v15 起不再有成本快照与库存流水。"""
    for row in rows:
        conn.execute('''INSERT INTO purchase_return_items(purchase_return_id,source_item_id,product_id,product_name,unit,quantity_3dp,unit_price_cents,subtotal_cents) VALUES (?,?,?,?,?,?,?,?)''', (order_id,row['source_item_id'],row['product_id'],row['product_name'],row['unit'],row['quantity_3dp'],row['unit_cost_cents'],row['subtotal_cents']))


def create(*, customer_id, business_date, items, request_key, status='saved', notes='', source_order_id=None):
    """Create a draft/saved purchase return.

    Decoupled manual rows (no ``source_order_id``) accept 商品 + 数量 + 手输单价; passing a
    ``source_order_id`` keeps the legacy source-bound path for old data.
    v15 起两种路径都不再消耗库存、不再产生成本（用户决策 ②/③）。
    """
    customer_id = _purchase_customer_id(customer_id)
    business_date = _date_value(business_date)
    if status not in {'draft','saved'} or not request_key or not str(request_key).strip():
        raise ValueError('状态或幂等键无效')
    notes = (notes or '').strip()
    trace_source_id = _purchase_customer_id(source_order_id) if source_order_id and str(source_order_id).strip() else None
    # 2026-10-13：原单关联只作**参考 / 追溯** —— 只要有一行是手输（没绑原拿货行），
    # 整单就走手输路径（手输单价 + 当前移动平均成本），但单据上仍记住原单号。
    decoupled = trace_source_id is None or not _all_rows_bound(items)
    if decoupled:
        prepared = _prepare_typed(items)
        if business_date > date.today().isoformat():
            raise ValueError('退拿货日期不能晚于今天')
        digest = _payload_hash({'kind':'manual','customer_id':customer_id,'business_date':business_date,'items':prepared,'status':status,'notes':notes,'source_order_id':trace_source_id})
    else:
        source_order_id = trace_source_id
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
            if trace_source_id is not None:
                _assert_trace_source(conn, trace_source_id, customer_id)
            total_amount = _typed_total(prepared)
        else:
            source, rows = _allocations(conn,customer_id,source_order_id,business_date,prepared,draft=status=='draft')
            customer_name = source['customer_name']
            total_amount = sum(r['subtotal_cents'] for r in rows)
        # 拿货/退拿货共用同一天 NH 流水（取号时会让过历史 TN 流水，旧单号不重写）。
        order_no = next_nh_order_no(conn, business_date)
        cur = conn.execute('''INSERT INTO purchase_return_orders(customer_id,customer_name,source_order_id,order_no,business_date,total_amount_cents,status,notes,request_key,creation_payload_hash) VALUES (?,?,?,?,?,?,?,?,?,?)''', (customer_id,customer_name,trace_source_id,order_no,business_date,total_amount,status,notes,request_key,digest))
        order_id = cur.lastrowid
        if decoupled:
            _write_typed_items(conn,order_id,prepared,business_date)
        else:
            _write_items(conn,order_id,rows,business_date)
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
    # 2026-10-16（决策 ④）：正式单编辑锁全撤——`posted_once` 列已随 v15 删除，
    # 「正式退拿货经济字段已锁定」不再是规则。只剩状态合法性校验。
    if status not in {'draft','saved'}:
        raise ValueError('状态无效')
    source, rows = _allocations(conn, customer_id, source_order_id, order['business_date'], prepared,draft=status=='draft')
    conn.execute('DELETE FROM purchase_return_items WHERE purchase_return_id=?', (order['id'],))
    _write_items(conn, order['id'], rows, order['business_date'])
    conn.execute('UPDATE purchase_return_orders SET customer_id=?,customer_name=?,source_order_id=?,total_amount_cents=?,notes=?,status=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (customer_id, source['customer_name'],source_order_id,sum(r['subtotal_cents'] for r in rows),(notes or '').strip(),status,order['id']))
    log_action('update_purchase_return','purchase_return',order['id'],'保存退拿货当前输入 '+status,conn=conn)
    return _result(conn.execute('SELECT * FROM purchase_return_orders WHERE id=?',(order['id'],)).fetchone())


def _update_draft_typed(conn, order, customer_id, prepared, notes, status):
    # 同上（决策 ④）：正式单不再锁编辑。
    if status not in {'draft','saved'}:
        raise ValueError('状态无效')
    if not prepared:
        raise ValueError('退拿货至少需要一行商品')
    customer = conn.execute('SELECT name FROM customers WHERE id=? AND deleted_at IS NULL', (customer_id,)).fetchone()
    if customer is None:
        raise ValueError('往来对象不存在')
    conn.execute('DELETE FROM purchase_return_items WHERE purchase_return_id=?', (order['id'],))
    _write_typed_items(conn, order['id'], prepared, order['business_date'])
    conn.execute('UPDATE purchase_return_orders SET customer_id=?,customer_name=?,source_order_id=NULL,total_amount_cents=?,notes=?,status=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (customer_id,customer['name'],_typed_total(prepared),(notes or '').strip(),status,order['id']))
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
        # v15（决策 ②/③）：作废不再冲回库存流水——库存系统已删除，只剩状态与原因。
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

    v15 起作废不再冲回库存流水（库存系统已删除），这条路径只剩「正式单不能直接删」的
    状态机要求；两步的错误都照常抛出。
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
