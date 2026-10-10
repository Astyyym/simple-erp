from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify, send_file
from pathlib import Path
from decimal import Decimal, InvalidOperation
import io
from datetime import date
from uuid import uuid4

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
from erp.services.master_data_import import (
    PRODUCT_HEADERS,
    build_product_preview,
    product_totals,
    ImportPreview,
)
from erp.utils.importing import ImportFileError, ImportResult, excel_template, normalized_name, price_to_cents, read_upload_detailed
from erp.utils.money import yuan_to_cents, cents_to_yuan, micro_to_yuan
from erp.utils.backup import list_backups

# Deferred Pillow seams: only the product-image route needs PIL, so it is imported
# on first use to keep app boot cheap. Kept as module-level names for overrides.
Image = None
UnidentifiedImageError = None


def _pil():
    """Resolve Pillow lazily; raises the original ImportError if Pillow is absent."""
    global Image, UnidentifiedImageError
    if Image is None:
        from PIL import Image as _Image
        Image = _Image
    if UnidentifiedImageError is None:
        from PIL import UnidentifiedImageError as _Err
        UnidentifiedImageError = _Err
    return Image, UnidentifiedImageError

products_bp = Blueprint("products", __name__, url_prefix="/products")


def _backup_hint() -> dict:
    """A-6/A-7：迁移前提醒与「本库尚无任何备份」告警。"""
    return {"has_backup": bool(list_backups())}


@products_bp.get("/import/template")
def download_import_template():
    return excel_template(list(PRODUCT_HEADERS[2]), "商品导入模板.xlsx")


@products_bp.get("/export.xlsx")
def export_products_excel():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        products, _suggestions, _truncated, _todo = _product_list_context(conn, q)
    return workbook_download(
        product_export_headers(),
        product_export_rows(products),
        sheet_title="商品导出",
        filename=export_filename("商品导出"),
    )


def _product_list_context(conn, q: str = "", quality: str = ""):
    # G-0c：商品「待补全」只看型号为空（未启用库存不进待补全，那是该路径的正常状态）。
    todo_sql = " AND TRIM(COALESCE(p.spec, ''))=''" if quality == "todo" else ""
    if q:
        products = conn.execute(
            """
            SELECT p.*, s.enabled AS inventory_enabled, s.quantity_3dp, s.cost_total_micro, s.avg_cost_micro
            FROM products AS p
            LEFT JOIN product_inventory_state AS s ON s.product_id=p.id
            WHERE p.deleted_at IS NULL AND (p.name LIKE ? OR p.spec LIKE ? OR p.pinyin_initials LIKE ?)
            """
            + todo_sql
            + """
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
            """
            + todo_sql
            + """
            ORDER BY p.name COLLATE NOCASE ASC, p.id ASC LIMIT 200
            """
        ).fetchall()
        # E4：到达上限即提示「已截断，可用搜索缩小范围」。
        truncated = len(products) >= 200
    suggestions = conn.execute(
        "SELECT DISTINCT name FROM products WHERE deleted_at IS NULL AND name LIKE ? ORDER BY usage_count DESC, name LIMIT 20",
        (f"%{q}%" if q else "%",),
    ).fetchall()
    todo_count = conn.execute(
        "SELECT COUNT(*) AS c FROM products WHERE deleted_at IS NULL AND TRIM(COALESCE(spec, ''))=''"
    ).fetchone()["c"]
    projected_products = []
    for row in products:
        product = dict(row)
        product.update(project_inventory_status(
            enabled=product["inventory_enabled"],
            quantity_3dp=product["quantity_3dp"],
            safety_stock_3dp=product["safety_stock_3dp"],
        ))
        projected_products.append(product)
    return projected_products, suggestions, truncated, todo_count


