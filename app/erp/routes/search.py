"""全局搜索（1-C）：顶栏常驻搜索框 → 跨源分组结果。

口径（详见执行计划 1-C）：
- 只搜**未软删除**数据（不搜回收站）；作废单据仍可搜到并标注「已作废」（作废单仍需追溯）；
- 不做全文索引，单机库小，用 LIKE（YAGNI）；
- 分组：单据（销售/退货/拿货/退拿货）、客户、商品、资料文档（2-A 后自动接入）。
"""
from __future__ import annotations

from flask import Blueprint, render_template, request

from erp.db import get_db
from erp.utils.money import cents_to_yuan
from erp.utils.pinyin import name_match_clause, name_match_params

search_bp = Blueprint("search", __name__, url_prefix="/search")

# 每组最多返回条数（单机库，够用即可；超出显示「仅显示前 N 条」提示）。
GROUP_LIMIT = 50

_ORDER_TYPE_LABELS = {
    "sale": "销售单",
    "return": "退货单",
    "purchase": "拿货单",
    "purchase_return": "退拿货单",
}


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _order_detail_url(order_type: str, order_id: int) -> str:
    if order_type == "purchase":
        return f"/purchases/{order_id}"
    if order_type == "purchase_return":
        return f"/purchases/return/{order_id}"
    return f"/orders/{order_id}"


def _search_orders(conn, raw_like: str, q: str) -> list[dict]:
    """销售/退货单据：单号 / 客户名（含拼音）/ 明细品名。"""
    rows = conn.execute(
        f"""
        SELECT o.id, o.order_no, o.order_date AS business_date, o.status, o.order_type,
               o.total_amount_cents, COALESCE(c.name, '') AS customer_name
        FROM orders o LEFT JOIN customers c ON c.id = o.customer_id
        WHERE o.deleted_at IS NULL AND (
            o.order_no LIKE ? OR {name_match_clause('c.')}
            OR EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.product_name LIKE ?)
        )
        ORDER BY o.order_date DESC, o.id DESC
        LIMIT ?
        """,
        (raw_like, *name_match_params(q), raw_like, GROUP_LIMIT),
    ).fetchall()
    return [dict(row) for row in rows]


def _search_purchase_orders(conn, raw_like: str) -> list[dict]:
    """拿货/退拿货：单号 / 客户名快照 / 明细品名。"""
    combined: list[dict] = []
    for table, item_table, fk, order_type, notes in (
        ("purchase_orders", "purchase_order_items", "purchase_order_id", "purchase", "source_notes"),
        ("purchase_return_orders", "purchase_return_items", "purchase_return_id", "purchase_return", "notes"),
    ):
        rows = conn.execute(
            f"""
            SELECT o.id, o.order_no, o.business_date, o.status, o.customer_name,
                   o.total_amount_cents, '{order_type}' AS order_type
            FROM {table} o
            WHERE o.deleted_at IS NULL AND (
                o.order_no LIKE ? OR o.customer_name LIKE ?
                OR EXISTS (SELECT 1 FROM {item_table} i WHERE i.{fk} = o.id AND i.product_name LIKE ?)
            )
            ORDER BY o.business_date DESC, o.id DESC
            LIMIT ?
            """,
            (raw_like, raw_like, raw_like, GROUP_LIMIT),
        ).fetchall()
        combined.extend(dict(row) for row in rows)
    combined.sort(key=lambda row: (row["business_date"] or "", row["id"]), reverse=True)
    return combined[:GROUP_LIMIT]


def _search_customers(conn, q: str) -> list[dict]:
    rows = conn.execute(
        f"""SELECT id, name, phone FROM customers
             WHERE deleted_at IS NULL AND {name_match_clause()}
             ORDER BY name COLLATE NOCASE, id LIMIT ?""",
        (*name_match_params(q), GROUP_LIMIT),
    ).fetchall()
    return [dict(row) for row in rows]


def _search_products(conn, q: str) -> list[dict]:
    rows = conn.execute(
        f"""SELECT id, name, unit FROM products
             WHERE deleted_at IS NULL AND {name_match_clause()}
             ORDER BY name COLLATE NOCASE, id LIMIT ?""",
        (*name_match_params(q), GROUP_LIMIT),
    ).fetchall()
    return [dict(row) for row in rows]


def _search_documents(conn, raw_like: str) -> list[dict]:
    """资料文档（2-A 之后才有表）；表不存在时返回空，不影响其它分组。"""
    if not _table_exists(conn, "document_files"):
        return []
    rows = conn.execute(
        """SELECT id, title, original_name, ext FROM document_files
             WHERE deleted_at IS NULL AND (title LIKE ? OR original_name LIKE ?)
             ORDER BY created_at DESC, id DESC LIMIT ?""",
        (raw_like, raw_like, GROUP_LIMIT),
    ).fetchall()
    return [dict(row) for row in rows]


@search_bp.get("/")
def search_results():
    q = (request.args.get("q") or "").strip()
    raw_like = f"%{q}%"
    groups: list[dict] = []
    if q:
        with get_db() as conn:
            orders = _search_orders(conn, raw_like, q) + _search_purchase_orders(conn, raw_like)
            orders.sort(key=lambda row: (row["business_date"] or "", row["id"]), reverse=True)
            orders = orders[:GROUP_LIMIT]
            customers = _search_customers(conn, q)
            products = _search_products(conn, q)
            documents = _search_documents(conn, raw_like)

        order_items = [
            {
                "kind": "order",
                "type_label": _ORDER_TYPE_LABELS.get(row["order_type"], row["order_type"]),
                "order_no": row["order_no"],
                "business_date": row["business_date"],
                "customer_name": row["customer_name"],
                "amount_cents": row["total_amount_cents"],
                "is_void": row["status"] == "void",
                "url": _order_detail_url(row["order_type"], row["id"]),
            }
            for row in orders
        ]
        customer_items = [
            {"kind": "customer", "name": row["name"], "phone": row["phone"], "url": f"/customers/{row['id']}/edit"}
            for row in customers
        ]
        product_items = [
            {"kind": "product", "name": row["name"], "unit": row["unit"], "url": f"/products/{row['id']}/edit"}
            for row in products
        ]
        document_items = [
            {
                "kind": "document",
                "title": row["title"],
                "original_name": row["original_name"],
                "ext": row["ext"],
                "url": f"/docs/{row['id']}/file",
            }
            for row in documents
        ]
        groups = [
            {"key": "orders", "title": "单据", "items": order_items},
            {"key": "customers", "title": "客户", "items": customer_items},
            {"key": "products", "title": "商品", "items": product_items},
            {"key": "documents", "title": "资料文档", "items": document_items},
        ]
    total = sum(len(group["items"]) for group in groups)
    return render_template(
        "search/results.html",
        q=q,
        groups=groups,
        total=total,
        group_limit=GROUP_LIMIT,
        cents_to_yuan=cents_to_yuan,
    )
