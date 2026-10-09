from __future__ import annotations

import secrets
from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for, jsonify
import sqlite3

from erp.db import get_db
from erp.utils.audit import log_action
from erp.utils.errors import error_response, not_found, RecordNotFound
from erp.utils.money import cents_to_yuan, micro_to_yuan
from erp.utils.order_numbering import next_nh_order_no, peek_nh_order_no
from erp.services.inventory import (
    create_purchase_order,
    finalize_purchase_order,
    update_purchase_draft,
    void_purchase_order,
)

purchases_bp = Blueprint("purchases", __name__, url_prefix="/purchases")


def _has_purchase_postings(conn, purchase_id: int) -> bool:
    """拿货单是否已入库（有 purchase 流水）；有流水即不可再改经济字段。"""
    return conn.execute(
        "SELECT 1 FROM inventory_postings WHERE source_type='purchase' AND (source_id=? OR source_id LIKE ?) LIMIT 1",
        (str(purchase_id), f"{purchase_id}:%"),
    ).fetchone() is not None


def next_purchase_no(conn, business_date: str) -> str:
    """拿货单取号：与退拿货共用同一套 NH 日流水。"""
    return next_nh_order_no(conn, business_date)


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def next_return_no(conn, business_date: str) -> str:
    """退拿货取号：与拿货共用同一套 NH 日流水。"""
    return next_nh_order_no(conn, business_date)


def _products():
    with get_db() as conn:
        return conn.execute(
            """SELECT p.id, p.name, p.spec, p.unit
                      , COALESCE(s.enabled, 0) AS inventory_enabled
                      , s.avg_cost_micro, s.quantity_3dp
               FROM products AS p LEFT JOIN product_inventory_state AS s ON s.product_id=p.id
               WHERE p.deleted_at IS NULL ORDER BY p.name COLLATE NOCASE, p.spec, p.id"""
        ).fetchall()


def _purchase_catalog(products) -> list[dict]:
    """Compact product list for the shared two-level picker, plus current moving-average cost."""
    catalog = []
    for p in products:
        enabled = bool(p["inventory_enabled"])
        cost_available = bool(enabled and p["avg_cost_micro"] is not None and p["quantity_3dp"])
        catalog.append({
            "id": int(p["id"]), "name": p["name"], "spec": p["spec"] or "", "unit": p["unit"],
            "inventory_enabled": enabled,
            "cost_available": cost_available,
            "unit_cost": micro_to_yuan(p["avg_cost_micro"], precision=6) if cost_available else None,
        })
    return catalog


def _resolve_purchase_customer_id() -> int:
    """Resolve the counterparty for 拿货/退拿货: free text with autocomplete, auto-create when new."""
    name = (request.form.get("customer_name") or "").strip()
    id_text = (request.form.get("customer_id") or "").strip()
    with get_db() as conn:
        if id_text:
            try:
                customer_id = int(id_text)
            except (TypeError, ValueError):
                raise ValueError("往来对象选择无效")
            existing = conn.execute(
                "SELECT id FROM customers WHERE id=? AND deleted_at IS NULL", (customer_id,)
            ).fetchone()
            if existing is None:
                raise ValueError("往来对象不存在，请重新选择")
            return customer_id
        if not name:
            raise ValueError("往来对象不能为空")
        existing = conn.execute("SELECT id, deleted_at FROM customers WHERE name=?", (name,)).fetchone()
        if existing:
            customer_id = int(existing["id"])
            if existing["deleted_at"] is not None:
                # G-9：命中回收站对象 → 复用并恢复可见性（与销售侧统一），并留痕。
                conn.execute(
                    "UPDATE customers SET deleted_at=NULL, delete_reason='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (customer_id,),
                )
                log_action(
                    "restore_customer_on_order",
                    "customer",
                    customer_id,
                    f"拿货复用回收站对象档案并恢复可见性：{name}",
                    conn=conn,
                )
            return customer_id
        cur = conn.execute("INSERT INTO customers(name, origin) VALUES (?, 'order')", (name,))
        new_id = int(cur.lastrowid)
        log_action("auto_create_customer", "customer", new_id, f"拿货自动建档对象：{name}", conn=conn)
        return new_id


