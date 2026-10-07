from flask import Blueprint, render_template, redirect, url_for, request
from erp.db import (
    CUSTOMER_REFERENCE_GUARD_SQL,
    _ORDER_PURGE_TABLES,
    _purge_orders_in_safe_order,
    get_db,
    physically_deletable_order,
    purge_order_blocker,
    purge_order_item,
    purge_product_blocker,
    purge_product_item,
)
from erp.services.accounting import restore_order
from erp.utils.audit import log_action
from erp.utils.errors import error_response
from erp.utils.money import cents_to_yuan

recycle_bp = Blueprint("recycle", __name__, url_prefix="/recycle")

# 回收站类型 → 展示标签/徽标色。四类单据共用一张列表，但清理规则各自不同。
_TYPE_LABELS = {"sale": "销售单", "return": "退货单", "purchase": "拿货单", "purchase_return": "退拿货单"}
_TYPE_BADGES = {"sale": "bg-primary", "return": "bg-danger", "purchase": "bg-warning text-dark", "purchase_return": "bg-info text-dark"}
_STATUS_LABELS = {"draft": "草稿", "saved": "正式保存", "printed": "已打印", "void": "已作废"}
_STATUS_BADGES = {"draft": "bg-secondary", "saved": "bg-success", "printed": "bg-info text-dark", "void": "bg-danger"}


def _document_rows(conn):
    """四类单据合并成一张按删除时间倒序的列表。

    清理能力按类型分别判定，不复用「状态是草稿就一定能删」的粗略假设：
    销售/退货、拿货、退拿货统一走 physically_deletable_order 的真实引用检查。
    """
    rows = []
    for kind in ("order", "purchase_order", "purchase_return"):
        if kind == "order":
            found = conn.execute(
                "SELECT o.id, o.order_no, o.order_type, o.total_amount_cents, o.status, o.deleted_at,"
                " COALESCE(c.name, '历史未关联对象') AS customer_name"
                " FROM orders o LEFT JOIN customers c ON c.id=o.customer_id"
                " WHERE o.deleted_at IS NOT NULL"
            ).fetchall()
        elif kind == "purchase_order":
            found = conn.execute(
                "SELECT id, order_no, 'purchase' AS order_type, total_amount_cents, status, deleted_at,"
                " COALESCE(NULLIF(customer_name, ''), '历史未关联对象') AS customer_name"
                " FROM purchase_orders WHERE deleted_at IS NOT NULL"
            ).fetchall()
        else:
            found = conn.execute(
                "SELECT id, order_no, 'purchase_return' AS order_type, total_amount_cents, status, deleted_at,"
                " COALESCE(NULLIF(customer_name, ''), '历史未关联对象') AS customer_name, version"
                " FROM purchase_return_orders WHERE deleted_at IS NOT NULL"
            ).fetchall()
        for row in found:
            order_type = row["order_type"]
            keys = row.keys()
            rows.append({
                "kind": kind,
                "id": row["id"],
                "key": f"{order_type}:{row['id']}",
                "order_no": row["order_no"],
                "order_type": order_type,
                "type_label": _TYPE_LABELS[order_type],
                "type_badge": _TYPE_BADGES[order_type],
                "customer_name": row["customer_name"],
                "total_amount_cents": row["total_amount_cents"],
                "status": row["status"],
                "status_label": _STATUS_LABELS.get(row["status"], row["status"]),
                "status_badge": _STATUS_BADGES.get(row["status"], "bg-secondary"),
                "deleted_at": row["deleted_at"],
                # 退拿货的恢复/清理都要带版本号做并发校验，其余类型没有版本字段。
                "version": row["version"] if "version" in keys else "",
                # 恢复对所有类型都可用；永久清理才按类型区分。
                # 能否永久删除 + 不能时的可读原因（供 UI 直接展示，别让用户猜）。
                "purge_blocker": purge_order_blocker(conn, kind, row["id"]),
                "can_purge": physically_deletable_order(conn, kind, row["id"]),
            })
    rows.sort(key=lambda item: (item["deleted_at"] or "", item["id"]), reverse=True)
    return rows


