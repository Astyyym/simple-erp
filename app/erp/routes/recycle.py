from flask import Blueprint, render_template, redirect, url_for, request
from erp.db import get_db
from erp.utils.audit import log_action
from erp.utils.money import cents_to_yuan

recycle_bp = Blueprint("recycle", __name__, url_prefix="/recycle")

@recycle_bp.get("/")
def recycle_bin():
    with get_db() as conn:
        orders = conn.execute("SELECT o.*, c.name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.deleted_at IS NOT NULL ORDER BY o.deleted_at DESC").fetchall()
        products = conn.execute("SELECT * FROM products WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
    return render_template("recycle/index.html", orders=orders, products=products, customers=customers, cents_to_yuan=cents_to_yuan)

@recycle_bp.post("/<kind>/<int:item_id>/restore")
def restore(kind: str, item_id: int):
    table = {"order": "orders", "product": "products", "customer": "customers"}[kind]
    with get_db() as conn:
        conn.execute(f"UPDATE {table} SET deleted_at=NULL, delete_reason='' WHERE id=?", (item_id,))
    log_action("restore_from_recycle", kind, item_id, "从回收站恢复")
    return redirect(url_for("recycle.recycle_bin"))

def _purge_one(conn, kind: str, item_id: int) -> None:
    if kind == "order":
        conn.execute("DELETE FROM order_items WHERE order_id=?", (item_id,))
        conn.execute("DELETE FROM orders WHERE id=?", (item_id,))
    elif kind == "product":
        # 历史订单明细保留了商品名/单位/价格快照，永久删除商品字典前先解除外键引用，避免 FK 报错。
        conn.execute("UPDATE order_items SET product_id=NULL WHERE product_id=?", (item_id,))
        conn.execute("DELETE FROM customer_prices WHERE product_id=?", (item_id,))
        conn.execute("DELETE FROM products WHERE id=?", (item_id,))
    elif kind == "customer":
        # 有历史单据/收款/调整的客户不能物理删除，否则会破坏账务追溯。
        conn.execute("DELETE FROM customers WHERE id=? AND id NOT IN (SELECT customer_id FROM orders) AND id NOT IN (SELECT customer_id FROM payments) AND id NOT IN (SELECT customer_id FROM adjustments)", (item_id,))

@recycle_bp.post("/<kind>/<int:item_id>/purge")
def purge(kind: str, item_id: int):
    with get_db() as conn:
        _purge_one(conn, kind, item_id)
    log_action("purge_recycle", kind, item_id, "从回收站确认删除")
    return redirect(url_for("recycle.recycle_bin"))

@recycle_bp.post("/bulk/<kind>/restore")
def bulk_restore(kind: str):
    table = {"order": "orders", "product": "products", "customer": "customers"}[kind]
    ids = [int(x) for x in request.form.getlist("ids")]
    with get_db() as conn:
        conn.executemany(f"UPDATE {table} SET deleted_at=NULL, delete_reason='' WHERE id=?", [(i,) for i in ids])
    log_action("bulk_restore_recycle", kind, "batch", f"批量恢复 {len(ids)} 条")
    return redirect(url_for("recycle.recycle_bin"))

@recycle_bp.post("/bulk/<kind>/purge")
def bulk_purge(kind: str):
    ids = [int(x) for x in request.form.getlist("ids")]
    with get_db() as conn:
        for item_id in ids:
            _purge_one(conn, kind, item_id)
    log_action("bulk_purge_recycle", kind, "batch", f"批量确认删除 {len(ids)} 条")
    return redirect(url_for("recycle.recycle_bin"))