def _purchase_today_history(conn, today: str) -> list[dict]:
    items: list[dict] = []
    rows = conn.execute(
        """SELECT id, order_no, customer_name, status, total_amount_cents FROM purchase_orders
           WHERE business_date=? AND deleted_at IS NULL AND status='saved' ORDER BY id DESC""",
        (today,),
    ).fetchall()
    for r in rows:
        items.append({
            "id": r["id"], "order_no": r["order_no"], "customer_name": r["customer_name"] or "（未知对象）",
            "order_type": "purchase", "type_label": "拿货单",
            "amount_display": cents_to_yuan(r["total_amount_cents"]),
            "status": r["status"], "status_label": "正式保存",
            "detail_url": url_for("purchases.purchase_detail", purchase_id=r["id"]),
        })
    rows = conn.execute(
        """SELECT id, order_no, customer_name, status, total_amount_cents FROM purchase_return_orders
           WHERE business_date=? AND deleted_at IS NULL AND status='saved' ORDER BY id DESC""",
        (today,),
    ).fetchall()
    for r in rows:
        items.append({
            "id": r["id"], "order_no": r["order_no"], "customer_name": r["customer_name"] or "（未知对象）",
            "order_type": "purchase_return", "type_label": "退拿货单",
            "amount_display": cents_to_yuan(r["total_amount_cents"]),
            "status": r["status"], "status_label": "正式保存",
            "detail_url": url_for("purchases.return_detail", return_id=r["id"]),
        })
    return items


@purchases_bp.get("/new")
def new_purchase():
    return _purchase_form(form_rows=[{}], request_key=secrets.token_urlsafe(24))


def _purchase_form(*, order=None, error="", form_rows=None, request_key=""):
    posted_id = (request.form.get("customer_id") or "").strip()
    posted_name = (request.form.get("customer_name") or "").strip()
    order_id = str(order["customer_id"]) if order and order["customer_id"] else ""
    selected_id = posted_id or order_id
    selected_name = posted_name
    products = _products()
    catalog = _purchase_catalog(products)
    today = date.today().isoformat()
    with get_db() as conn:
        if not selected_name and selected_id:
            row = conn.execute("SELECT name FROM customers WHERE id=?", (selected_id,)).fetchone()
            if row:
                selected_name = row["name"]
        if not selected_name and order:
            selected_name = order["customer_name"]
        if order is not None:
            order_no = order["order_no"]
        else:
            try:
                order_no = peek_nh_order_no(conn, today)
            except ValueError:
                order_no = ""
        today_history = _purchase_today_history(conn, today)
    return render_template(
        "purchases/new.html", products=products, product_catalog=catalog,
        selected_customer_id=selected_id, selected_customer_name=selected_name,
        today=today, order=order, error=error, form_rows=form_rows or [{}],
        request_key=request_key, order_no=order_no, today_history=today_history,
    )


