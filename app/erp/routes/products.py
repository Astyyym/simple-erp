from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify, send_file
from pathlib import Path
from decimal import InvalidOperation
import io
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from erp.db import get_db
from erp.services.accounting import create_product, update_product_record
from erp.services.inventory import initialize_product, revise_initialization
from erp.services.inventory_status import project_inventory_status
from erp.utils.errors import error_response, not_found
from erp.utils.pinyin import pinyin_initials as build_pinyin_initials
from erp.utils.exporting import (
    export_filename,
    product_export_headers,
    product_export_rows,
    workbook_download,
)
from erp.config import data_root
from erp.utils.importing import ImportFileError, ImportResult, excel_template, normalized_name, price_to_cents, read_upload
from erp.utils.money import yuan_to_cents, cents_to_yuan, micro_to_yuan

products_bp = Blueprint("products", __name__, url_prefix="/products")

@products_bp.get("/import/template")
def download_import_template():
    return excel_template(["商品名称", "型号", "价格"], "商品导入模板.xlsx")


@products_bp.get("/export.xlsx")
def export_products_excel():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        products, _suggestions, _truncated = _product_list_context(conn, q)
    return workbook_download(
        product_export_headers(),
        product_export_rows(products),
        sheet_title="商品导出",
        filename=export_filename("商品导出"),
    )


