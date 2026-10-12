"""历史成本人工补录（销售单）。

**只补利润、不动库存**：本模块绝不触碰 `inventory_postings`、
`product_inventory_state`、`inventory_initializations`。补录只写
`order_items.unit_cost_micro / cost_total_micro / cost_source`，并留审计。

数据落库即自动生效——分析/利润服务已按 `cost_total_micro IS NULL` 计入
「未知成本」，补录后该行离开未覆盖范围，无需改任何聚合逻辑。

口径（见 `开发短计划/2026-10-11_历史成本补录_执行计划.md` §二之二）：
- 只限销售单（`order_type='sale'`）、正式态（`saved`/`printed`）；
- 逐行：真实销售时快照行不可覆盖；`cost_source='backfill'` 行可再修正；
- **已有有效退货的单拒绝再修正**（原行成本改了、退货累计成本不会重算，
  已全部退完的单会留下永久差额）；
- 原因必填并写审计，与补录写入同一事务。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from erp.db import get_db
from erp.utils.audit import log_action


_COST_SCALE = Decimal("1000000")
_QUANTITY_SCALE = Decimal("1000")
_ZERO = Decimal("0")

BACKFILL_SOURCE = "backfill"


def _cost_micro(value, label: str = "成本") -> int:
    """非负、有限、≤6 位小数的金额 → micro 整数（与期初成本同一口径）。"""
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}必须是有效数字") from None
    if not parsed.is_finite():
        raise ValueError(f"{label}必须是有限数字")
    if parsed < _ZERO:
        raise ValueError(f"{label}不能为负数")
    scaled = parsed * _COST_SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{label}最多保留6位小数")
    return int(scaled)


def _quantity_3dp(value) -> int:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("数量无效") from None
    if not parsed.is_finite() or parsed < _ZERO:
        raise ValueError("数量无效")
    scaled = parsed * _QUANTITY_SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError("数量最多保留3位小数")
    return int(scaled)


def _cost_total_micro(quantity_3dp: int, unit_cost_micro: int) -> int:
    return int(
        (Decimal(quantity_3dp) * Decimal(unit_cost_micro) / _QUANTITY_SCALE)
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _has_live_return(conn, order_id: int) -> bool:
    """该销售单是否已产生有效退货（非删除、正式态）。"""
    return conn.execute(
        """
        SELECT 1 FROM orders r
        WHERE r.source_order_id=? AND r.order_type='return'
          AND r.status IN ('saved', 'printed') AND r.deleted_at IS NULL
        LIMIT 1
        """,
        (order_id,),
    ).fetchone() is not None


def backfill_order_costs(order_id: int, entries: list[dict], reason: str) -> dict:
    """人工补录销售单历史成本。返回 `{"updated": n}`。

    `entries` 每项：`{"item_id": int, "unit_cost": "元"}` 或
    `{"item_id": int, "total_cost": "元"}`（二选一，单位成本优先）。
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("补录原因不能为空")

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE id=? AND deleted_at IS NULL", (order_id,)
        ).fetchone()
        if order is None:
            raise ValueError("单据不存在，可能已被移入回收站或从未存在")
        if order["order_type"] != "sale":
            raise ValueError("人工补录成本目前只支持销售单")
        if order["status"] == "void":
            raise ValueError("已作废单据不能补录成本")
        if order["status"] not in ("saved", "printed"):
            raise ValueError("草稿单不需要补录成本，直接重编辑即可")

        rows = conn.execute(
            "SELECT id, quantity, unit_cost_micro, cost_total_micro, cost_source, product_name, spec "
            "FROM order_items WHERE order_id=? ORDER BY id",
            (order_id,),
        ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        if not by_id:
            raise ValueError("该单据没有可补录的明细行")

        if not entries:
            raise ValueError("请至少填写一行成本")

        prepared = []
        summaries = []
        for entry in entries:
            try:
                item_id = int(entry.get("item_id"))
            except (TypeError, ValueError):
                raise ValueError("补录明细必须属于该单据") from None
            target = by_id.get(item_id)
            if target is None:
                raise ValueError("补录明细必须属于该单据")
            already_backfilled = (target["cost_source"] or "") == BACKFILL_SOURCE
            if target["cost_total_micro"] is not None and not already_backfilled:
                raise ValueError("该行已有销售时成本，不能补录")
            if already_backfilled and _has_live_return(conn, order_id):
                raise ValueError("该单已产生退货，不能再修正补录成本；如需更正请作废后重开")

            quantity_3dp = _quantity_3dp(target["quantity"])
            unit_raw = entry.get("unit_cost")
            total_raw = entry.get("total_cost")
            has_unit = str(unit_raw).strip() != "" if unit_raw is not None else False
            has_total = str(total_raw).strip() != "" if total_raw is not None else False
            if not has_unit and not has_total:
                raise ValueError("请填写单位成本或总成本")
            if has_unit:
                unit_cost_micro = _cost_micro(unit_raw, "单位成本")
                cost_total_micro = _cost_total_micro(quantity_3dp, unit_cost_micro)
            else:
                cost_total_micro = _cost_micro(total_raw, "总成本")
                unit_cost_micro = (
                    int(
                        (Decimal(cost_total_micro) / Decimal(quantity_3dp) * _QUANTITY_SCALE)
                        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    )
                    if quantity_3dp > 0
                    else 0
                )
            prepared.append((unit_cost_micro, cost_total_micro, item_id))
            label = target["product_name"] + (f" · {target['spec']}" if target["spec"] else "")
            summaries.append(f"{label} 单位成本 {unit_cost_micro / 1_000_000:.6f} 元")

        for unit_cost_micro, cost_total_micro, item_id in prepared:
            conn.execute(
                "UPDATE order_items SET unit_cost_micro=?, cost_total_micro=?, cost_source=? WHERE id=?",
                (unit_cost_micro, cost_total_micro, BACKFILL_SOURCE, item_id),
            )
        # 审计与补录写入同一事务，避免「补录成功、审计丢失」。
        log_action(
            "backfill_order_cost",
            "order",
            order_id,
            f"人工补录历史成本：{order['order_no']}；{'；'.join(summaries)}；原因：{reason}",
            conn=conn,
        )

    return {"updated": len(prepared)}