@purchases_bp.post("/create")
def create_purchase():
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    costs = request.form.getlist("unit_cost_yuan")

    business_date = (request.form.get("business_date") or "").strip()
    request_key = (request.form.get("request_key") or "").strip() or secrets.token_urlsafe(24)
    status = request.form.get("status", "saved").strip()
    if status not in {"draft", "saved"}:
        status = "saved"
    form_rows = [
        {"product_id": product_ids[i] if i < len(product_ids) else "", "quantity": quantities[i] if i < len(quantities) else "", "unit_cost_yuan": costs[i] if i < len(costs) else ""}
        for i in range(max(len(product_ids), len(quantities), len(costs), 1))
    ]
    rows = [row for row in form_rows if any(str(value).strip() for value in row.values())]
    try:
        with get_db() as conn:
            existing = conn.execute("SELECT order_no FROM purchase_orders WHERE request_key=?", (request_key,)).fetchone()
            order_no = existing["order_no"] if existing is not None else next_purchase_no(conn, business_date)
        customer_id = _resolve_purchase_customer_id()
        result = create_purchase_order(
            order_no,
            business_date,
            rows,
            request_key=request_key,
            customer_id=customer_id,
            source_notes=request.form.get("source_notes", ""),
            status=status,
        )
    except (TypeError, ValueError) as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"ok": False, "message": str(exc)}), 400
        return _purchase_form(
            error=str(exc),
            form_rows=form_rows,
            request_key=(request.form.get("request_key") or "").strip() or secrets.token_urlsafe(24),
        ), 400
    # C2：保存返回 JSON（含 pdf_url），保存并打印成功后面板自动打预览。
    # 拿货/退拿货没有「已打印」状态：入库即 determined by status=saved，不额外标记。
    want_print = request.form.get("save_action") == "save_print"
    pdf_path = url_for("purchases.purchase_pdf", purchase_id=result["order_id"])
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({
            "ok": True,
            "order_id": result["order_id"],
            "order_no": result["order_no"],
            "printed": False,
            "pdf_url": request.host_url.rstrip("/") + pdf_path,
            "detail_url": url_for("purchases.purchase_detail", purchase_id=result["order_id"]),
            "next_url": url_for("purchases.new_purchase"),
        })
    if want_print:
        return redirect(pdf_path, code=303)
    return redirect(url_for("purchases.purchase_detail", purchase_id=result["order_id"]))


@purchases_bp.get("/<int:purchase_id>")
def purchase_detail(purchase_id: int):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (purchase_id,)).fetchone()
        if order is None:
            return not_found("拿货单不存在，可能已被移入回收站或从未存在。", back_url=url_for("orders.list_orders"))
        items = conn.execute("SELECT * FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id", (purchase_id,)).fetchall()
    return render_template("purchases/detail.html", order=order, items=items)


@purchases_bp.get("/<int:purchase_id>/edit")
def edit_purchase(purchase_id: int):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (purchase_id,)).fetchone()
        items = conn.execute("SELECT * FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id", (purchase_id,)).fetchall()
        has_postings = _has_purchase_postings(conn, purchase_id)
    if order is None:
        return not_found("拿货单不存在，可能已被移入回收站或从未存在。", back_url=url_for("orders.list_orders"))
    if order["status"] != "draft":
        return error_response("正式拿货单经济字段已锁定，只能回看。", title="无法编辑",
                              back_url=url_for("purchases.purchase_detail", purchase_id=purchase_id))
    if has_postings:
        return error_response("该拿货单已入库，经济字段不能再改。请用「回看」核对。", title="无法编辑",
                              back_url=url_for("purchases.purchase_detail", purchase_id=purchase_id))
    return _purchase_form(
        order=order,
        form_rows=[dict(item) | {"quantity": f"{item['quantity_3dp'] / 1000:g}", "unit_cost_yuan": f"{item['unit_cost_cents'] / 100:.2f}"} for item in items],
        request_key=order["request_key"],
    )


@purchases_bp.post("/<int:purchase_id>/edit")
def update_purchase(purchase_id: int):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (purchase_id,)).fetchone()
    if order is None:
        return not_found("拿货单不存在，可能已被移入回收站或从未存在。", back_url=url_for("orders.list_orders"))
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    costs = request.form.getlist("unit_cost_yuan")
    rows = [
        {"product_id": product_ids[i] if i < len(product_ids) else "", "quantity": quantities[i] if i < len(quantities) else "", "unit_cost_yuan": costs[i] if i < len(costs) else ""}
        for i in range(max(len(product_ids), len(quantities), len(costs), 1))
    ]
    try:
        customer_id = _resolve_purchase_customer_id()
        update_purchase_draft(
            purchase_id,
            request.form.get("business_date", ""),
            rows,
            customer_id=customer_id,
            source_notes=request.form.get("source_notes", ""),
            status=request.form.get("status", "draft"),
        )
    except (TypeError, ValueError) as exc:
        return _purchase_form(order=order, error=str(exc), form_rows=rows, request_key=order["request_key"]), 400
    return redirect(url_for("purchases.purchase_detail", purchase_id=purchase_id))


