"""G-0：档案来源与「待补全」判定的共享口径。

页面标记与列表筛选必须用同一个函数，否则「列表说缺、编辑页说全」这类漂移无法收敛。

口径（2026-10-09 定案；2026-10-14 第二轮扩范围，见 开发短计划/2026-10-14_关联带入替换与商品筛选排序与流水展开_执行计划.md §九）：
- **来源**：`order`（开单自动建档）与 `import`（批量导入）都算「顺手搬进来的半成品档案」，
  缺字段就该被提醒补全；`manual`（用户自己点「新增」填的表单）**不算** —— 缺什么是他自己的选择。
- **商品**缺字段：品牌为空，**或**默认价为 0（导入时默认价可留空，落库为 0）。
- **客户**缺字段：电话与地址**都**为空。

来源取值：`manual` / `import` / `order`；旧数据为空（NULL）按「未知」处理，不猜。
"""

from __future__ import annotations

ORIGIN_MANUAL = "manual"
ORIGIN_IMPORT = "import"
ORIGIN_ORDER = "order"

# 「待补全」只看这两个来源：开单攒出 + 批量导入。
COMPLETION_ORIGINS = (ORIGIN_ORDER, ORIGIN_IMPORT)

# SQL 侧必须与 COMPLETION_ORIGINS 一致（列表筛选、待补全计数、chip 计数三处共用）。
COMPLETION_ORIGINS_SQL = "('order', 'import')"

ORIGIN_LABELS = {
    ORIGIN_MANUAL: "手工新建",
    ORIGIN_IMPORT: "批量导入",
    ORIGIN_ORDER: "开单攒出",
}

UNKNOWN_ORIGIN_LABEL = "未知"

# 商品缺字段：机器可读键 → (长标签, 短标签)。
# · 长标签：列表徽标 / 「待补全」筛选图例 / 导入预览提醒用；
# · 短标签：编辑页页头 chip 用（「待补全 · 缺品牌」）；
# · key    ：编辑页按**具体字段**打 ⚠ 标记用。
# 判定只写在 `product_missing_fields` 一处；另两个函数从它的结论反查本表，
# 所以「缺哪个字段」永远只有一条规则，页面提示与筛选口径不会漂移。
PRODUCT_MISSING_LABELS = {
    "brand": ("品牌（用于区分同名同规格商品）", "品牌"),
    "default_price": ("默认价（用于开单计价）", "默认价"),
}


def origin_label(origin: str | None) -> str:
    """把来源列转成给人看的短标签；空值按「未知」处理，不猜来源。"""
    return ORIGIN_LABELS.get((origin or "").strip(), UNKNOWN_ORIGIN_LABEL)


def _text(value) -> str:
    return str(value or "").strip()


def customer_missing_fields(customer) -> list[str]:
    """客户档案缺哪些字段（人话），空列表 = 不缺。

    列表徽标 title、编辑页提示、导入预览提醒三处都调它，保证「说的」和「筛的」是同一套。
    """
    keys = customer.keys()
    origin = _text(customer["origin"] if "origin" in keys else None)
    if origin not in COMPLETION_ORIGINS:
        return []
    phone = _text(customer["phone"] if "phone" in keys else "")
    address = _text(customer["address"] if "address" in keys else "")
    if not phone and not address:
        return ["联系方式（电话或地址，便于后续对账联系）"]
    return []


def customer_needs_completion(customer) -> bool:
    """客户待补全：来源为开单攒出/批量导入，且电话与地址均为空。"""
    return bool(customer_missing_fields(customer))


def product_missing_fields(product) -> list[str]:
    """商品档案缺哪些字段（人话），空列表 = 不缺。

    - 品牌为空：用于区分同名同规格商品。
    - 默认价为 0：导入时默认价可留空（落库 0），开单按 0 计价会算错钱，必须提醒补。
      拿不到 `default_price_cents` 这一列时不判价（避免对部分字典误判）。
    """
    keys = product.keys()
    origin = _text(product["origin"] if "origin" in keys else None)
    if origin not in COMPLETION_ORIGINS:
        return []
    missing: list[str] = []
    if not _text(product["brand"] if "brand" in keys else ""):
        missing.append(PRODUCT_MISSING_LABELS["brand"][0])
    price = product["default_price_cents"] if "default_price_cents" in keys else None
    if price is not None and int(price) <= 0:
        missing.append(PRODUCT_MISSING_LABELS["default_price"][0])
    return missing


def product_missing_keys(product) -> list[str]:
    """同上口径的机器可读版本，供编辑页按**具体字段**打 ⚠ 标记。

    不另立判定：先把 `product_missing_fields` 的结论拿到，再按长标签反查键。
    """
    missing = set(product_missing_fields(product))
    return [key for key, (label, _short) in PRODUCT_MISSING_LABELS.items() if label in missing]


def product_missing_summary(product) -> str:
    """缺字段的短标签串（如「品牌」「品牌、默认价」），给编辑页页头 chip 用。"""
    missing = set(product_missing_fields(product))
    return "、".join(short for label, short in PRODUCT_MISSING_LABELS.values() if label in missing)


def product_needs_completion(product) -> bool:
    """商品待补全：来源为开单攒出/批量导入，且缺品牌或缺默认价。"""
    return bool(product_missing_fields(product))


def customer_completion_hint(customer) -> str:
    missing = customer_missing_fields(customer)
    return f"缺{missing[0]}" if missing else ""


def product_completion_hint(product) -> str:
    missing = product_missing_fields(product)
    return "缺" + "、".join(missing) if missing else ""