def _product_list_context(conn, q: str = ""):
    if q:
        products = conn.execute(
            """
            SELECT p.*, s.enabled AS inventory_enabled, s.quantity_3dp, s.cost_total_micro, s.avg_cost_micro
            FROM products AS p
            LEFT JOIN product_inventory_state AS s ON s.product_id=p.id
            WHERE p.deleted_at IS NULL AND (p.name LIKE ? OR p.spec LIKE ? OR p.pinyin_initials LIKE ?)
            ORDER BY p.usage_count DESC, p.id DESC
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
        truncated = False
    else:
        products = conn.execute(
            """
            SELECT p.*, s.enabled AS inventory_enabled, s.quantity_3dp, s.cost_total_micro, s.avg_cost_micro
            FROM products AS p
            LEFT JOIN product_inventory_state AS s ON s.product_id=p.id
            WHERE p.deleted_at IS NULL
            ORDER BY p.name COLLATE NOCASE ASC, p.id ASC LIMIT 200
            """
        ).fetchall()
        # E4：到达上限即提示「已截断，可用搜索缩小范围」。
        truncated = len(products) >= 200
    suggestions = conn.execute(
        "SELECT DISTINCT name FROM products WHERE deleted_at IS NULL AND name LIKE ? ORDER BY usage_count DESC, name LIMIT 20",
        (f"%{q}%" if q else "%",),
    ).fetchall()
    projected_products = []
    for row in products:
        product = dict(row)
        product.update(project_inventory_status(
            enabled=product["inventory_enabled"],
            quantity_3dp=product["quantity_3dp"],
            safety_stock_3dp=product["safety_stock_3dp"],
        ))
        projected_products.append(product)
    return projected_products, suggestions, truncated


@products_bp.post("/import")
def import_products():
    try:
        rows = read_upload(request.files.get("file"), (["商品名称", "型号", "价格"], ["商品名称", "价格"]))
    except (ImportFileError, AttributeError) as exc:
        message = str(exc) if str(exc) else "请选择要导入的文件"
        with get_db() as conn:
            products, suggestions, truncated = _product_list_context(conn)
        return render_template(
            "products/list.html",
            products=products,
            suggestions=suggestions,
            truncated=truncated,
            cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
            q="",
            import_error=message,
        ), 400

    result = ImportResult()
    with get_db() as conn:
        existing_identities = {
            (row["name"].strip(), (row["spec"] or "").strip())
            for row in conn.execute("SELECT name, spec FROM products").fetchall()
        }
        seen_identities: set[tuple[str, str]] = set()
        for line_number, row in rows:
            name = normalized_name(row[0] if row else None)
            if not name:
                result.add_error(f"第{line_number}行：商品名称不能为空")
                continue
            spec = normalized_name(row[1] if len(row) > 2 else "")
            price_value = row[2] if len(row) > 2 else (row[1] if len(row) > 1 else None)
            identity = (name, spec)
            if identity in existing_identities or identity in seen_identities:
                result.skipped += 1
                continue
            try:
                price_cents = price_to_cents(price_value)
            except ValueError as exc:
                result.add_error(f"第{line_number}行：{exc}")
                continue
            conn.execute(
                "INSERT INTO products(name, spec, unit, default_price_cents, pinyin_initials) VALUES (?, ?, '个', ?, ?)",
                (name, spec, price_cents, build_pinyin_initials(name)),
            )
            seen_identities.add(identity)
            existing_identities.add(identity)
            result.added += 1
        products, suggestions, truncated = _product_list_context(conn)
    return render_template(
        "products/list.html",
        products=products,
        suggestions=suggestions,
        truncated=truncated,
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        q="",
        import_result=result,
    )


@products_bp.get("/")
def list_products():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        rows, suggestions, truncated = _product_list_context(conn, q)
    return render_template(
        "products/list.html",
        products=rows,
        suggestions=suggestions,
        truncated=truncated,
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
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
    try:
        create_product(request.form["name"], request.form.get("spec", ""), request.form["unit"], yuan_to_cents(request.form["default_price"]), (request.form.get("pinyin_initials", "") or build_pinyin_initials(request.form["name"])), safety_stock=request.form.get("safety_stock", "0"))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and str(exc) else "默认单价不是有效数字，请填写例如 12.00 的金额"
        with get_db() as conn:
            products, suggestions, truncated = _product_list_context(conn)
        return render_template("products/list.html", products=products, suggestions=suggestions, truncated=truncated, cents_to_yuan=cents_to_yuan, micro_to_yuan=micro_to_yuan, q="", import_error=message), 400
    return redirect(url_for("products.list_products"))

@products_bp.get("/<int:product_id>/edit")
def edit_product(product_id: int):
    return _render_product_edit(product_id)


def _render_product_edit(product_id: int, *, error: str = "", inventory_message: str = "", status_code: int = 200):
    with get_db() as conn:
        product = conn.execute("SELECT * FROM products WHERE id=? AND deleted_at IS NULL", (product_id,)).fetchone()
        inventory_state = conn.execute("SELECT * FROM product_inventory_state WHERE product_id=?", (product_id,)).fetchone()
        initialization = conn.execute("SELECT * FROM inventory_initializations WHERE product_id=?", (product_id,)).fetchone()
    if product is None:
        return not_found("商品不存在，可能已被删除或从未存在。", back_url=url_for("products.list_products"))
    inventory_status = project_inventory_status(
        enabled=inventory_state["enabled"] if inventory_state else False,
        quantity_3dp=inventory_state["quantity_3dp"] if inventory_state else None,
        safety_stock_3dp=product["safety_stock_3dp"] if product else 0,
    )
    return render_template(
        "products/edit.html",
        product=product,
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        inventory_state=inventory_state,
        inventory_status=inventory_status,
        initialization=initialization,
        error=error,
        inventory_message=inventory_message,
    ), status_code


def _product_image_dir() -> Path:
    path = data_root() / "product_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


@products_bp.post("/<int:product_id>/image")
def upload_product_image(product_id: int):
    upload = request.files.get("image")
    try:
        with get_db() as conn:
            product = conn.execute("SELECT image_path FROM products WHERE id=? AND deleted_at IS NULL", (product_id,)).fetchone()
        if product is None:
            raise ValueError("商品不存在")
        if upload is None or not upload.filename:
            raise ValueError("商品图片不能为空")
        content = upload.stream.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise ValueError("商品图片不能超过5MB")
        try:
            with Image.open(io.BytesIO(content)) as source:
                if source.format not in {"JPEG", "PNG"}:
                    raise ValueError("商品图片仅支持 JPEG 或 PNG")
                if source.width * source.height > 20_000_000:
                    raise ValueError("商品图片像素总数不能超过2000万")
                source.load()
                converted = source.convert("RGB")
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("商品图片无法解析") from exc
        filename = f"{uuid4().hex}.jpg"
        target = _product_image_dir() / filename
        converted.save(target, format="JPEG", quality=90, optimize=True)
        try:
            with get_db() as conn:
                old_path = (product["image_path"] or "").strip()
                conn.execute("UPDATE products SET image_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (filename, product_id))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if old_path and old_path != filename:
            old_file = _product_image_dir() / Path(old_path).name
            if old_file.is_file():
                old_file.unlink()
    except ValueError as exc:
        return _render_product_edit(product_id, error=str(exc), status_code=400)
    return _render_product_edit(product_id, inventory_message="商品图片已更新")


@products_bp.get("/<int:product_id>/image")
def product_image(product_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT image_path FROM products WHERE id=? AND deleted_at IS NULL", (product_id,)).fetchone()
    if row is None or not row["image_path"]:
        return Response(status=404)
    filename = Path(row["image_path"]).name
    path = _product_image_dir() / filename
    if not path.is_file():
        return Response(status=404)
    return send_file(path, mimetype="image/jpeg", max_age=0)

@products_bp.post("/<int:product_id>/edit")
def update_product(product_id: int):
    try:
        default_price_cents = yuan_to_cents(request.form["default_price"])
        update_product_record(product_id, request.form["name"], request.form.get("spec", ""), request.form["unit"], default_price_cents, safety_stock=request.form.get("safety_stock", "0"))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and str(exc) else "默认单价不是有效数字，请填写例如 12.00 的金额"
        return _render_product_edit(product_id, error=message, status_code=400)
    return redirect(url_for("products.list_products"))


@products_bp.post("/<int:product_id>/inventory/initialize")
def initialize_product_inventory(product_id: int):
    try:
        initialize_product(
            product_id,
            request.form.get("quantity", ""),
            request.form.get("unit_cost", ""),
            request.form.get("business_date", ""),
            request.form.get("source", "系统上线期初"),
            request.form.get("request_key", "") or f"web-init-{product_id}-{request.form.get('business_date', '')}-{request.form.get('quantity', '')}",
            confirm_zero=request.form.get("confirm_zero") == "1",
        )
    except ValueError as exc:
        return _render_product_edit(product_id, error=str(exc), status_code=400)
    return _render_product_edit(product_id, inventory_message="期初库存已启用")


@products_bp.post("/<int:product_id>/inventory/revise")
def revise_product_inventory(product_id: int):
    try:
        revise_initialization(
            product_id,
            request.form.get("quantity", ""),
            request.form.get("unit_cost", ""),
            request.form.get("reason", ""),
            request.form.get("expected_version", type=int),
        )
    except (TypeError, ValueError) as exc:
        return _render_product_edit(product_id, error=str(exc), status_code=400)
    return _render_product_edit(product_id, inventory_message="期初库存已修订")

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