@purchases_bp.post("/<int:purchase_id>/finalize")
def finalize_purchase(purchase_id: int):
    if any(name in request.form for name in ("customer_id", "product_id", "quantity", "unit_cost_yuan", "source_notes", "business_date")):
        return error_response("此入口仅入库已保存草稿；请在编辑页保存当前填写内容并入库。", title="未能入库",
                              back_url=url_for("purchases.purchase_detail", purchase_id=purchase_id))
    try:
        finalize_purchase_order(purchase_id)
    except ValueError as exc:
        return error_response(str(exc), title="未能入库",
                              back_url=url_for("purchases.purchase_detail", purchase_id=purchase_id))
    return redirect(url_for("purchases.purchase_detail", purchase_id=purchase_id))


@purchases_bp.post("/<int:purchase_id>/void")
def void_purchase(purchase_id: int):
    try:
        void_purchase_order(purchase_id, request.form.get("reason", ""))
    except ValueError as exc:
        return error_response(str(exc), title="拿货单未能作废",
                              back_url=url_for("purchases.purchase_detail", purchase_id=purchase_id))
    return redirect(url_for("purchases.purchase_detail", purchase_id=purchase_id))


@purchases_bp.post("/<int:purchase_id>/delete")
def delete_purchase(purchase_id: int):
    """Row/list delete for 拿货单: live documents are reversed (voided) first."""
    from erp.services.inventory import void_then_delete_purchase_order

    try:
        void_then_delete_purchase_order(purchase_id)
    except ValueError as exc:
        return error_response(str(exc), title="拿货单未能删除", back_url=url_for("recycle.recycle_bin"))
    return redirect(url_for("recycle.recycle_bin"))


def _return_source_data(customer_id, source_order_id=''):
    from erp.services.purchase_returns import _source
    from erp.services.inventory import _purchase_customer_id
    customer_id = _purchase_customer_id(customer_id)
    with get_db() as conn:
        candidates = conn.execute("SELECT id,order_no,business_date,total_amount_cents FROM purchase_orders WHERE customer_id=? AND status='saved' AND deleted_at IS NULL ORDER BY business_date DESC,id DESC",(customer_id,)).fetchall()
        sources = []
        for row in candidates:
            try:
                _source(conn,customer_id,row['id'])
            except ValueError:
                continue
            sources.append(dict(row))
        items = []
        if source_order_id:
            source_order_id = _purchase_customer_id(source_order_id)
            _, rows = _source(conn,customer_id,source_order_id)
            for row in rows.values():
                prior = conn.execute("SELECT COALESCE(SUM(i.quantity_3dp),0) FROM purchase_return_items i JOIN purchase_return_orders o ON o.id=i.purchase_return_id WHERE i.source_item_id=? AND o.status='saved' AND o.deleted_at IS NULL",(row['id'],)).fetchone()[0]
                state = conn.execute('SELECT * FROM product_inventory_state WHERE product_id=?',(row['product_id'],)).fetchone()
                items.append(dict(row) | {'source_item_id':row['id'],'returned_quantity_3dp':prior,'remaining_quantity_3dp':row['quantity_3dp']-prior,'stock_quantity_3dp':state['quantity_3dp'] if state and state['enabled'] else None,'avg_cost_micro':state['avg_cost_micro'] if state and state['enabled'] else None})
        return {'sources':sources,'items':items}


