from flask import Blueprint, render_template, request, redirect, url_for, Response
from erp.db import get_db
from erp.services.accounting import create_customer
from erp.utils.money import yuan_to_cents, cents_to_yuan

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")

@customers_bp.get("/")
def list_customers():
    with get_db() as conn:
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE ASC, id ASC LIMIT 200").fetchall()
    return render_template("customers/list.html", customers=customers, cents_to_yuan=cents_to_yuan)

@customers_bp.post("/create")
def create_customer_view():
    create_customer(request.form["name"], request.form.get("phone", ""), request.form.get("address", ""), yuan_to_cents(request.form.get("opening_balance", "0")))
    return redirect(url_for("customers.list_customers"))

@customers_bp.get("/<int:customer_id>/edit")
def edit_customer(customer_id: int):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    return render_template("customers/edit.html", customer=customer, cents_to_yuan=cents_to_yuan)

@customers_bp.post("/<int:customer_id>/edit")
def update_customer(customer_id: int):
    with get_db() as conn:
        conn.execute("UPDATE customers SET name=?, phone=?, address=?, opening_balance_cents=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (request.form["name"], request.form.get("phone", ""), request.form.get("address", ""), yuan_to_cents(request.form.get("opening_balance", "0")), customer_id))
    return redirect(url_for("customers.list_customers"))

@customers_bp.post("/bulk_delete")
def bulk_delete_customers():
    ids = [int(x) for x in request.form.getlist("ids")]
    with get_db() as conn:
        conn.executemany("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP, delete_reason='批量删除' WHERE id=?", [(i,) for i in ids])
    return redirect(url_for("customers.list_customers"))


@customers_bp.post("/<int:customer_id>/delete")
def delete_customer(customer_id: int):
    with get_db() as conn:
        conn.execute("UPDATE customers SET deleted_at=CURRENT_TIMESTAMP, delete_reason='用户删除' WHERE id=?", (customer_id,))
    if request.headers.get("X-Requested-With") or "fetch" in request.headers.get("Sec-Fetch-Mode", ""):
        return Response(status=204)
    return redirect(url_for("customers.list_customers"))
