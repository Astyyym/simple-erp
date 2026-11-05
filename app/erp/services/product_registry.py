"""开单商品解析与自动建档的唯一入口。

背景（2026-11-05 用户口径）：
- 销售单 / 拿货单：手输的商品名若未建档，**自动建档**（`origin='order'`，写审计留痕）。
- 退货单 / 退拿货单：**只能选已建档商品**，未建档直接拒单，绝不偷偷建档案。

同一天的另一条要求：商品管理里已加过同名商品，拿货单不能还提示「未建档」。根因是
页面商品匹配只用**加载时的快照**；本模块按名称**实时查库**（含全/半角、空格差异的
容错匹配），让「刚加的商品」保存时必然命中，不做重复建档。

价格口径：拿货单价是**成本**，只在拿货/退拿货；它永远不能写进 `customer_prices`
（那是售价）。所以 `ensure_product_for_order` 默认 `learn_customer_price=False`，
调用方（销售）若要学价必须显式开启。

商品身份仍以 `TRIM(name)` 唯一索引为准；容错匹配只用于「查找已有商品」，不自动合并
近似同名档案（与 `docs/需求/拿货与对账业务契约.md` §2.1 一致）。
"""
from __future__ import annotations

from erp.utils.audit import log_action
from erp.utils.pinyin import normalize_name_for_store, normalize_name_query, pinyin_fields as build_pinyin_fields


def normalize_product_name(name: str) -> str:
    """商品身份 = 名称本身（v14 起型号已并进名称），只去首尾空白。"""
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("商品名称不能为空")
    return normalized


def product_name_key(name: str) -> str:
    """匹配用的归一键：NFKC（全角→半角）+ 去空格/拼音分隔符。

    与 `products.name_norm` 写入时的规则（`normalize_name_for_store`）是同一实现，
    所以「沟槽（开维喜）」与「沟槽(开维喜)」「沟槽 明杆」与「沟槽明杆」都能对上。
    """
    return normalize_name_query(normalize_product_name(name))


def find_product_row(conn, name: str):
    """按名称找商品档案（含软删除），找不到返回 None。

    先精确比较 `TRIM(name)`（与唯一索引同口径），不中再按 `name_norm` 容错比较。
    同一名称若有软删除与未删除两份，优先返回未删除的。只用于查找，不写库。
    """
    normalized = normalize_product_name(name)
    row = conn.execute(
        "SELECT * FROM products WHERE TRIM(name)=? ORDER BY (deleted_at IS NOT NULL), id LIMIT 1",
        (normalized,),
    ).fetchone()
    if row is not None:
        return row
    key = product_name_key(normalized)
    if not key:
        return None
    return conn.execute(
        "SELECT * FROM products WHERE name_norm=? ORDER BY (deleted_at IS NOT NULL), id LIMIT 1",
        (key,),
    ).fetchone()


def resolve_existing_product(conn, name: str):
    """只解析**已建档且未删除**的商品；找不到或已在回收站返回 None（退货类单据用）。"""
    row = find_product_row(conn, name)
    if row is None or row["deleted_at"] is not None:
        return None
    return row


def ensure_product_for_order(
    conn,
    name: str,
    unit: str,
    *,
    order_no: str = "",
    write_back_unit: bool = False,
    learn_customer_price: bool = False,
    customer_id: int | None = None,
    unit_price_cents: int | None = None,
) -> int:
    """按名称拿到商品 id；没有就自动建档（`origin='order'` + 拼音 + 审计留痕）。

    命中回收站同名档案时复用并恢复可见性（G-9），与销售侧行为一致。
    - `write_back_unit`：销售开单会顺手同步商品档案单位；拿货/退拿货**不**回写。
    - `learn_customer_price`：仅销售单价（售价）学成客户专属价；拿货成本价必须传 False。
    """
    normalized = normalize_product_name(name)
    unit_text = str(unit or "").strip()
    row = find_product_row(conn, normalized)

    if row is None:
        initials, full = build_pinyin_fields(normalized)
        cur = conn.execute(
            "INSERT INTO products(name, unit, pinyin_initials, pinyin_full, name_norm, usage_count, origin) "
            "VALUES (?, ?, ?, ?, ?, 1, 'order')",
            (normalized, unit_text, initials, full, normalize_name_for_store(normalized)),
        )
        product_id = int(cur.lastrowid)
        log_action(
            "auto_create_product",
            "product",
            product_id,
            f"开单自动建档商品：{normalized}，来源 {order_no}" if order_no else f"开单自动建档商品：{normalized}",
            conn=conn,
        )
    else:
        product_id = int(row["id"])
        if row["deleted_at"] is not None:
            # G-9：命中回收站档案 → 复用并恢复可见性，留痕便于用户察觉。
            conn.execute(
                "UPDATE products SET deleted_at=NULL, delete_reason='', usage_count=usage_count+1, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (product_id,),
            )
            log_action(
                "restore_product_on_order",
                "product",
                product_id,
                f"开单复用回收站商品档案并恢复可见性：{normalized}",
                conn=conn,
            )
        else:
            conn.execute(
                "UPDATE products SET usage_count=usage_count+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (product_id,),
            )
        if write_back_unit and unit_text:
            conn.execute("UPDATE products SET unit=? WHERE id=?", (unit_text, product_id))

    if learn_customer_price and customer_id is not None and unit_price_cents is not None and unit_price_cents > 0:
        conn.execute(
            """
            INSERT INTO customer_prices(customer_id, product_id, price_cents)
            VALUES (?, ?, ?)
            ON CONFLICT(customer_id, product_id) DO UPDATE SET price_cents=excluded.price_cents, updated_at=CURRENT_TIMESTAMP
            """,
            (customer_id, product_id, int(unit_price_cents)),
        )
    return product_id