@purchases_bp.get('/return/sources')
def return_sources():
    try:
        return jsonify(_return_source_data(request.args.get('customer_id',''),request.args.get('source_order_id','')))
    except ValueError as exc:
        return jsonify(error=str(exc)),400


def _return_form(order=None, error='', form_rows=None, request_key=''):
    posted_id = (request.form.get('customer_id') or '').strip()
    posted_name = (request.form.get('customer_name') or '').strip()
    order_id = str(order['customer_id']) if order and order['customer_id'] else ''
    selected_id = posted_id or order_id
    selected_name = posted_name
    products = _products()
    catalog = _purchase_catalog(products)
    today = date.today().isoformat()
    with get_db() as conn:
        if not selected_name and selected_id:
            row = conn.execute('SELECT name FROM customers WHERE id=?', (selected_id,)).fetchone()
            if row:
                selected_name = row['name']
        if not selected_name and order:
            selected_name = order['customer_name']
        if order is not None:
            order_no = order['order_no']
        else:
            try:
                order_no = peek_nh_order_no(conn, today)
            except ValueError:
                order_no = ''
        today_history = _purchase_today_history(conn, today)
        purchase_sources = conn.execute(
            """SELECT id, order_no, customer_name, business_date
                 FROM purchase_orders
                WHERE deleted_at IS NULL AND status='saved'
                ORDER BY business_date DESC, id DESC LIMIT 200"""
        ).fetchall() if order is None else []
    return render_template('purchases/return_new.html',order=order,error=error,
                           selected_customer_id=selected_id,selected_customer_name=selected_name,
                           product_catalog=catalog,products=products,
                           form_rows=form_rows or [{}],today=today,
                           order_no=order_no,today_history=today_history,
                           purchase_sources=purchase_sources,
                           request_key=request_key or secrets.token_urlsafe(24))


@purchases_bp.get('/return/new')
def new_return():
    return _return_form()


def _typed_return_rows():
    ids = request.form.getlist('product_id')
    quantities = request.form.getlist('quantity')
    prices = request.form.getlist('unit_price_yuan')
    return [
        {'product_id': ids[i] if i < len(ids) else '',
         'quantity': quantities[i] if i < len(quantities) else '',
         'unit_price_yuan': prices[i] if i < len(prices) else ''}
        for i in range(max(len(ids), len(quantities), len(prices), 1))
    ]


@purchases_bp.post('/return/create')
def create_return():
    from erp.services import purchase_returns as service
    form_rows = _typed_return_rows()
    rows = [row for row in form_rows if any(str(value).strip() for value in row.values())]
    try:
        customer_id = _resolve_purchase_customer_id()
        result = service.create(customer_id=customer_id,business_date=request.form.get('business_date',''),items=rows,request_key=request.form.get('request_key',''),status=request.form.get('status','saved'),notes=request.form.get('notes',''))
    except (ValueError,sqlite3.OperationalError) as exc:
        message = str(exc) if isinstance(exc,ValueError) else '数据库忙，请稍后重试'
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"ok": False, "message": message}), 400
        return _return_form(error=message,form_rows=form_rows,request_key=request.form.get('request_key','')),400
    want_print = request.form.get("save_action") == "save_print"
    pdf_path = url_for("purchases.purchase_return_pdf", return_id=result["order_id"])
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({
            "ok": True,
            "order_id": result["order_id"],
            "order_no": result["order_no"],
            "printed": False,
            "pdf_url": request.host_url.rstrip("/") + pdf_path,
            "detail_url": url_for("purchases.return_detail", return_id=result["order_id"]),
            "next_url": url_for("purchases.new_return"),
        })
    if want_print:
        return redirect(pdf_path, code=303)
    return redirect(url_for('purchases.return_detail',return_id=result['order_id']))


