from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify, send_file
from pathlib import Path
from decimal import Decimal, InvalidOperation
import io
from datetime import date
from uuid import uuid4

from erp.db import get_db
from erp.services.accounting import create_product, update_product_record
from erp.utils.errors import error_response, not_found
from erp.utils.pinyin import name_match_clause, name_match_params, pinyin_fields as build_pinyin_fields
from erp.utils.exporting import (
    export_filename,
    product_export_headers,
    product_export_rows,
    workbook_download,
)
from erp.config import data_root
from erp.services.master_data_import import (
    PRODUCT_HEADERS,
    PRODUCT_HEADERS_TEMPLATE,
    PRODUCT_TEMPLATE_NOTES,
    build_product_preview,
    ImportPreview,
)
from erp.services.master_data_quality import COMPLETION_ORIGINS_SQL
from erp.utils.importing import ImportFileError, ImportResult, excel_template, normalized_name, read_upload_detailed
from erp.utils.money import cents_to_yuan, micro_to_yuan
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
    return excel_template(list(PRODUCT_HEADERS_TEMPLATE), "商品导入模板.xlsx", PRODUCT_TEMPLATE_NOTES)


@products_bp.get("/export.xlsx")
def export_products_excel():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        products, _suggestions, _truncated, _todo_count = _product_list_context(conn, q)
    return workbook_download(
        product_export_headers(),
        product_export_rows(products),
        sheet_title="商品导出",
        filename=export_filename("商品导出"),
    )


def _product_list_context(conn, q: str = "", quality: str = "", sort: str = ""):
    # v14：商品身份只有名称，「待补全（型号为空）」筛选已随 spec 列一起删除。
    # 2026-10-14 第一轮：用户要求商品页恢复「全部 / 待补全」+ 新增「按拼音排序」。
    # 2026-10-14 第二轮：口径扩到「开单攒出 + 批量导入」。
    # 2026-10-15 第三轮：默认价下线 → 「待补全」只剩「缺品牌」。判定必须与
    # services/master_data_quality 同源，别在这里另写一套条件。
    todo_sql = (
        f" AND origin IN {COMPLETION_ORIGINS_SQL}"
        " AND TRIM(COALESCE(brand, ''))=''"
        if quality == "todo"
        else ""
    )
    # 按拼音排序：pinyin_full 是「无分隔符小写全拼」，空值排最后再回落名称。
    # 默认仍按名称（NOCASE）——中文是按码位排，不是拼音，这正是要加这个开关的原因。
    pinyin_order = (
        "ORDER BY (CASE WHEN COALESCE(p.pinyin_full, '') = '' THEN 1 ELSE 0 END), "
        "p.pinyin_full COLLATE NOCASE ASC, p.name COLLATE NOCASE ASC, p.id ASC"
    )
    if q:
        products = conn.execute(
            f"""
            SELECT p.*
            FROM products AS p
            WHERE p.deleted_at IS NULL AND {name_match_clause('p.')}{todo_sql}
            {pinyin_order if sort == 'pinyin' else 'ORDER BY p.usage_count DESC, p.id DESC'}
            """,
            (*name_match_params(q),),
        ).fetchall()
        truncated = False
    else:
        products = conn.execute(
            f"""
            SELECT p.*
            FROM products AS p
            WHERE p.deleted_at IS NULL{todo_sql}
            {pinyin_order if sort == 'pinyin' else 'ORDER BY p.name COLLATE NOCASE ASC, p.id ASC'} LIMIT 200
            """
        ).fetchall()
        # E4：到达上限即提示「已截断，可用搜索缩小范围」。
        truncated = len(products) >= 200
    suggestions = conn.execute(
        f"SELECT DISTINCT name FROM products WHERE deleted_at IS NULL AND {name_match_clause()} ORDER BY usage_count DESC, name LIMIT 20",
        (*name_match_params(q),),
    ).fetchall()
    todo_count = conn.execute(
        "SELECT COUNT(*) AS c FROM products WHERE deleted_at IS NULL"
        f" AND origin IN {COMPLETION_ORIGINS_SQL}"
        " AND TRIM(COALESCE(brand, ''))=''"
    ).fetchone()["c"]
    projected_products = [dict(row) for row in products]
    # v15（决策 ②/③/⑦）：库存状态/成本/安全库存字段全部下线，列表不再有投影步骤。
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
            cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
            q="",
            quality="",
            sort="",
            todo_count=todo_count,
            import_error=message,
            **_backup_hint(),
        ), 400

    with get_db() as conn:
        existing = {
            row["name"].strip()
            for row in conn.execute("SELECT name FROM products WHERE deleted_at IS NULL")
        }
        soft_deleted = {
            row["name"].strip()
            for row in conn.execute("SELECT name FROM products WHERE deleted_at IS NOT NULL")
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
    with get_db() as conn:
        existing = {
            row["name"].strip()
            for row in conn.execute("SELECT name FROM products WHERE deleted_at IS NULL")
        }
        soft_deleted = {
            row["name"].strip()
            for row in conn.execute("SELECT name FROM products WHERE deleted_at IS NOT NULL")
        }
        # v15（决策 ⑥）：导入不再有「期初数量/期初成本」列，因此没有待初始化项。
        for row in preview.rows:
            if row.action == "error":
                result.add_error(f"第{row.line_number}行：{row.reason}")
                continue
            if row.action == "skip":
                result.skipped += 1
                continue
            name = normalized_name(row.payload.get("name"))
            identity = name
            if identity in soft_deleted:
                result.add_error(f"第{row.line_number}行：该名称已被回收站记录占用")
                continue
            if row.action == "add":
                if identity in existing:
                    result.skipped += 1
                    continue
                initials, full = build_pinyin_fields(name)
                conn.execute(
                    "INSERT INTO products(name, brand, unit, pinyin_initials, pinyin_full, origin) VALUES (?, ?, ?, ?, ?, 'import')",
                    (name, row.payload.get("brand") or None, row.payload.get("unit", "个"),
                     initials, full),
                )
                existing.add(identity)
                result.added += 1
                if row.completion:
                    # 字段不完整的新增行：列表里已经带上「待补全」徽标，摘要里如实报数。
                    result.pending_completion += 1
            elif row.action == "update":
                if identity not in existing:
                    result.add_error(f"第{row.line_number}行：同名商品已不存在，无法更新")
                    continue
                initials, full = build_pinyin_fields(name)
                conn.execute(
                    "UPDATE products SET brand=?, unit=?, pinyin_initials=?, pinyin_full=?, updated_at=CURRENT_TIMESTAMP WHERE TRIM(name)=? AND deleted_at IS NULL",
                    (row.payload.get("brand") or None, row.payload.get("unit", "个"),
                     initials, full, name),
                )
                result.added += 1
            else:
                continue

    with get_db() as conn:
        products, suggestions, truncated, todo_count = _product_list_context(conn)
    return render_template(
        "products/list.html",
        products=products,
        suggestions=suggestions,
        truncated=truncated,
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        q="",
        quality="",
        sort="",
        todo_count=todo_count,
        import_result=result,
        **_backup_hint(),
    )


@products_bp.get("/")
def list_products():
    q = request.args.get("q", "").strip()
    # 档案筛选与排序都只接受白名单取值（不把任意串带进查询）。
    quality = request.args.get("quality", "").strip()
    if quality not in ("", "todo"):
        quality = ""
    sort = request.args.get("sort", "").strip()
    if sort not in ("", "pinyin"):
        sort = ""
    with get_db() as conn:
        rows, suggestions, truncated, todo_count = _product_list_context(conn, q, quality, sort)
    return render_template(
        "products/list.html",
        products=rows,
        suggestions=suggestions,
        truncated=truncated,
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        q=q,
        quality=quality,
        sort=sort,
        todo_count=todo_count,
        **_backup_hint(),
    )

@products_bp.get("/api/suggestions")
def product_suggestions():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT name FROM products WHERE deleted_at IS NULL AND {name_match_clause()} ORDER BY usage_count DESC, name LIMIT 20",
            (*name_match_params(q),),
        ).fetchall()
    return jsonify([row["name"] for row in rows])


