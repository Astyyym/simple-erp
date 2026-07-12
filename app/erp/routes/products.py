from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify
from erp.db import get_db
from erp.services.accounting import create_product
from erp.utils.importing import ImportFileError, ImportResult, excel_template, normalized_name, price_to_cents, read_upload
from erp.utils.money import yuan_to_cents, cents_to_yuan

products_bp = Blueprint("products", __name__, url_prefix="/products")

@products_bp.get("/import/template")
def download_import_template():
    return excel_template(["商品名称", "价格"], "商品导入模板.xlsx")


def _product_list_context(conn, q: str = ""):
    if q:
        products = conn.execute(
            "SELECT * FROM products WHERE deleted_at IS NULL AND (name LIKE ? OR spec LIKE ? OR pinyin_initials LIKE ?) ORDER BY usage_count DESC, id DESC",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        products = conn.execute(
            "SELECT * FROM products WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE ASC, id ASC LIMIT 200"
        ).fetchall()
    suggestions = conn.execute(
        "SELECT DISTINCT name FROM products WHERE deleted_at IS NULL AND name LIKE ? ORDER BY usage_count DESC, name LIMIT 20",
        (f"%{q}%" if q else "%",),
    ).fetchall()
    return products, suggestions


@products_bp.post("/import")
def import_products():
    try:
        rows = read_upload(request.files.get("file"), ["商品名称", "价格"])
    except (ImportFileError, AttributeError) as exc:
        message = str(exc) if str(exc) else "请选择要导入的文件"
        with get_db() as conn:
            products, suggestions = _product_list_context(conn)
        return render_template(
            "products/list.html",
            products=products,
            suggestions=suggestions,
            cents_to_yuan=cents_to_yuan,
            q="",
            import_error=message,
        ), 400

    result = ImportResult()
    with get_db() as conn:
        existing_names = {row["name"] for row in conn.execute("SELECT name FROM products").fetchall()}
        seen_names: set[str] = set()
        for line_number, row in rows:
            name = normalized_name(row[0] if row else None)
            if not name:
                result.add_error(f"第{line_number}行：商品名称不能为空")
                continue
            if name in existing_names or name in seen_names:
                result.skipped += 1
                continue
            try:
                price_cents = price_to_cents(row[1] if len(row) > 1 else None)
            except ValueError as exc:
                result.add_error(f"第{line_number}行：{exc}")
                continue
            conn.execute(
                "INSERT INTO products(name, spec, unit, default_price_cents) VALUES (?, '', '个', ?)",
                (name, price_cents),
            )
            seen_names.add(name)
            existing_names.add(name)
            result.added += 1
        products, suggestions = _product_list_context(conn)
    return render_template(
        "products/list.html",
        products=products,
        suggestions=suggestions,
        cents_to_yuan=cents_to_yuan,
        q="",
        import_result=result,
    )


@products_bp.get("/")
def list_products():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        rows, suggestions = _product_list_context(conn, q)
    return render_template(
        "products/list.html",
        products=rows,
        suggestions=suggestions,
        cents_to_yuan=cents_to_yuan,
        q=q,
    )

@products_bp.get("/api/suggestions")
def product_suggestions():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT name FROM products WHERE deleted_at IS NULL AND name LIKE ? ORDER BY usage_count DESC, name LIMIT 20",
            (f"%{q}%",),
        ).fetchall()
    return jsonify([row["name"] for row in rows])


@products_bp.post("/create")
def create_product_view():
    create_product(request.form["name"], request.form.get("spec", ""), request.form["unit"], yuan_to_cents(request.form["default_price"]), request.form.get("pinyin_initials", ""))
    return redirect(url_for("products.list_products"))

@products_bp.get("/<int:product_id>/edit")
def edit_product(product_id: int):
    with get_db() as conn:
        product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    return render_template("products/edit.html", product=product, cents_to_yuan=cents_to_yuan)

@products_bp.post("/<int:product_id>/edit")
def update_product(product_id: int):
    with get_db() as conn:
        conn.execute("UPDATE products SET name=?, unit=?, default_price_cents=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (request.form["name"], request.form["unit"], yuan_to_cents(request.form["default_price"]), product_id))
    return redirect(url_for("products.list_products"))

@products_bp.post("/bulk_delete")
def bulk_delete_products():
    ids = [int(x) for x in request.form.getlist("ids")]
    with get_db() as conn:
        conn.executemany("UPDATE products SET deleted_at=CURRENT_TIMESTAMP, delete_reason='批量删除' WHERE id=?", [(i,) for i in ids])
    return redirect(url_for("products.list_products"))


@products_bp.post("/<int:product_id>/delete")
def delete_product(product_id: int):
    with get_db() as conn:
        conn.execute("UPDATE products SET deleted_at=CURRENT_TIMESTAMP, delete_reason='用户删除' WHERE id=?", (product_id,))
    if request.headers.get("X-Requested-With") or "fetch" in request.headers.get("Sec-Fetch-Mode", ""):
        return Response(status=204)
    return redirect(url_for("products.list_products"))