@purchases_bp.get('/return/<int:return_id>')
def return_detail(return_id):
    with get_db() as conn:
        order = conn.execute('SELECT * FROM purchase_return_orders WHERE id=? AND deleted_at IS NULL',(return_id,)).fetchone()
        if order is None:
            return not_found('退拿货单不存在，可能已被移入回收站或从未存在。', back_url=url_for('orders.list_orders'))
        items = conn.execute('SELECT * FROM purchase_return_items WHERE purchase_return_id=? ORDER BY id',(return_id,)).fetchall()
        source = conn.execute('SELECT id,order_no FROM purchase_orders WHERE id=?',(order['source_order_id'],)).fetchone()
    return render_template('purchases/return_detail.html',order=order,items=items,source=source)


@purchases_bp.get('/<int:purchase_id>/pdf')
def purchase_pdf(purchase_id: int):
    from erp.utils.purchase_pdf import generate_purchase_pdf
    from erp.utils.pdf import send_pdf_for_preview
    try:
        path = generate_purchase_pdf(purchase_id)
    except RecordNotFound as exc:
        return not_found(str(exc), title="拿货单不存在", back_url=url_for('orders.list_orders'))
    except ValueError as exc:
        return error_response(str(exc), title="拿货清单未能生成", back_url=url_for('orders.list_orders'))
    return send_pdf_for_preview(path, f'purchase_{purchase_id}.pdf', '拿货清单')


@purchases_bp.get('/return/<int:return_id>/pdf')
def purchase_return_pdf(return_id: int):
    from erp.utils.purchase_pdf import generate_purchase_pdf
    from erp.utils.pdf import send_pdf_for_preview
    try:
        path = generate_purchase_pdf(return_id, is_return=True)
    except RecordNotFound as exc:
        return not_found(str(exc), title="退拿货单不存在", back_url=url_for('orders.list_orders'))
    except ValueError as exc:
        return error_response(str(exc), title="退拿货清单未能生成", back_url=url_for('orders.list_orders'))
    return send_pdf_for_preview(path, f'purchase_return_{return_id}.pdf', '退拿货清单')


@purchases_bp.get('/return/<int:return_id>/edit')
def edit_return(return_id):
    with get_db() as conn:
        order = conn.execute('SELECT * FROM purchase_return_orders WHERE id=? AND deleted_at IS NULL',(return_id,)).fetchone()
        items = conn.execute('SELECT product_id,quantity_3dp,unit_price_cents FROM purchase_return_items WHERE purchase_return_id=? ORDER BY id',(return_id,)).fetchall()
    if order is None:
        return not_found('退拿货单不存在，可能已被移入回收站或从未存在。', back_url=url_for('orders.list_orders'))
    if order['status'] != 'draft':
        return error_response('正式退拿货经济字段已锁定，请在详情修改备注。', title="无法编辑",
                              back_url=url_for('purchases.return_detail', return_id=return_id))
    return _return_form(order=order,form_rows=[{'product_id':i['product_id'],'quantity':f"{i['quantity_3dp']/1000:g}",'unit_price_yuan':f"{i['unit_price_cents']/100:.2f}"} for i in items],request_key=order['request_key'])


@purchases_bp.post('/return/<int:return_id>/edit')
def update_return(return_id):
    from erp.services import purchase_returns as service
    with get_db() as conn:
        order = conn.execute('SELECT * FROM purchase_return_orders WHERE id=? AND deleted_at IS NULL',(return_id,)).fetchone()
    if order is None:
        return not_found('退拿货单不存在，可能已被移入回收站或从未存在。', back_url=url_for('orders.list_orders'))
    form_rows = _typed_return_rows()
    rows = [row for row in form_rows if any(str(value).strip() for value in row.values())]
    try:
        customer_id = _resolve_purchase_customer_id()
        service.update_draft(return_id,expected_version=request.form.get('version'),customer_id=customer_id,items=rows,notes=request.form.get('notes',''),status=request.form.get('status','draft'))
    except (ValueError,sqlite3.OperationalError) as exc:
        return _return_form(order=order,error=str(exc) if isinstance(exc,ValueError) else '数据库忙，请稍后重试',form_rows=form_rows,request_key=order['request_key']),400
    return redirect(url_for('purchases.return_detail',return_id=return_id))


