"""手机只读端的查询层：**物理只读连接** + 列常量 + 批量余额。

为什么单独一层（不是复用 `routes/` 里的实现）：

1. 手机端所有查询必须走 `sqlite3.connect("file:...?mode=ro", uri=True)` +
   `PRAGMA busy_timeout`。主应用是 `threads=8`（`desktop_app.py:217`）且用
   `BEGIN IMMEDIATE` 写，普通连接读会撞 `database is locked`；`mode=ro` 同时让
   「只读」成为数据库层的物理保证——代码写错也写不进去。
2. 只查需要的列（**不写 `SELECT *`**，`SELECT *` 会把进价、软删原因等
   一并带进手机端可访问面）。

口径来源（不许自创，2026-10-17 复核）：

- 客户余额：权威实现是 `services/accounting.py:get_customer_account_ledger()`；
  `customer_balance_cents()` 在 `app/` 内零调用（只有测试在调），**禁止调用**。
- 今日 4 类：`routes/orders.py:_today_history()`（销售/退货，`order_date`，
  `status IN ('saved','printed')`）+ `routes/purchases.py:_purchase_today_history()`
  （拿货/退拿货，`business_date`，`status='saved'`）。**合并这两个函数的返回值**，
  不重写 UNION、不自己发明状态过滤。
- 退货单金额库里已是负数（`accounting.py:382/427`），拿货/退拿货存正数；
  手机端唯一的减法是两个净额。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

from erp.db import db_path
from erp.routes.orders import _resolve_date_range, _today_history
from erp.routes.purchases import _purchase_today_history
from erp.services.accounting import get_customer_account_ledger
from erp.utils.filter_chips import ORDER_TYPE_ORDER
from erp.utils.pinyin import name_match_clause, name_match_params

# 列常量：任何新增查询都从这里挑列，避免 SELECT * 把不该给手机端的字段带出去。
CUSTOMER_COLUMNS = "c.id, c.name, c.pinyin_initials, c.phone"
PRODUCT_COLUMNS = "p.id, p.name, p.unit, p.usage_count"
DOCUMENT_COLUMNS = (
    "id, order_no, customer_id, customer_name, business_date, "
    "order_type, total_amount_cents, status, created_at"
)

# 手机端只看「生效的单据」：销售/退货有效态是 saved/printed，拿货/退拿货只有 saved。
# 与今日概览、余额口径一致（作废单不计金额，也不该出现在手机列表里）。
ORDER_STATUS_SQL = "('saved','printed')"
PURCHASE_STATUS_SQL = "('saved')"

BUSY_TIMEOUT_MS = 4000
DOCUMENT_LIST_LIMIT = 200
PRODUCT_LIST_LIMIT = 60


@contextmanager
def connect_readonly():
    """手机端唯一的数据库入口：`mode=ro` + `busy_timeout` + `query_only`。"""
    conn = sqlite3.connect(
        f"file:{db_path().as_posix()}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA query_only=1")
    try:
        yield conn
    finally:
        conn.close()


def _placeholders(values) -> str:
    return ", ".join("?" for _ in values)


def customer_balances(ids: list[int]) -> dict[int, int]:
    """一条 SQL 批量算多个客户的当前欠款（不做 N+1、不逐客户开连接）。

    口径与 `get_customer_account_ledger()` 的 `current_balance_cents` 逐项一致：
    期初 + 销售/退货（saved/printed 且未删）+ 调整（active）− 收款（active）
    − 拿货（saved 且未删）+ 退拿货（saved 且未删）。
    对拍测试见 `tests/test_mobile_queries.py`。
    """
    cleaned: list[int] = []
    for value in ids or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number not in cleaned:
            cleaned.append(number)
    if not cleaned:
        return {}
    sql = f"""
        SELECT c.id AS customer_id,
               COALESCE(c.opening_balance_cents, 0)
             + COALESCE((SELECT SUM(o.total_amount_cents) FROM orders o
                          WHERE o.customer_id = c.id AND o.status IN {ORDER_STATUS_SQL}
                            AND o.deleted_at IS NULL), 0)
             + COALESCE((SELECT SUM(a.amount_cents) FROM adjustments a
                          WHERE a.customer_id = c.id AND a.status='active'), 0)
             - COALESCE((SELECT SUM(p.amount_cents) FROM payments p
                          WHERE p.customer_id = c.id AND p.status='active'), 0)
             - COALESCE((SELECT SUM(po.total_amount_cents) FROM purchase_orders po
                          WHERE po.customer_id = c.id AND po.status='saved'
                            AND po.deleted_at IS NULL), 0)
             + COALESCE((SELECT SUM(pr.total_amount_cents) FROM purchase_return_orders pr
                          WHERE pr.customer_id = c.id AND pr.status='saved'
                            AND pr.deleted_at IS NULL), 0) AS balance_cents
          FROM customers c
         WHERE c.id IN ({_placeholders(cleaned)}) AND c.deleted_at IS NULL
    """
    with connect_readonly() as conn:
        rows = conn.execute(sql, cleaned).fetchall()
    return {int(row["customer_id"]): int(row["balance_cents"] or 0) for row in rows}


# ---------------------------------------------------------------------------
# 客户
# ---------------------------------------------------------------------------

def list_customers(q: str = "", *, only_debt: bool = False, limit: int = 400) -> dict:
    """客户列表：按当前欠款从多到少；欠款只用批量 SQL 算一次。"""
    q = (q or "").strip()
    where = ["c.deleted_at IS NULL"]
    params: list = []
    if q:
        where.append(name_match_clause("c."))
        params.extend(name_match_params(q))
    with connect_readonly() as conn:
        rows = conn.execute(
            f"SELECT {CUSTOMER_COLUMNS} FROM customers c WHERE {' AND '.join(where)}"
            " ORDER BY c.name COLLATE NOCASE ASC LIMIT ?",
            (*params, max(limit, 1) + 1),
        ).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    balances = customer_balances([row["id"] for row in rows])
    customers = [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "pinyin": row["pinyin_initials"] or "",
            "phone": row["phone"] or "",
            "balance_cents": int(balances.get(int(row["id"]), 0)),
        }
        for row in rows
    ]
    customers.sort(key=lambda item: (-item["balance_cents"], item["name"]))
    if only_debt:
        customers = [item for item in customers if item["balance_cents"] > 0]
    return {"customers": customers, "truncated": truncated, "query": q}


def customer_brief(customer_id: int) -> dict | None:
    """只取客户 id + 名称（单据列表按客户筛选时显示页头用）。"""
    with connect_readonly() as conn:
        row = conn.execute(
            "SELECT c.id, c.name FROM customers c WHERE c.id=? AND c.deleted_at IS NULL",
            (customer_id,),
        ).fetchone()
    return {"id": int(row["id"]), "name": row["name"]} if row else None


def customer_detail(
    customer_id: int, *, ledger_limit: int = 10, document_limit: int = 5
) -> dict | None:
    """客户详情：余额三段 + 最近流水 + 最近单据（含拿货/退拿货）。

    余额与流水只走 `get_customer_account_ledger()`（桌面端在用的权威实现），
    这里通过 `conn=` 传入只读连接，保证手机端**没有**可写连接。
    """
    with connect_readonly() as conn:
        row = conn.execute(
            "SELECT c.id, c.name, c.pinyin_initials, c.phone, c.address FROM customers c"
            " WHERE c.id=? AND c.deleted_at IS NULL",
            (customer_id,),
        ).fetchone()
        if row is None:
            return None
        ledger = get_customer_account_ledger(customer_id, conn=conn)
        documents = _list_documents(conn=conn, customer_id=customer_id, limit=document_limit)
    return {
        "customer": {
            "id": int(row["id"]),
            "name": row["name"],
            "pinyin": row["pinyin_initials"] or "",
            "phone": row["phone"] or "",
            "address": row["address"] or "",
        },
        "opening_balance_cents": ledger["opening_balance_cents"],
        "current_balance_cents": ledger["current_balance_cents"],
        "change_cents": ledger["current_balance_cents"] - ledger["opening_balance_cents"],
        "entries": ledger["entries"][:ledger_limit],
        "documents": documents["documents"],
        "documents_truncated": documents["truncated"],
    }


# ---------------------------------------------------------------------------
# 今日概览（4 类合并）
# ---------------------------------------------------------------------------

def _local_hm(created_at: str) -> str:
    """`created_at` 存的是 UTC（SQLite CURRENT_TIMESTAMP），显示要换成本地时间。"""
    try:
        moment = datetime.fromisoformat(str(created_at))
    except (TypeError, ValueError):
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%H:%M")


def mobile_document_url(order_type: str, source_id: int) -> str:
    """今日事件跳转到**手机端**单据详情（不是桌面端 URL）。"""
    return f"/m/orders/{order_type}/{int(source_id)}"


def today_overview(today: str | None = None) -> dict:
    """今日 4 类：合并两个现成函数的返回值，按类型分组求和 + 两段净额 + 时间轴。

    - 顺序固定 `ORDER_TYPE_ORDER`（同 `filter_chips.py:19`），不自定义。
    - 单数用 `len()`，不为计数再查一次库。
    - 某类为 0 也保留该行（由模板负责显示 `0 单 ¥0`）。
    """
    day = today or date.today().isoformat()
    with connect_readonly() as conn:
        sale_return = _today_history(conn, day)
        purchases = _purchase_today_history(
            conn, day, detail_url_builder=mobile_document_url
        )

    grouped = {key: {"type": key, "count": 0, "amount_cents": 0} for key in ORDER_TYPE_ORDER}
    events = []
    for row in list(sale_return) + list(purchases):
        order_type = row.get("order_type")
        if order_type not in grouped:
            continue
        grouped[order_type]["count"] += 1
        grouped[order_type]["amount_cents"] += int(row.get("total_amount_cents") or 0)
        events.append(
            {
                "order_type": order_type,
                "order_no": row.get("order_no", ""),
                "customer_name": row.get("customer_name", ""),
                "amount_cents": int(row.get("total_amount_cents") or 0),
                "source_id": int(row.get("id") or 0),
                "time": _local_hm(row.get("created_at", "")),
                "sort_at": str(row.get("created_at", "") or ""),
            }
        )
    events.sort(key=lambda item: (item["sort_at"], item["source_id"]), reverse=True)

    by_type = [grouped[key] for key in ORDER_TYPE_ORDER]
    return {
        "date": day,
        "types": by_type,
        "net_sale_cents": grouped["sale"]["amount_cents"] + grouped["return"]["amount_cents"],
        "net_purchase_cents": (
            grouped["purchase"]["amount_cents"] - grouped["purchase_return"]["amount_cents"]
        ),
        "document_count": sum(entry["count"] for entry in by_type),
        "events": events,
    }


# ---------------------------------------------------------------------------
# 单据
# ---------------------------------------------------------------------------

def resolve_mobile_range(args) -> dict:
    """手机端日期筛选 → 统一 `start_date` / `end_date`（复用桌面端的换算，不另写规则）。

    `date_mode`：`day`（按日，起止两端都含）/`month`/`year`/`all`（不加日期条件）。
    默认落在「今天」，与今日概览一致。
    """
    mode = str(args.get("date_mode") or "day").strip().lower()
    if mode not in {"day", "month", "year", "all"}:
        mode = "day"
    if mode == "all":
        return {
            "mode": mode,
            "start_date": "",
            "end_date": "",
            "year": date.today().year,
            "month": date.today().month,
            "label": "全部时间",
        }

    desktop_mode = {"day": "range", "month": "month", "year": "year"}[mode]
    (_, year, month, start_date, end_date, *_rest) = _resolve_date_range(
        desktop_mode,
        str(args.get("year") or "").strip(),
        str(args.get("month") or "").strip(),
        str(args.get("start_date") or "").strip(),
        str(args.get("end_date") or "").strip(),
        None,
        None,
    )
    if mode == "year":
        label = f"{year} 年"
    elif mode == "month":
        label = f"{year} 年 {month} 月"
    elif start_date == end_date:
        label = f"{int(start_date[0:4])} 年 {int(start_date[5:7])} 月 {int(start_date[8:10])} 日"
    else:
        label = f"{start_date[5:].replace('-', '/')} – {end_date[5:].replace('-', '/')}"
    return {
        "mode": mode,
        "start_date": start_date,
        "end_date": end_date,
        "year": year,
        "month": month,
        "label": label,
    }


def list_documents(
    *,
    order_type: str = "",
    start_date: str = "",
    end_date: str = "",
    customer_id: int | None = None,
    limit: int = DOCUMENT_LIST_LIMIT,
) -> dict:
    """四类单据列表：抄桌面端 `routes/orders.py:370-388` 的 UNION 结构。

    两个不同点（都是口径要求，不是偷懒）：日期列名不同（`order_date` vs
    `business_date`），状态过滤逐表照抄（销售/退货含 printed，拿货/退拿货只有 saved）。
    """
    with connect_readonly() as conn:
        return _list_documents(
            conn=conn,
            order_type=order_type,
            start_date=start_date,
            end_date=end_date,
            customer_id=customer_id,
            limit=limit,
        )


def _list_documents(
    *,
    conn: sqlite3.Connection,
    order_type: str = "",
    start_date: str = "",
    end_date: str = "",
    customer_id: int | None = None,
    limit: int = DOCUMENT_LIST_LIMIT,
) -> dict:
    if order_type and order_type not in ORDER_TYPE_ORDER:
        order_type = ""

    def build_conditions(for_orders: bool) -> tuple[str, list]:
        if for_orders:
            conditions = ["o.deleted_at IS NULL", f"o.status IN {ORDER_STATUS_SQL}"]
            date_column = "o.order_date"
            customer_column = "o.customer_id"
        else:
            conditions = ["deleted_at IS NULL", f"status IN {PURCHASE_STATUS_SQL}"]
            date_column = "business_date"
            customer_column = "customer_id"
        params: list = []
        if start_date:
            conditions.append(f"{date_column} >= ?")
            params.append(start_date)
        if end_date:
            conditions.append(f"{date_column} <= ?")
            params.append(end_date)
        if customer_id:
            conditions.append(f"{customer_column} = ?")
            params.append(int(customer_id))
        return " AND ".join(conditions), params

    order_where, order_params = build_conditions(True)
    purchase_where, purchase_params = build_conditions(False)

    branches: list[tuple[str, list]] = []
    if not order_type or order_type in {"sale", "return"}:
        for value in (["sale", "return"] if not order_type else [order_type]):
            branches.append(
                (
                    "\nSELECT o.id, o.order_no, o.customer_id,"
                    " COALESCE(c.name, '（未知客户）') AS customer_name,"
                    " o.order_date AS business_date, o.order_type, o.total_amount_cents, o.status,"
                    " o.created_at"
                    "\n  FROM orders o LEFT JOIN customers c ON c.id = o.customer_id"
                    f"\n WHERE {order_where} AND o.order_type=?",
                    [*order_params, value],
                )
            )
    if not order_type or order_type == "purchase":
        branches.append(
            (
                "\nSELECT id, order_no, customer_id,"
                " COALESCE(customer_name, '（未知对象）') AS customer_name,"
                " business_date, 'purchase' AS order_type, total_amount_cents, status, created_at"
                f"\n  FROM purchase_orders WHERE {purchase_where}",
                list(purchase_params),
            )
        )
    if not order_type or order_type == "purchase_return":
        branches.append(
            (
                "\nSELECT id, order_no, customer_id,"
                " COALESCE(customer_name, '（未知对象）') AS customer_name,"
                " business_date, 'purchase_return' AS order_type, total_amount_cents, status, created_at"
                f"\n  FROM purchase_return_orders WHERE {purchase_where}",
                list(purchase_params),
            )
        )

    if not branches:
        return {"documents": [], "truncated": False}

    union_sql = "\nUNION ALL\n".join(branch for branch, _ in branches)
    params: list = []
    for _, branch_params in branches:
        params.extend(branch_params)

    rows = conn.execute(
        f"SELECT {DOCUMENT_COLUMNS} FROM ({union_sql})"
        " ORDER BY business_date DESC, order_type, id DESC LIMIT ?",
        (*params, max(limit, 1) + 1),
    ).fetchall()
    truncated = len(rows) > limit
    documents = [
        {
            "id": int(row["id"]),
            "order_no": row["order_no"],
            "order_type": row["order_type"],
            "customer_id": row["customer_id"],
            "customer_name": row["customer_name"],
            "business_date": row["business_date"],
            "status": row["status"],
            "amount_cents": int(row["total_amount_cents"] or 0),
            "time_label": _local_hm(row["created_at"]),
        }
        for row in rows[:limit]
    ]
    return {"documents": documents, "truncated": truncated}


def document_detail(order_type: str, document_id: int) -> dict | None:
    """单据详情：四类各自一张表；单价/数量/金额都是精确值（详情页才给精确金额）。"""
    order_type = str(order_type or "")
    if order_type not in ORDER_TYPE_ORDER:
        return None
    with connect_readonly() as conn:
        if order_type in {"sale", "return"}:
            header = conn.execute(
                "SELECT o.id, o.order_no, o.order_date AS business_date, o.order_type, o.status,"
                " o.total_amount_cents, o.notes,"
                " COALESCE(c.name, '（未知客户）') AS customer_name"
                "  FROM orders o LEFT JOIN customers c ON c.id = o.customer_id"
                " WHERE o.id=? AND o.order_type=? AND o.deleted_at IS NULL",
                (document_id, order_type),
            ).fetchone()
            if header is None:
                return None
            item_rows = conn.execute(
                "SELECT product_name, unit, quantity, unit_price_cents, subtotal_cents"
                "  FROM order_items WHERE order_id=? ORDER BY id",
                (document_id,),
            ).fetchall()
            items = [
                {
                    "name": row["product_name"],
                    "unit": row["unit"],
                    "quantity": str(row["quantity"] or ""),
                    "quantity_3dp": False,
                    "unit_price_cents": int(row["unit_price_cents"] or 0),
                    "subtotal_cents": int(row["subtotal_cents"] or 0),
                }
                for row in item_rows
            ]
        elif order_type == "purchase":
            header = conn.execute(
                "SELECT id, order_no, business_date, 'purchase' AS order_type, status,"
                " total_amount_cents, source_notes AS notes,"
                " COALESCE(customer_name, '（未知对象）') AS customer_name"
                "  FROM purchase_orders WHERE id=? AND deleted_at IS NULL",
                (document_id,),
            ).fetchone()
            if header is None:
                return None
            item_rows = conn.execute(
                "SELECT product_name, unit, quantity_3dp,"
                " unit_cost_cents AS unit_price_cents, subtotal_cents"
                "  FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id",
                (document_id,),
            ).fetchall()
            items = [
                {
                    "name": row["product_name"],
                    "unit": row["unit"],
                    "quantity": str(row["quantity_3dp"] or 0),
                    "quantity_3dp": True,
                    "unit_price_cents": int(row["unit_price_cents"] or 0),
                    "subtotal_cents": int(row["subtotal_cents"] or 0),
                }
                for row in item_rows
            ]
        else:
            header = conn.execute(
                "SELECT id, order_no, business_date, 'purchase_return' AS order_type, status,"
                " total_amount_cents, notes,"
                " COALESCE(customer_name, '（未知对象）') AS customer_name"
                "  FROM purchase_return_orders WHERE id=? AND deleted_at IS NULL",
                (document_id,),
            ).fetchone()
            if header is None:
                return None
            item_rows = conn.execute(
                "SELECT product_name, unit, quantity_3dp, unit_price_cents, subtotal_cents"
                "  FROM purchase_return_items WHERE purchase_return_id=? ORDER BY id",
                (document_id,),
            ).fetchall()
            items = [
                {
                    "name": row["product_name"],
                    "unit": row["unit"],
                    "quantity": str(row["quantity_3dp"] or 0),
                    "quantity_3dp": True,
                    "unit_price_cents": int(row["unit_price_cents"] or 0),
                    "subtotal_cents": int(row["subtotal_cents"] or 0),
                }
                for row in item_rows
            ]

    return {
        "id": int(header["id"]),
        "order_no": header["order_no"],
        "order_type": header["order_type"],
        "customer_name": header["customer_name"],
        "business_date": header["business_date"],
        "status": header["status"],
        "total_cents": int(header["total_amount_cents"] or 0),
        "notes": header["notes"] or "",
        "items": items,
    }


# ---------------------------------------------------------------------------
# 商品价
# ---------------------------------------------------------------------------

def product_prices(customer_id: int, q: str = "", *, limit: int = PRODUCT_LIST_LIMIT) -> dict:
    """商品价屏：该客户专属价 + 该客户最近一次成交价，一屏并排显示。

    - 专属价来源 `customer_prices`（售价唯一来源 = 开单学习出的客户专属价）。
    - 最近成交价 = 该客户最近一张有效销售/退货单里这个商品的单价。
    - 两者不一致时由模板给差额提示，**不静默取其一**。
    """
    q = (q or "").strip()
    conditions = ["p.deleted_at IS NULL"]
    params: list = []
    if q:
        conditions.append(name_match_clause("p."))
        params.extend(name_match_params(q))
    sql = f"""
        SELECT {PRODUCT_COLUMNS},
               cp.price_cents AS customer_price_cents,
               (SELECT oi.unit_price_cents
                  FROM order_items oi JOIN orders o ON o.id = oi.order_id
                 WHERE oi.product_id = p.id AND o.customer_id = ?
                   AND o.status IN {ORDER_STATUS_SQL} AND o.deleted_at IS NULL
                 ORDER BY o.order_date DESC, o.id DESC LIMIT 1) AS last_price_cents
          FROM products p
          LEFT JOIN customer_prices cp ON cp.product_id = p.id AND cp.customer_id = ?
         WHERE {' AND '.join(conditions)}
         ORDER BY CASE WHEN cp.price_cents IS NULL THEN 1 ELSE 0 END,
                  p.usage_count DESC, p.name
         LIMIT ?
    """
    with connect_readonly() as conn:
        customer = conn.execute(
            "SELECT id, name FROM customers WHERE id=? AND deleted_at IS NULL",
            (customer_id,),
        ).fetchone()
        rows = conn.execute(
            sql, (int(customer_id), int(customer_id), *params, max(limit, 1) + 1)
        ).fetchall()
    truncated = len(rows) > limit
    products = [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "unit": row["unit"],
            "customer_price_cents": (
                None if row["customer_price_cents"] is None else int(row["customer_price_cents"])
            ),
            "last_price_cents": (
                None if row["last_price_cents"] is None else int(row["last_price_cents"])
            ),
        }
        for row in rows[:limit]
    ]
    return {
        "customer": ({"id": int(customer["id"]), "name": customer["name"]} if customer else None),
        "products": products,
        "truncated": truncated,
        "query": q,
    }