@products_bp.post("/import")
def import_products():
    """B-4：第一段——只解析出预览，不落库。"""
    try:
        headers, rows = read_upload_detailed(request.files.get("file"), PRODUCT_HEADERS)
    except (ImportFileError, AttributeError) as exc:
        message = str(exc) if str(exc) else "请选择要导入的文件"
        with get_db() as conn:
            products, suggestions, truncated, todo_count = _product_list_context(conn)
        return render_template(
            "products/list.html",
            products=products,
            suggestions=suggestions,
            truncated=truncated,
            todo_count=todo_count,
            quality="",
            cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
            q="",
            import_error=message,
            **_backup_hint(),
        ), 400

    with get_db() as conn:
        existing = {
            (row["name"].strip(), (row["spec"] or "").strip())
            for row in conn.execute("SELECT name, spec FROM products WHERE deleted_at IS NULL")
        }
        soft_deleted = {
            (row["name"].strip(), (row["spec"] or "").strip())
            for row in conn.execute("SELECT name, spec FROM products WHERE deleted_at IS NOT NULL")
        }
    same_name = request.form.get("same_name", "skip")
    if same_name not in ("skip", "update"):
        same_name = "skip"
    preview = build_product_preview(headers, rows, existing, soft_deleted, same_name=same_name)
    preview.batch_id = uuid4().hex
    return render_template(
        "import_preview.html",
        kind="products",
        title="商品导入预览",
        action_url=url_for("products.import_products_confirm"),
        back_url=url_for("products.list_products"),
        preview=preview,
        totals=product_totals(preview),
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        **_backup_hint(),
    )


@products_bp.post("/import/confirm")
def import_products_confirm():
    """B-4/B-6：第二段——服务端二次校验后落库；带期初的行独立事务初始化。"""
    raw = request.form.get("preview_json", "")
    same_name = request.form.get("same_name", "skip")
    try:
        preview = ImportPreview.from_json(raw)
    except Exception:  # noqa: BLE001
        return error_response("导入预览已失效，请重新上传文件。", title="导入未执行",
                              back_url=url_for("products.list_products"))
    if preview.kind != "products":
        return error_response("导入预览类型不匹配，请重新上传。", title="导入未执行",
                              back_url=url_for("products.list_products"))

    result = ImportResult()
    init_errors: list[str] = []
    with get_db() as conn:
        existing = {
            (row["name"].strip(), (row["spec"] or "").strip())
            for row in conn.execute("SELECT name, spec FROM products WHERE deleted_at IS NULL")
        }
        soft_deleted = {
            (row["name"].strip(), (row["spec"] or "").strip())
            for row in conn.execute("SELECT name, spec FROM products WHERE deleted_at IS NOT NULL")
        }
        # 记录需要做期初初始化的商品（B-6：独立事务，事务外调用）。
        pending_inits: list[tuple[int, dict]] = []
        for row in preview.rows:
            if row.action == "error":
                result.add_error(f"第{row.line_number}行：{row.reason}")
                continue
            if row.action == "skip":
                result.skipped += 1
                continue
            name = normalized_name(row.payload.get("name"))
            spec = normalized_name(row.payload.get("spec"))
            identity = (name, spec)
            if identity in soft_deleted:
                result.add_error(f"第{row.line_number}行：该名称+型号已被回收站记录占用")
                continue
            if row.action == "add":
                if identity in existing:
                    result.skipped += 1
                    continue
                cur = conn.execute(
                    "INSERT INTO products(name, spec, brand, unit, default_price_cents, pinyin_initials, origin) VALUES (?, ?, ?, ?, ?, ?, 'import')",
                    (name, spec, row.payload.get("brand") or None, row.payload.get("unit", "个"), int(row.payload.get("default_price_cents", 0)),
                     build_pinyin_initials(name)),
                )
                product_id = int(cur.lastrowid)
                existing.add(identity)
                result.added += 1
            elif row.action == "update":
                if identity not in existing:
                    result.add_error(f"第{row.line_number}行：同名同型号商品已不存在，无法更新")
                    continue
                conn.execute(
                    "UPDATE products SET brand=?, unit=?, default_price_cents=?, pinyin_initials=?, updated_at=CURRENT_TIMESTAMP WHERE TRIM(name)=? AND TRIM(COALESCE(spec,''))=? AND deleted_at IS NULL",
                    (row.payload.get("brand") or None, row.payload.get("unit", "个"), int(row.payload.get("default_price_cents", 0)),
                     build_pinyin_initials(name), name, spec),
                )
                found = conn.execute(
                    "SELECT id FROM products WHERE TRIM(name)=? AND TRIM(COALESCE(spec,''))=? AND deleted_at IS NULL",
                    (name, spec),
                ).fetchone()
                product_id = int(found["id"])
                result.added += 1
            else:
                continue
            qty = str(row.payload.get("quantity") or "").strip()
            cost_cents = row.payload.get("cost_cents")
            if qty and cost_cents is not None:
                pending_inits.append((product_id, {"quantity": qty, "cost_cents": int(cost_cents), "line": row.line_number}))

    # B-6：期初初始化各自独立事务；用「批次号+行号」作幂等键，重复确认不重复初始化。
    for product_id, info in pending_inits:
        request_key = f"import-{preview.batch_id}-{info['line']}"
        try:
            initialize_product(
                product_id,
                info["quantity"],
                str(Decimal(int(info["cost_cents"])) / Decimal(100)),
                date.today().isoformat(),
                "批量导入期初",
                request_key,
                confirm_zero=False,
            )
        except ValueError as exc:
            init_errors.append(f"第{info['line']}行：商品已建档，但期初初始化失败（{exc}）")

    with get_db() as conn:
        products, suggestions, truncated, todo_count = _product_list_context(conn)
    return render_template(
        "products/list.html",
        products=products,
        suggestions=suggestions,
        truncated=truncated,
        todo_count=todo_count,
        quality="",
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        q="",
        import_result=result,
        import_init_errors=init_errors,
        **_backup_hint(),
    )