@purchases_bp.post('/return/<int:return_id>/<action>')
def return_action(return_id, action):
    from erp.services import purchase_returns as service
    try:
        version = request.form.get('version')
        if action == 'finalize':
            if any(k in request.form for k in ('quantity','source_item_id','customer_id','source_order_id','business_date','notes')):
                raise ValueError('此入口仅将已保存草稿出库；请在编辑页保存当前输入')
            service.finalize(return_id,expected_version=version)
        elif action == 'void':
            service.void(return_id,request.form.get('reason',''),expected_version=version)
        elif action == 'delete':
            service.void_then_delete(return_id,expected_version=version)
            return redirect(url_for('recycle.recycle_bin'))
        elif action == 'notes':
            if any(k in request.form for k in ('quantity','source_item_id','customer_id','source_order_id','business_date','status')):
                raise ValueError('备注入口不能修改经济字段')
            service.edit_notes(return_id,request.form.get('notes',''),expected_version=version)
        else:
            return not_found('未知退拿货操作。', back_url=url_for('orders.list_orders'))
    except (ValueError,sqlite3.OperationalError) as exc:
        return error_response(
            str(exc) if isinstance(exc, ValueError) else '数据库忙，请稍后重试',
            title="退拿货操作未完成",
            back_url=url_for('purchases.return_detail', return_id=return_id),
        )
    return redirect(url_for('purchases.return_detail',return_id=return_id))


@purchases_bp.get('/api/purchase_sources')
def api_purchase_sources():
    """Saved inbound orders for the 退拿货 source picker (A: linkage only)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, order_no, customer_name, business_date
                 FROM purchase_orders
                WHERE deleted_at IS NULL AND status='saved'
                ORDER BY business_date DESC, id DESC LIMIT 200"""
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@purchases_bp.get('/api/purchase_source/<int:purchase_id>')
def api_purchase_source(purchase_id: int):
    """Items of one saved 拿货单 so the 退拿货 page can prefill rows."""
    with get_db() as conn:
        order = conn.execute(
            "SELECT id, order_no, business_date FROM purchase_orders WHERE id=? AND deleted_at IS NULL AND status='saved'",
            (purchase_id,),
        ).fetchone()
        if order is None:
            return jsonify({'error': '原拿货单不存在或未正式保存'}), 404
        items = conn.execute(
            """SELECT product_id, product_name, COALESCE(spec,'') AS spec, unit,
                      quantity_3dp, unit_cost_cents
                 FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id""",
            (purchase_id,),
        ).fetchall()
    return jsonify({
        'order': dict(order),
        'items': [
            {
                'product_id': row['product_id'],
                'product_name': row['product_name'],
                'spec': row['spec'],
                'unit': row['unit'],
                'quantity': f"{row['quantity_3dp'] / 1000:g}",
                'unit_price': f"{row['unit_cost_cents'] / 100:.2f}",
            }
            for row in items
        ],
    })


@purchases_bp.get('/api/objects')
def api_objects():
    q = (request.args.get('q') or '').strip()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, phone FROM customers WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name LIMIT 20",
            (f'%{q}%',),
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@purchases_bp.get('/api/next_order_no')
def api_next_order_no():
    """开单页只读单号预览：拿货/退拿货共用 NH 日流水，只预览不占用。"""
    raw = (request.args.get('date') or '').strip()
    business_date = raw if _valid_iso_date(raw) else date.today().isoformat()
    with get_db() as conn:
        order_no = peek_nh_order_no(conn, business_date)
    return jsonify({'business_date': business_date, 'order_no': order_no})


@purchases_bp.get('/api/today_history')
def api_today_history():
    today = date.today().isoformat()
    with get_db() as conn:
        orders = _purchase_today_history(conn, today)
    return jsonify({'date': today, 'orders': orders})
