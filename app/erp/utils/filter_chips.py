"""共享筛选卡「已选条件」chip 的构造。

只服务单据管理 / 数据分析两页的展示层，不改变任何筛选口径：
- ``removal_url`` 基于当前请求参数构造「去掉某个条件」的 URL，保留其余筛选与展示态，
  并始终去掉 ``page``（改条件后回到第一页）。
- 时间范围不生成 chip：它是常驻的组合控件，不是可移除的附加条件。
- 「不勾选 = 全部」与「全选 = 全部」语义等价，两种情况都不生成 chip。
"""
from __future__ import annotations

from flask import request, url_for

ORDER_TYPE_LABELS = {
    "sale": "销售单",
    "return": "退货单",
    "purchase": "拿货单",
    "purchase_return": "退拿货单",
}
ORDER_TYPE_ORDER = ("sale", "return", "purchase", "purchase_return")
STATUS_LABELS = {"draft": "草稿", "saved": "正式保存", "printed": "已打印", "void": "已作废"}
STATUS_ORDER = ("draft", "saved", "printed", "void")


def removal_url(exclude_keys) -> str:
    """当前页 URL，去掉 exclude_keys 中的参数（并始终去掉 page）。"""
    excluded = set(exclude_keys)
    query: dict[str, object] = {}
    for key in request.args:
        if key in excluded or key == "page":
            continue
        values = request.args.getlist(key)
        query[key] = values[0] if len(values) == 1 else values
    return url_for(request.endpoint, **query)


def _labels(values, order, labels) -> str:
    chosen = set(values)
    return "、".join(labels[value] for value in order if value in chosen)


def _multi_chip(values, order, labels, *, caption: str, key: str):
    chosen = set(values)
    if not chosen or len(chosen) >= len(order):
        return None
    return {"key": key, "label": f"{caption}：{_labels(values, order, labels)}", "url": removal_url({key})}


def order_filter_chips(*, customer_q: str, order_types, order_statuses) -> list[dict]:
    """单据管理页的 chip：客户 / 订单类型 / 单据状态。"""
    chips: list[dict] = []
    if customer_q:
        chips.append({"key": "customer", "label": f"客户：{customer_q}", "url": removal_url({"customer"})})
    for chip in (
        _multi_chip(order_types, ORDER_TYPE_ORDER, ORDER_TYPE_LABELS, caption="类型", key="order_type"),
        _multi_chip(order_statuses, STATUS_ORDER, STATUS_LABELS, caption="状态", key="status"),
    ):
        if chip:
            chips.append(chip)
    return chips


def analytics_filter_chips(
    *,
    customer_id: str,
    customer_name: str,
    product_name: str,
    spec: str,
    document_types,
) -> list[dict]:
    """数据分析页的 chip：客户 / 商品 / 型号 / 订单类型。"""
    chips: list[dict] = []
    if customer_id:
        chips.append({
            "key": "customer_id",
            "label": f"客户：{customer_name or customer_id}",
            "url": removal_url({"customer_id"}),
        })
    if product_name:
        # 移除商品时同时清掉型号（型号是商品的从属条件）。
        chips.append({
            "key": "product_name",
            "label": f"商品：{product_name}",
            "url": removal_url({"product_name", "spec"}),
        })
    if spec and spec != "__unfilled__":
        chips.append({"key": "spec", "label": f"型号：{spec}", "url": removal_url({"spec"})})
    chip = _multi_chip(document_types, ORDER_TYPE_ORDER, ORDER_TYPE_LABELS, caption="类型", key="document_type")
    if chip:
        chips.append(chip)
    return chips
