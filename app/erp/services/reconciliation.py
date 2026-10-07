from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

from erp.db import get_db


def _parse_date(value: str, label: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{label}格式无效") from None


def _scope_rows(conn, customer_id: int, start_date: str, end_date: str, order_type_filter: str):
    conditions = [
        "o.customer_id=?",
        "o.deleted_at IS NULL",
        "o.status IN ('saved','printed')",
    ]
    params: list[Any] = [int(customer_id)]
    if start_date:
        conditions.append("o.order_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("o.order_date <= ?")
        params.append(end_date)
    if order_type_filter != "all":
        conditions.append("o.order_type=?")
        params.append(order_type_filter)
    return conn.execute(
        f"""
        SELECT o.id, o.order_no, o.order_date, o.order_type, o.status,
               o.version, o.total_amount_cents, o.updated_at
        FROM orders o
        WHERE {' AND '.join(conditions)}
        ORDER BY o.order_date ASC, o.id ASC
        """,
        params,
    ).fetchall()


def _fingerprint(rows) -> str:
    payload = [
        [
            int(row["id"]), row["order_no"], row["order_date"], row["order_type"],
            row["status"], int(row["version"] or 0), int(row["total_amount_cents"]), row["updated_at"],
        ]
        for row in rows
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scope_ids(rows) -> list[int]:
    return [int(row["id"]) for row in rows]


def _load_snapshot_row(conn, snapshot_id: int):
    row = conn.execute("SELECT * FROM reconciliation_snapshots WHERE id=?", (int(snapshot_id),)).fetchone()
    if row is None:
        raise ValueError("对账查询不存在或已失效")
    return row


TYPE_LABELS = {
    "sale": "销售", "customer_return": "客户退货",
    "purchase": "拿货", "purchase_return": "退拿货",
}
DIRECTIONS = {
    "sale": "卖给对方（+）", "customer_return": "收回商品（−）",
    "purchase": "从对方买入（−）", "purchase_return": "退给对方（+）",
}
_CHANGED = "查询结果已变化，请重新查询后打印"


def _documents(conn, customer_id, start_date, end_date, type_filter, scope_version):
    """Private evidence and explicit public projection, in stable source/line order."""
    sources = [
        ("order", "orders", "order_items", "order_id", "order_date", "o.version", "o.notes", "o.source_order_id"),
    ]
    if scope_version == 2:
        sources += [
            ("purchase", "purchase_orders", "purchase_order_items", "purchase_order_id", "business_date", "0", "o.source_notes", "NULL"),
            ("purchase_return", "purchase_return_orders", "purchase_return_items", "purchase_return_id", "business_date", "o.version", "o.notes", "o.source_order_id"),
        ]
    documents = []
    for namespace, table, items_table, owner, date_col, version_col, notes_col, source_col in sources:
        conditions = ["o.customer_id=?", "o.deleted_at IS NULL", "o.status IN ('saved','printed')" if namespace == "order" else "o.status='saved'"]
        params = [customer_id]
        for value, operator in ((start_date, ">="), (end_date, "<=")):
            if value:
                conditions.append(f"o.{date_col} {operator} ?")
                params.append(value)
        type_col = "o.order_type" if namespace == "order" else f"'{namespace}'"
        heads = conn.execute(f"""SELECT o.id, o.customer_id, o.order_no, o.{date_col} AS order_date,
            {type_col} AS order_type, o.status, {version_col} AS version, o.updated_at,
            o.total_amount_cents, {notes_col} AS notes, {source_col} AS source_order_id
            FROM {table} o WHERE {' AND '.join(conditions)} ORDER BY o.{date_col}, o.id""", params).fetchall()
        for head in heads:
            kind = "customer_return" if head["order_type"] == "return" else head["order_type"]
            if type_filter != "all" and kind != ("customer_return" if type_filter == "return" else type_filter):
                continue
            key = int(head["id"]) if scope_version == 1 else f"{namespace}:{head['id']}"
            quantity = "quantity" if namespace == "order" else "quantity_3dp"
            price = "unit_cost_cents" if namespace == "purchase" else "unit_price_cents"
            source_item = "NULL" if namespace == "purchase" else "source_item_id"
            lines = conn.execute(f"""SELECT id, product_id, {source_item} AS source_item_id,
                product_name, spec, unit, {quantity} AS quantity, {price} AS unit_price_cents,
                subtotal_cents FROM {items_table} WHERE {owner}=? ORDER BY id""", (head["id"],)).fetchall()
            sign = -1 if kind in {"customer_return", "purchase"} else 1
            public_items = []
            for line in lines:
                qty = line["quantity"] if namespace == "order" else format(Decimal(line["quantity"]) / 1000, 'f')
                public_items.append({"product_name": line["product_name"], "spec": line["spec"] or "",
                    "unit": line["unit"], "quantity": qty,
                    "unit_price_cents": int(line["unit_price_cents"]), "subtotal_cents": sign * int(line["subtotal_cents"])})
            # orders already store signed amounts; never reverse customer returns twice.
            total = int(head["total_amount_cents"]) * (-1 if namespace == "purchase" else 1)
            public_head = {"id": key, "order_no": head["order_no"], "order_date": head["order_date"],
                "order_type": kind, "type_label": TYPE_LABELS[kind], "direction": DIRECTIONS[kind],
                "total_amount_cents": total, "notes": head["notes"] or "" if namespace == "order" else ""}
            documents.append({"key": key, "order": public_head, "items": public_items,
                "evidence": {"head": dict(head), "items": [dict(line) for line in lines]},
                "source_rank": {"order": 0, "purchase": 1, "purchase_return": 2}[namespace], "source_id": head["id"]})
    documents.sort(key=lambda doc: (doc["order"]["order_date"], doc["source_rank"], doc["source_id"]))
    return documents


def _v2_hash(documents):
    encoded = json.dumps([[doc["key"], doc["evidence"]] for doc in documents], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_scope(conn, snapshot):
    version = int(snapshot["scope_version"])
    documents = _documents(conn, snapshot["customer_id"], snapshot["start_date"], snapshot["end_date"], snapshot["order_type_filter"], version)
    keys = [doc["key"] for doc in documents]
    if version == 1:
        # The original orders-only candidate hash is an immutable legacy contract.
        rows = _scope_rows(conn, snapshot["customer_id"], snapshot["start_date"], snapshot["end_date"], snapshot["order_type_filter"])
        fingerprint = _fingerprint(rows)
    else:
        fingerprint = _v2_hash(documents)
    if keys != json.loads(snapshot["scope_json"]) or fingerprint != snapshot["scope_hash"]:
        raise ValueError(_CHANGED)
    selected = json.loads(snapshot["selected_json"])
    if not set(selected).issubset(set(keys)):
        raise ValueError("只能选择当前客户的单据")
    return documents


def create_reconciliation_snapshot(customer_id: int, start_date: str = "", end_date: str = "", order_type_filter: str = "all", *, scope_version: int = 2) -> int:
    start_date = _parse_date(start_date, "开始日期")
    end_date = _parse_date(end_date, "结束日期")
    if start_date and end_date and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    allowed = {"all", "sale", "return"} if scope_version == 1 else {"all", "sale", "return", "customer_return", "purchase", "purchase_return"}
    if scope_version not in (1, 2) or order_type_filter not in allowed:
        raise ValueError("对账单据类型不合法")
    if scope_version == 2 and order_type_filter == "return":
        order_type_filter = "customer_return"
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT id FROM customers WHERE id=? AND deleted_at IS NULL", (int(customer_id),)).fetchone() is None:
            raise ValueError("客户不存在")
        documents = _documents(conn, int(customer_id), start_date, end_date, order_type_filter, scope_version)
        keys = [doc["key"] for doc in documents]
        fingerprint = _v2_hash(documents) if scope_version == 2 else _fingerprint(_scope_rows(conn, int(customer_id), start_date, end_date, order_type_filter))
        cursor = conn.execute("""INSERT INTO reconciliation_snapshots
            (customer_id, start_date, end_date, order_type_filter, scope_json, selected_json, scope_hash, scope_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (int(customer_id), start_date, end_date, order_type_filter,
            json.dumps(keys), json.dumps(keys), fingerprint, scope_version))
        return int(cursor.lastrowid)


def validate_reconciliation_snapshot(snapshot_id: int) -> str | None:
    with get_db() as conn:
        conn.execute("BEGIN")
        snapshot = _load_snapshot_row(conn, snapshot_id)
        try:
            _read_scope(conn, snapshot)
        except ValueError as exc:
            return str(exc)
    return None


def _selection_key(value, version):
    if version == 1:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value):
            return int(value)
    elif isinstance(value, str) and re.fullmatch(r'(order|purchase|purchase_return):[1-9][0-9]*', value):
        return value
    raise ValueError("单据来源键不合法；只能选择当前客户的单据")


def update_reconciliation_selection(snapshot_id: int, *, mode: str, selected_ids: list | None = None, excluded_ids: list | None = None) -> None:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = _load_snapshot_row(conn, snapshot_id)
        scope = json.loads(snapshot["scope_json"])
        selected_keys = {_selection_key(value, snapshot["scope_version"]) for value in (selected_ids or [])}
        excluded_keys = {_selection_key(value, snapshot["scope_version"]) for value in (excluded_ids or [])}
        if not (selected_keys | excluded_keys).issubset(set(scope)):
            raise ValueError("只能选择当前客户的单据")
        if mode not in {"all", "explicit"}:
            raise ValueError("选择模式不合法")
        _read_scope(conn, snapshot)
        keys = selected_keys if mode == "explicit" else excluded_keys
        selected = [key for key in scope if (key in keys if mode == "explicit" else key not in keys)]
        conn.execute("UPDATE reconciliation_snapshots SET selected_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(selected), snapshot_id))


def toggle_reconciliation_selection(snapshot_id: int, order_id: int | str, selected: bool) -> None:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = _load_snapshot_row(conn, snapshot_id)
        _read_scope(conn, snapshot)
        scope = json.loads(snapshot["scope_json"])
        key = _selection_key(order_id, snapshot["scope_version"])
        if key not in scope:
            raise ValueError("只能选择当前客户的单据")
        selected_ids = set(json.loads(snapshot["selected_json"]))
        if selected:
            selected_ids.add(key)
        else:
            selected_ids.discard(key)
        ordered = [value for value in scope if value in selected_ids]
        conn.execute("UPDATE reconciliation_snapshots SET selected_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(ordered), snapshot_id))


def _public_snapshot(conn, snapshot, documents):
    keys = json.loads(snapshot["scope_json"])
    selected = set(json.loads(snapshot["selected_json"]))
    selected_documents = [doc for doc in documents if doc["key"] in selected]
    totals = {kind: 0 for kind in TYPE_LABELS}
    for doc in selected_documents:
        totals[doc["order"]["order_type"]] += doc["order"]["total_amount_cents"]
    customer = conn.execute("SELECT id, name, phone, address FROM customers WHERE id=?", (snapshot["customer_id"],)).fetchone()
    params = []
    conditions = ["customer_id IS NULL", "deleted_at IS NULL", "status='saved'"]
    for value, operator in ((snapshot["start_date"], ">="), (snapshot["end_date"], "<=")):
        if value:
            conditions.append(f"business_date {operator} ?")
            params.append(value)
    unbound_count = conn.execute(f"SELECT COUNT(*) FROM purchase_orders WHERE {' AND '.join(conditions)}", params).fetchone()[0] if snapshot["scope_version"] == 2 else 0
    return {"id": snapshot["id"], "scope_version": snapshot["scope_version"],
        "customer": dict(customer) if customer else {"id": snapshot["customer_id"], "name": "", "phone": "", "address": ""},
        "customer_id": snapshot["customer_id"], "start_date": snapshot["start_date"], "end_date": snapshot["end_date"],
        "order_type_filter": snapshot["order_type_filter"], "scope_ids": keys,
        "selected_ids": [doc["key"] for doc in selected_documents], "selected_count": len(selected_documents),
        "scope_count": len(keys), "selected_total_cents": sum(totals.values()), "type_totals_cents": totals,
        "unbound_purchase_count": unbound_count, "rows": [doc["order"] for doc in documents]}


def get_reconciliation_snapshot(snapshot_id: int) -> dict[str, Any]:
    with get_db() as conn:
        conn.execute("BEGIN")
        snapshot = _load_snapshot_row(conn, snapshot_id)
        return _public_snapshot(conn, snapshot, _read_scope(conn, snapshot))


def get_reconciliation_print_context(snapshot_id: int) -> dict[str, Any]:
    """Validate ALL candidates and project selected documents in ONE read transaction."""
    with get_db() as conn:
        conn.execute("BEGIN")
        snapshot = _load_snapshot_row(conn, snapshot_id)
        documents = _read_scope(conn, snapshot)
        public = _public_snapshot(conn, snapshot, documents)
        if not public["selected_count"]:
            raise ValueError("未选择任何单据，不能打印对账单")
        selected = set(public["selected_ids"])
        # Do not forward private scope keys, fingerprints, or database rows to print.
        print_snapshot = {key: public[key] for key in ("customer", "start_date", "end_date", "selected_count", "scope_count", "selected_total_cents", "type_totals_cents")}
        print_snapshot["customer"] = {key: public["customer"][key] for key in ("name", "phone", "address")}
        return {"snapshot": print_snapshot, "documents": [{"order": {key: doc["order"][key] for key in ("order_no", "order_date", "order_type", "type_label", "direction", "total_amount_cents", "notes")}, "items": doc["items"]} for doc in documents if doc["key"] in selected]}


def get_reconciliation_orders(snapshot_id: int) -> list[dict[str, Any]]:
    """Compatibility public flattened business rows; prints use grouped documents."""
    context = get_reconciliation_print_context(snapshot_id)
    return [{**doc["order"], **item} for doc in context["documents"] for item in doc["items"]]


def get_reconciliation_order_detail(snapshot_id: int, order_id: int | str) -> dict[str, Any] | None:
    with get_db() as conn:
        conn.execute("BEGIN")
        snapshot = _load_snapshot_row(conn, snapshot_id)
        # The legacy numeric detail API is explicitly orders-only, never guessed.
        if type(order_id) is int and snapshot["scope_version"] == 2:
            order_id = f"order:{order_id}"
        key = _selection_key(order_id, snapshot["scope_version"])
        documents = _read_scope(conn, snapshot)
        for doc in documents:
            if doc["key"] == key:
                return {"order": doc["order"], "items": doc["items"]}
        return None