@products_bp.get("/")
def list_products():
    q = request.args.get("q", "").strip()
    quality = request.args.get("quality", "").strip()
    with get_db() as conn:
        rows, suggestions, truncated, todo_count = _product_list_context(conn, q, quality)
    return render_template(
        "products/list.html",
        products=rows,
        suggestions=suggestions,
        truncated=truncated,
        todo_count=todo_count,
        quality=quality,
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        q=q,
        **_backup_hint(),
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
        create_product(request.form["name"], request.form.get("spec", ""), request.form["unit"], yuan_to_cents(request.form["default_price"]), (request.form.get("pinyin_initials", "") or build_pinyin_initials(request.form["name"])), safety_stock=request.form.get("safety_stock", "0"), brand=request.form.get("brand", ""))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and str(exc) else "默认单价不是有效数字，请填写例如 12.00 的金额"
        with get_db() as conn:
            products, suggestions, truncated, todo_count = _product_list_context(conn)
        return render_template("products/list.html", products=products, suggestions=suggestions, truncated=truncated, todo_count=todo_count, quality="", cents_to_yuan=cents_to_yuan, micro_to_yuan=micro_to_yuan, q="", import_error=message, create_open=True, **_backup_hint()), 400
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
        image_cls, unidentified_error = _pil()
        try:
            with image_cls.open(io.BytesIO(content)) as source:
                if source.format not in {"JPEG", "PNG"}:
                    raise ValueError("商品图片仅支持 JPEG 或 PNG")
                if source.width * source.height > 20_000_000:
                    raise ValueError("商品图片像素总数不能超过2000万")
                source.load()
                converted = source.convert("RGB")
        except (unidentified_error, OSError) as exc:
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
        update_product_record(product_id, request.form["name"], request.form.get("spec", ""), request.form["unit"], default_price_cents, safety_stock=request.form.get("safety_stock", "0"), brand=request.form.get("brand", ""))
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
            cost_source=request.form.get("cost_source", "known"),
            total_cost=request.form.get("total_cost", ""),
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
