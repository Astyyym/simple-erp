"""资料文档库路由（2-A，schema v16）。

定位：左边栏「基础资料 → 资料文档」，纯文件柜；可内嵌预览、直接打开、直接打印。
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from erp.db import get_db
from erp.services import documents as doc_service
from erp.services.documents import DocumentError
from erp.utils.errors import not_found

docs_bp = Blueprint("docs", __name__, url_prefix="/docs")

PAGE_SIZE = 50


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _categories() -> list[dict]:
    with get_db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM document_categories ORDER BY sort_order, id"
        ).fetchall()]


def _back_url() -> str:
    target = (request.form.get("next") or request.args.get("next") or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("docs.list_docs")


@docs_bp.get("/")
def list_docs():
    category_id = request.args.get("category_id", type=int)
    q = (request.args.get("q") or "").strip()
    page = max(request.args.get("page", 1, type=int) or 1, 1)

    where = ["d.deleted_at IS NULL"]
    params: list = []
    if category_id:
        where.append("d.category_id=?")
        params.append(category_id)
    if q:
        like = f"%{q}%"
        where.append("(d.title LIKE ? OR d.original_name LIKE ? OR d.note LIKE ?)")
        params.extend([like, like, like])
    where_clause = " AND ".join(where)

    with get_db() as conn:
        categories = [dict(row) for row in conn.execute(
            "SELECT * FROM document_categories ORDER BY sort_order, id"
        ).fetchall()]
        counts_rows = conn.execute(
            "SELECT category_id, COUNT(*) AS n FROM document_files WHERE deleted_at IS NULL GROUP BY category_id"
        ).fetchall()
        counts = {row["category_id"]: int(row["n"]) for row in counts_rows}
        total_all = int(conn.execute(
            "SELECT COUNT(*) FROM document_files WHERE deleted_at IS NULL"
        ).fetchone()[0])
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM document_files d WHERE {where_clause}", params
        ).fetchone()[0])
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = conn.execute(
            f"""SELECT d.*, c.name AS category_name
                FROM document_files d LEFT JOIN document_categories c ON c.id=d.category_id
                WHERE {where_clause}
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT ? OFFSET ?""",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()

    documents = []
    for row in rows:
        item = dict(row)
        item["size_display"] = _human_size(item["size_bytes"])
        item["is_image"] = doc_service.is_image(item["ext"])
        item["is_pdf"] = (item["ext"] or "").lower() == "pdf"
        item["file_url"] = url_for("docs.doc_file", doc_id=item["id"])
        item["preview_url"] = url_for("docs.doc_preview", doc_id=item["id"]) if item["is_image"] else ""
        item["edit_url"] = url_for("docs.edit_doc", doc_id=item["id"])
        item["open_url"] = url_for("docs.doc_open", doc_id=item["id"])
        item["print_url"] = url_for("docs.doc_print", doc_id=item["id"])
        item["delete_url"] = url_for("docs.doc_delete", doc_id=item["id"])
        documents.append(item)

    return render_template(
        "docs/list.html",
        documents=documents,
        categories=categories,
        counts=counts,
        total_all=total_all,
        category_id=category_id,
        q=q,
        page=page,
        total=total,
        total_pages=total_pages,
        max_size_mb=doc_service.max_size_mb(),
        current_back_url=request.full_path if request.query_string else url_for("docs.list_docs"),
    )


@docs_bp.post("/upload")
def upload_docs():
    category_id = request.form.get("category_id", type=int)
    note = (request.form.get("note") or "").strip()
    files = [f for f in request.files.getlist("files") if (f.filename or "").strip()]
    if not files:
        flash("请选择要上传的文件", "danger")
        return redirect(_back_url())

    if category_id is not None:
        existing_ids = {category["id"] for category in _categories()}
        if category_id not in existing_ids:
            flash("所选分类不存在，请重新选择", "danger")
            return redirect(_back_url())

    ok = 0
    errors: list[str] = []
    for file_storage in files:
        try:
            doc_service.save_upload(file_storage, category_id=category_id, note=note)
            ok += 1
        except DocumentError as exc:
            errors.append(f"{file_storage.filename}：{exc}")
        except Exception as exc:  # noqa: BLE001 — 单个文件失败不中断整批
            errors.append(f"{file_storage.filename}：上传失败（{exc}）")

    if ok:
        flash(f"已上传 {ok} 个文件。", "success")
    if errors:
        flash("以下文件未上传：" + "；".join(errors), "danger")
    return redirect(_back_url())


@docs_bp.get("/<int:doc_id>/file")
def doc_file(doc_id: int):
    try:
        doc = doc_service.get_document(doc_id)
        path = doc_service.document_path(doc)
    except DocumentError as exc:
        return not_found(str(exc), back_url=url_for("docs.list_docs"))
    inline = doc_service.is_inline_viewable(doc["ext"])
    return send_file(
        path,
        mimetype=doc["mime"],
        as_attachment=not inline,
        download_name=doc["original_name"],
    )


@docs_bp.get("/<int:doc_id>/preview")
def doc_preview(doc_id: int):
    try:
        doc = doc_service.get_document(doc_id)
    except DocumentError as exc:
        return not_found(str(exc), back_url=url_for("docs.list_docs"))
    preview = doc_service.preview_path(doc)
    if preview is None:
        return not_found("该文档没有预览图", back_url=url_for("docs.list_docs"))
    return send_file(preview, mimetype="image/jpeg")


@docs_bp.post("/<int:doc_id>/open")
def doc_open(doc_id: int):
    try:
        doc = doc_service.get_document(doc_id)
        path = doc_service.document_path(doc)
        doc_service.open_externally(path)
        flash(f"已用本机程序打开：{doc['original_name']}", "success")
    except DocumentError as exc:
        flash(f"打开失败：{exc}", "danger")
    return redirect(_back_url())


@docs_bp.post("/<int:doc_id>/print")
def doc_print(doc_id: int):
    try:
        doc = doc_service.get_document(doc_id)
        path = doc_service.document_path(doc)
        doc_service.print_externally(path)
        flash(f"已调用系统打印：{doc['original_name']}", "success")
    except DocumentError as exc:
        flash(f"打印失败：{exc}。可以「打开」后在程序里打印。", "danger")
    return redirect(_back_url())


@docs_bp.route("/<int:doc_id>/edit", methods=["GET", "POST"])
def edit_doc(doc_id: int):
    try:
        doc = doc_service.get_document(doc_id)
    except DocumentError as exc:
        return not_found(str(exc), back_url=url_for("docs.list_docs"))
    error = ""
    if request.method == "POST":
        title = request.form.get("title", "")
        category_id = request.form.get("category_id", type=int)
        note = request.form.get("note", "")
        try:
            doc_service.update_document(doc_id, title=title, category_id=category_id, note=note)
            flash("文档信息已保存", "success")
            return redirect(url_for("docs.list_docs"))
        except DocumentError as exc:
            error = str(exc)
    return render_template("docs/edit.html", doc=doc, categories=_categories(), error=error)


@docs_bp.post("/<int:doc_id>/delete")
def doc_delete(doc_id: int):
    confirm_text = (request.form.get("confirm_text") or "").strip()
    if confirm_text != "删除":
        flash("删除已取消：确认词必须输入「删除」两个字。", "danger")
        return redirect(_back_url())
    try:
        doc = doc_service.delete_document(doc_id)
        flash(f"已删除文档：{doc['original_name']}（文件已一并删除）", "success")
    except DocumentError as exc:
        flash(f"删除失败：{exc}", "danger")
    return redirect(url_for("docs.list_docs"))


@docs_bp.post("/categories")
def create_category():
    name = request.form.get("name", "")
    try:
        created = doc_service.add_category(name)
        flash(f"已新增分类：{created['name']}", "success")
    except DocumentError as exc:
        flash(f"新增分类失败：{exc}", "danger")
    return redirect(url_for("docs.list_docs"))
