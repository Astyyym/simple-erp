"""G-0：档案来源与「待补全」判定的共享口径。

页面标记与列表筛选必须用同一个函数，否则「列表说缺、编辑页说全」这类漂移无法收敛。

口径（2026-10-09 定案，见 开发短计划/2026-10-08_旧数据迁移承接与备份恢复_执行计划.md §二之三）：
- **商品**：只看「型号为空」。**未启用库存不进待补全**——零建档路径的定位本就是
  「只开销售单、不做库存」，该条件下「未启用」是正常状态而非缺陷；商品列表已有
  独立的「启用状态」列表达同一事实，重复表达只会让筛选器变噪声。
- **客户**：来源为开单自动建档（`origin='order'`）**且**电话与地址均为空。

来源取值：`manual` / `import` / `order`；旧数据为空（NULL）按「未知」处理，不猜。
"""

from __future__ import annotations

ORIGIN_MANUAL = "manual"
ORIGIN_IMPORT = "import"
ORIGIN_ORDER = "order"

ORIGIN_LABELS = {
    ORIGIN_MANUAL: "手工新建",
    ORIGIN_IMPORT: "批量导入",
    ORIGIN_ORDER: "开单攒出",
}

UNKNOWN_ORIGIN_LABEL = "未知"


def origin_label(origin: str | None) -> str:
    """把来源列转成给人看的短标签；空值按「未知」处理，不猜来源。"""
    return ORIGIN_LABELS.get((origin or "").strip(), UNKNOWN_ORIGIN_LABEL)


def product_needs_completion(product) -> bool:
    """商品待补全：仅「型号为空」。接受 sqlite3.Row 或映射。"""
    spec = product["spec"] if "spec" in product.keys() else None
    return not str(spec or "").strip()


def customer_needs_completion(customer) -> bool:
    """客户待补全：开单自动建档 且 电话与地址均为空。"""
    keys = customer.keys()
    origin = (customer["origin"] if "origin" in keys else None) or ""
    phone = str((customer["phone"] if "phone" in keys else "") or "").strip()
    address = str((customer["address"] if "address" in keys else "") or "").strip()
    return origin == ORIGIN_ORDER and not phone and not address


def product_completion_hint(product) -> str:
    """给编辑页/列表用的「缺什么」说明；完整时返回空串。"""
    if product_needs_completion(product):
        return "缺型号：请补全型号，避免与同名不同规格的商品混在一起"
    return ""


def customer_completion_hint(customer) -> str:
    if customer_needs_completion(customer):
        return "缺联系方式：请补全电话或地址，便于后续对账联系"
    return ""
