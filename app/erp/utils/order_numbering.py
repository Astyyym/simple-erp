"""拿货/退拿货共用的 NH 单号取号。

销售/退货共用 `MD` + 8 位业务日期 + 4 位日流水；拿货/退拿货采用同一套规则的
`NH` 号池：两张表共用一个按业务日期的流水表，避免任一侧单独取号时撞号。

历史数据兼容：早期退拿货使用 `TN` 前缀，旧单号不重写；取号时会把同一天已有的
`TN` 流水一并计入最大值，保证新 `NH` 号不会与历史单号重复。
"""
from __future__ import annotations

import re

PURCHASE_PREFIX = "NH"
LEGACY_PURCHASE_RETURN_PREFIX = "TN"


def _daily_prefix(business_date: str, prefix: str) -> str:
    return f"{prefix}{business_date.replace('-', '')}"


def _max_matching_sequence(conn, table: str, prefix: str) -> int:
    """Return the highest 4-digit sequence already used with this prefix."""
    pattern = re.compile(rf"{re.escape(prefix)}(\d{{4}})$")
    rows = conn.execute(
        f"SELECT order_no FROM {table} WHERE order_no LIKE ?", (prefix + "%",)
    ).fetchall()
    highest = 0
    for row in rows:
        match = pattern.search(row["order_no"])
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def _highest_sequence(conn, business_date: str) -> int:
    """Highest NH sequence used on this day, including legacy TN numbers."""
    highest = max(
        _max_matching_sequence(conn, "purchase_orders", _daily_prefix(business_date, PURCHASE_PREFIX)),
        _max_matching_sequence(conn, "purchase_return_orders", _daily_prefix(business_date, PURCHASE_PREFIX)),
        _max_matching_sequence(conn, "purchase_return_orders", _daily_prefix(business_date, LEGACY_PURCHASE_RETURN_PREFIX)),
    )
    reserved = conn.execute(
        "SELECT last_sequence FROM purchase_number_sequences WHERE business_date=?",
        (business_date,),
    ).fetchone()
    if reserved:
        highest = max(highest, int(reserved["last_sequence"]))
    return highest


def next_nh_order_no(conn, business_date: str) -> str:
    """Allocate the next NH order number for the given business date.

    必须在调用方的写事务内执行（拿货与退拿货都在 ``BEGIN IMMEDIATE`` 下取号），
    否则两个并发单可能拿到同一号。
    """
    sequence = _highest_sequence(conn, business_date) + 1
    if sequence > 9999:
        raise ValueError("当天拿货/退拿货流水已达到9999，不能继续创建")
    conn.execute(
        "INSERT INTO purchase_number_sequences(business_date,last_sequence) VALUES (?,?) "
        "ON CONFLICT(business_date) DO UPDATE SET last_sequence=excluded.last_sequence",
        (business_date, sequence),
    )
    return f"{_daily_prefix(business_date, PURCHASE_PREFIX)}{sequence:04d}"


def peek_nh_order_no(conn, business_date: str) -> str:
    """Preview the next NH number without consuming it（开单页只读单号展示）。"""
    sequence = _highest_sequence(conn, business_date) + 1
    if sequence > 9999:
        raise ValueError("当天拿货/退拿货流水已达到9999，不能继续创建")
    return f"{_daily_prefix(business_date, PURCHASE_PREFIX)}{sequence:04d}"
