"""单位候选池（Phase 4 / §4.4）。

候选池 = 默认三项（个/套/件） ∪ 四张表的 `DISTINCT unit`：
`products` / `order_items` / `purchase_order_items` / `purchase_return_items`。

**为什么必须含明细表**：单据上的单位是**本单快照**，可以不同于商品档案单位——
历史原因是「拿货路径的商品必然已启用库存、锁定规则禁止改档案单位」，
所以新单位只能从明细表汇总。（2026-10-16 库存系统删除后单位锁定规则已取消，
但「明细里出现过的单位也该出现在候选里」这个口径保留。）
**零迁移**：不新建表、不升 schema 版本。
"""
from __future__ import annotations

from erp.db import get_db

# 默认三项：始终出现在候选里，与四类开单页原来的硬编码一致。
DEFAULT_UNITS: tuple[str, ...] = ("个", "套", "件")

_UNIT_TABLES: tuple[str, ...] = (
    "products",
    "order_items",
    "purchase_order_items",
    "purchase_return_items",
)


def unit_options(conn=None) -> list[str]:
    """返回去重后的单位候选列表：**默认三项在前（保持声明顺序）**，其余按字典序。\

    只读查询；空/纯空白单位被忽略。不改动任何数据。
    """
    if conn is None:
        with get_db() as owned:
            return unit_options(owned)
    extra: set[str] = set()
    for table in _UNIT_TABLES:
        rows = conn.execute(
            f"SELECT DISTINCT unit FROM {table} WHERE COALESCE(TRIM(unit), '') <> ''"
        ).fetchall()
        extra.update(str(row[0]).strip() for row in rows)
    # 默认三项保持声明顺序在前，库内新增单位按字典序排在后面。
    tail = sorted(extra - set(DEFAULT_UNITS))
    # 去重（库内单位可能与默认三项重合）
    seen: set[str] = set()
    result: list[str] = []
    for unit in [*DEFAULT_UNITS, *tail]:
        if unit not in seen:
            seen.add(unit)
            result.append(unit)
    return result