@recycle_bp.get("/")
def recycle_bin():
    with get_db() as conn:
        documents = _document_rows(conn)
        products = conn.execute("SELECT * FROM products WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
        customers = conn.execute("SELECT * FROM customers WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
    return render_template(
        "recycle/index.html",
        documents=documents,
        orders=[row for row in documents if row["kind"] == "order"],
        purchase_orders=[row for row in documents if row["kind"] == "purchase_order"],
        purchase_returns=[row for row in documents if row["kind"] == "purchase_return"],
        products=products,
        customers=customers,
        cents_to_yuan=cents_to_yuan,
    )


# 勾选键前缀（= 单据类型）→ 回收站内部 kind。
_KEY_TO_KIND = {
    "sale": "order", "return": "order",
    "purchase": "purchase_order", "purchase_return": "purchase_return",
    "product": "product", "customer": "customer",
}


def _parse_selected_keys(raw_keys):
    """把 `类型:id` 键解析成 (kind, id)，未知类型直接拒绝而不是静默跳过。"""
    parsed = []
    for raw in raw_keys:
        prefix, _, raw_id = str(raw).partition(":")
        kind = _KEY_TO_KIND.get(prefix)
        if kind is None or not raw_id.isdigit():
            raise ValueError(f"未知回收站类型：{raw}")
        parsed.append((kind, int(raw_id)))
    if not parsed:
        raise ValueError("请先选中要操作的记录")
    return parsed


@recycle_bp.post("/bulk")
def bulk_action():
    """合并列表的统一批量端点：一次提交可混合四类单据。

    退拿货的版本号按 `version_<id>` 单独提交，因此不依赖 ids 的顺序；
    销售/退货、拿货、退拿货分别走各自既有的恢复/清理规则。
    """
    from erp.services import purchase_returns as return_service

    action = request.form.get("action", "")
    if action not in {"restore", "purge"}:
        return "未知批量操作", 400
    try:
        parsed = _parse_selected_keys(request.form.getlist("ids"))
        for kind, item_id in parsed:
            if kind == "order":
                if action == "restore":
                    restore_order(item_id)
                else:
                    with get_db() as conn:
                        _purge_one(conn, kind, item_id)
            elif kind == "purchase_return":
                expected = request.form.get(f"version_{item_id}")
                if action == "restore":
                    return_service.restore(item_id, expected_version=expected)
                else:
                    return_service.recycle_batch([item_id], [expected], action="purge")
            else:
                with get_db() as conn:
                    if action == "restore":
                        table = {"purchase_order": "purchase_orders", "product": "products", "customer": "customers"}[kind]
                        conn.execute(f"UPDATE {table} SET deleted_at=NULL, delete_reason='' WHERE id=?", (item_id,))
                    else:
                        _purge_one(conn, kind, item_id)
    except ValueError as exc:
        return str(exc), 400
    log_action("bulk_restore_recycle" if action == "restore" else "bulk_purge_recycle", "batch", "batch", f"批量 {len(parsed)} 条")
    return redirect(url_for("recycle.recycle_bin"))

@recycle_bp.post("/<kind>/<int:item_id>/restore")
def restore(kind: str, item_id: int):
    if kind not in {'order','purchase_order','purchase_return','product','customer'}:
        return '未知回收站类型',400
    if kind == 'purchase_return':
        from erp.services.purchase_returns import restore as restore_return
        try:
            restore_return(item_id,expected_version=request.form.get('version'))
        except ValueError as exc:
            return str(exc),400
        return redirect(url_for('recycle.recycle_bin'))
    if kind == "order":
        try:
            restore_order(item_id)
        except ValueError as exc:
            return str(exc), 400
        return redirect(url_for("recycle.recycle_bin"))
    table = {"order": "orders", "purchase_order": "purchase_orders", "product": "products", "customer": "customers"}[kind]
    with get_db() as conn:
        conn.execute(f"UPDATE {table} SET deleted_at=NULL, delete_reason='' WHERE id=?", (item_id,))
    log_action("restore_from_recycle", kind, item_id, "从回收站恢复")
    return redirect(url_for("recycle.recycle_bin"))

def _purge_one(conn, kind: str, item_id: int) -> None:
    if kind not in {'order','purchase_order','purchase_return','product','customer'}:
        raise ValueError('未知回收站类型')
    if kind in {'order','purchase_order','purchase_return'}:
        # 连带明细与它自己的库存流水一起清掉（用户口径：不要了就清干净，不留孤儿流水）。
        purge_order_item(conn, kind, item_id)
    elif kind == "product":
        purge_product_item(conn, item_id)
    elif kind == "customer":
        # 有历史单据/收款/调整的客户不能物理删除，否则会破坏账务追溯。
        # 注意：guard 不满足时 DELETE 影响 0 行且不报错，会变成「点了删除、页面刷新、
        # 东西还在」的静默失败。这里显式检查影响行数并给出可见原因。
        cursor = conn.execute(f"DELETE FROM customers WHERE id=? AND {CUSTOMER_REFERENCE_GUARD_SQL}", (item_id,))
        if cursor.rowcount == 0:
            exists = conn.execute("SELECT 1 FROM customers WHERE id=?", (item_id,)).fetchone()
            if exists is None:
                raise ValueError("客户不存在")
            raise ValueError("该客户有历史单据或账务记录，只能保留，不能永久删除")

@recycle_bp.post("/<kind>/<int:item_id>/purge")
def purge(kind: str, item_id: int):
    try:
        if kind == 'purchase_return':
            from erp.services.purchase_returns import recycle_batch
            recycle_batch([item_id],[request.form.get('version')],action='purge')
            return redirect(url_for('recycle.recycle_bin'))
        with get_db() as conn:
            _purge_one(conn, kind, item_id)
    except ValueError as exc:
        return error_response(str(exc), title="未能永久删除", back_url=url_for("recycle.recycle_bin"))
    log_action("purge_recycle", kind, item_id, "从回收站确认删除")
    return redirect(url_for("recycle.recycle_bin"))

@recycle_bp.post("/bulk/<kind>/restore")
def bulk_restore(kind: str):
    if kind not in {'order','purchase_order','purchase_return','product','customer'}:
        return '未知回收站类型',400
    if kind == 'purchase_return':
        from erp.services.purchase_returns import recycle_batch
        try:
            recycle_batch([int(x) for x in request.form.getlist('ids')],request.form.getlist('versions'),action='restore')
        except ValueError as exc:
            return str(exc),400
        return redirect(url_for('recycle.recycle_bin'))
    ids = [int(x) for x in request.form.getlist("ids")]
    if kind == "order":
        try:
            for item_id in ids:
                restore_order(item_id)
        except ValueError as exc:
            return str(exc), 400
        log_action("bulk_restore_recycle", kind, "batch", f"批量恢复 {len(ids)} 条")
        return redirect(url_for("recycle.recycle_bin"))
    table = {"purchase_order": "purchase_orders", "product": "products", "customer": "customers"}[kind]
    with get_db() as conn:
        conn.executemany(f"UPDATE {table} SET deleted_at=NULL, delete_reason='' WHERE id=?", [(i,) for i in ids])
    log_action("bulk_restore_recycle", kind, "batch", f"批量恢复 {len(ids)} 条")
    return redirect(url_for("recycle.recycle_bin"))

@recycle_bp.post("/bulk/<kind>/purge")
def bulk_purge(kind: str):
    """批量永久删除：能删的都删掉，删不掉的逐条报出原因。

    不再「遇到第一个失败就整体中止」，也不再像旧版那样静默跳过（那会让用户以为全删了）。
    """
    ids = [int(x) for x in request.form.getlist("ids")]
    if kind == 'purchase_return':
        from erp.services.purchase_returns import recycle_batch
        try:
            recycle_batch(ids, request.form.getlist('versions'), action='purge')
        except ValueError as exc:
            return error_response(str(exc), title="未能永久删除", back_url=url_for("recycle.recycle_bin"))
        log_action("bulk_purge_recycle", kind, "batch", f"批量确认删除 {len(ids)} 条")
        return redirect(url_for("recycle.recycle_bin"))

    failures: list[str] = []
    removed = 0
    with get_db() as conn:
        if kind in _ORDER_PURGE_TABLES:
            # 多趟扫描自动处理「引用方必须先删」的情况（如退货明细引用销售明细）。
            count, blocked = _purge_orders_in_safe_order(conn, kind, ids)
            removed += count
            failures.extend(f"#{item_id}：{reason}" for item_id, reason in blocked)
        else:
            # 商品 / 客户走各自的清理逻辑（没有引用顺序问题）。
            for item_id in ids:
                try:
                    _purge_one(conn, kind, item_id)
                    removed += 1
                except ValueError as exc:
                    failures.append(f"#{item_id}：{exc}")
    if failures:
        return error_response(
            f"已删除 {removed} 条；以下 {len(failures)} 条未能删除——" + "；".join(failures),
            title="部分记录未能永久删除",
            back_url=url_for("recycle.recycle_bin"),
        )
    log_action("bulk_purge_recycle", kind, "batch", f"批量确认删除 {len(ids)} 条")
    return redirect(url_for("recycle.recycle_bin"))


@recycle_bp.post("/empty")
def empty_recycle_bin():
    """一键清空回收站：能清的清掉，清不掉的报出原因。

    顺序固定为 退拿货 → 销售/退货 → 拿货 → 商品 → 客户：
    退拿货的明细可能引用拿货的明细行，先清退拿货，被引用的拿货单才能满足「无活引用」；
    商品在单据之后处理，此时引用它的单据流水已随单据清掉，商品才有机会被清走。
    有历史账务的客户属于账务追溯底线，始终保留并报出。
    """
    from erp.services import purchase_returns as return_service

    removed = 0
    failures: list[str] = []
    with get_db() as conn:
        # 单据：退拿货（需要逐条版本号）→ 销售/退货 → 拿货
        returns = conn.execute(
            "SELECT id, version FROM purchase_return_orders WHERE deleted_at IS NOT NULL"
        ).fetchall()
        for row in returns:
            try:
                return_service.recycle_batch([row["id"]], [row["version"]], action="purge")
                removed += 1
            except ValueError as exc:
                failures.append(f"退拿货 #{row['id']}：{exc}")
        for kind in ("order", "purchase_order"):
            table = _ORDER_PURGE_TABLES[kind][0]
            ids = [row["id"] for row in conn.execute(f"SELECT id FROM {table} WHERE deleted_at IS NOT NULL").fetchall()]
            # 多趟扫描自动处理「退货引用销售明细」这类必须先删引用方的情况。
            count, blocked = _purge_orders_in_safe_order(conn, kind, ids)
            removed += count
            failures.extend(f"#{item_id}：{reason}" for item_id, reason in blocked)
        # 商品
        for row in conn.execute("SELECT id, name FROM products WHERE deleted_at IS NOT NULL").fetchall():
            try:
                purge_product_item(conn, row["id"])
                removed += 1
            except ValueError as exc:
                failures.append(f"商品 {row['name']}：{exc}")
        # 客户：有账务的始终保留
        for row in conn.execute("SELECT id, name FROM customers WHERE deleted_at IS NOT NULL").fetchall():
            cursor = conn.execute(f"DELETE FROM customers WHERE id=? AND {CUSTOMER_REFERENCE_GUARD_SQL}", (row["id"],))
            if cursor.rowcount:
                removed += 1
            else:
                failures.append(f"客户 {row['name']}：有历史单据或账务记录，只能保留")
    if failures:
        return error_response(
            f"已清空 {removed} 条；以下 {len(failures)} 条保留——" + "；".join(failures),
            title="回收站已部分清空",
            back_url=url_for("recycle.recycle_bin"),
            severity="warning",
        )
    log_action("empty_recycle_bin", "recycle", "all", f"清空回收站 {removed} 条")
    return redirect(url_for("recycle.recycle_bin"))