@products_bp.post("/create")
def create_product_view():
    try:
        initials, full = build_pinyin_fields(request.form["name"])
        create_product(request.form["name"], request.form["unit"], (request.form.get("pinyin_initials", "") or initials), pinyin_full=(request.form.get("pinyin_full", "") or full), brand=request.form.get("brand", ""))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and str(exc) else "新增商品失败，请检查填写内容"
        with get_db() as conn:
            products, suggestions, truncated, todo_count = _product_list_context(conn)
        return render_template("products/list.html", products=products, suggestions=suggestions, truncated=truncated, cents_to_yuan=cents_to_yuan, micro_to_yuan=micro_to_yuan, q="", quality="", sort="", todo_count=todo_count, import_error=message, create_open=True, **_backup_hint()), 400
    return redirect(url_for("products.list_products"))

@products_bp.get("/<int:product_id>/edit")
def edit_product(product_id: int):
    return _render_product_edit(product_id)


def _render_product_edit(product_id: int, *, error: str = "", notice: str = "", status_code: int = 200):
    with get_db() as conn:
        product = conn.execute("SELECT * FROM products WHERE id=? AND deleted_at IS NULL", (product_id,)).fetchone()
        # 客户专属价（2026-10-15）：售价唯一来源 = 开单学习出的客户专属价，所以编辑页要能一眼看到
        # 「这个商品现在对哪些客户是什么价」——用户原话「点进商品后要能看到该客户的专属价」。
        customer_prices = conn.execute(
            """
            SELECT c.name AS customer_name, cp.price_cents, cp.updated_at
            FROM customer_prices AS cp
            JOIN customers AS c ON c.id = cp.customer_id
            WHERE cp.product_id=?
            ORDER BY c.name COLLATE NOCASE ASC, c.id ASC
            """,
            (product_id,),
        ).fetchall()
    if product is None:
        return not_found("商品不存在，可能已被删除或从未存在。", back_url=url_for("products.list_products"))
    # v15（决策 ②/③/⑦）：库存状态、期初与安全库存全部下线，编辑页只剩档案与客户专属价。
    return render_template(
        "products/edit.html",
        product=product,
        cents_to_yuan=cents_to_yuan,
        micro_to_yuan=micro_to_yuan,
        customer_prices=customer_prices,
        error=error,
        notice=notice,
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
    return _render_product_edit(product_id, notice="商品图片已更新")


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
        update_product_record(product_id, request.form["name"], request.form["unit"], brand=request.form.get("brand", ""))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and str(exc) else "保存失败，请检查填写内容"
        return _render_product_edit(product_id, error=message, status_code=400)
    return redirect(url_for("products.list_products"))


# v15（决策 ②/③/⑦）：库存期初的启用/修订入口已删除（`/inventory/initialize`、`/inventory/revise`）。

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
