"""侧栏导航归属：由路由 endpoint 决定高亮项，而不是 URL 路径前缀。

背景（2026-10-10 用户反馈）：往来对账的真实路由是 `/analytics/reconciliation`，
但它由账款管理页进入。原来的 `request.path.startswith('/analytics')` 必然把高亮
打在「数据分析」上，而返回按钮写着「返回账款管理」——两处打架。

路径前缀法匹配的是「服务命名空间」，不是「用户所在模块」；每新增一个跨模块页面
就会再错一次。这里改为 endpoint → owner 映射，并允许显式来源参数覆盖。

调用方：`app/erp/__init__.py` 的 context processor 注入模板变量 `nav`。

注意：本模块被 context processor 调用，而 context processor 也会在**没有请求上下文**
时被触发（例如测试里直接 `render_template` 渲染打印模板）。所以 `nav_state()` 内部
必须自己容错，不能假定 `request` 可用；默认值取「无高亮」。
"""
from __future__ import annotations

from flask import has_request_context, request

# owner 取值：workbench / sale_entry / return_entry / purchase_entry /
# purchase_return_entry / documents / accounts / analytics / products /
# customers / recycle / settings
_OWNER_BY_ENDPOINT: dict[str, str] = {
    "index": "workbench",
    # 单据管理：销售/退货列表、详情、编辑、PDF 与导出
    "orders.list_orders": "documents",
    "orders.view_order": "documents",
    "orders.edit_order": "documents",
    "orders.order_pdf": "documents",
    "orders.orders_summary_pdf": "documents",
    "orders.export_sales_orders_excel": "documents",
    "orders.export_return_orders_excel": "documents",
    # 开单：销售 / 退货 / 拿货 / 退拿货
    "orders.new_order": "sale_entry",
    "orders.new_return_order": "return_entry",
    "purchases.new_purchase": "purchase_entry",
    "purchases.purchase_detail": "purchase_entry",
    "purchases.edit_purchase": "purchase_entry",
    "purchases.purchase_pdf": "purchase_entry",
    "purchases.new_return": "purchase_return_entry",
    "purchases.return_detail": "purchase_return_entry",
    "purchases.edit_return": "purchase_return_entry",
    "purchases.purchase_return_pdf": "purchase_return_entry",
    # 其余一级模块
    "accounts.accounts": "accounts",
    "analytics.analytics_center": "analytics",
    "analytics.reconciliation": "analytics",
    "products.list_products": "products",
    "products.edit_product": "products",
    "customers.list_customers": "customers",
    "customers.edit_customer": "customers",
    "recycle.recycle_bin": "recycle",
    "settings.settings_page": "settings",
}

# 跨模块页面：endpoint → {来源参数值: 覆盖后的 owner}
_SOURCE_OVERRIDES: dict[str, dict[str, str]] = {
    # 从账款管理点「往来对账」进入 → 侧栏仍归账款管理（与「返回账款管理」按钮一致）。
    "analytics.reconciliation": {"accounts": "accounts"},
}

_NAV_KEYS = (
    "workbench",
    "order_entry",
    "sale_entry",
    "return_entry",
    "purchase_entry",
    "purchase_return_entry",
    "documents",
    "accounts",
    "analytics",
    "products",
    "customers",
    "recycle",
    "settings",
)


def nav_owner(endpoint: str | None, source: str | None = None) -> str | None:
    """当前页所属的侧栏条目；未登记的路由返回 None（不高亮任何一项）。"""
    owner = _OWNER_BY_ENDPOINT.get(endpoint or "")
    if source:
        override = _SOURCE_OVERRIDES.get(endpoint or "", {}).get(source)
        if override:
            owner = override
    return owner


def nav_state(endpoint: str | None = None, source: str | None = None) -> dict[str, bool]:
    """模板用的高亮布尔表。同一页最多一项为 True。

    可在无请求上下文时调用（打印模板渲染）：此时取当前 request 的值，取不到就
    返回「全部不高亮」。
    """
    if endpoint is None and source is None and has_request_context():
        endpoint = request.endpoint
        source = request.args.get("from")
    owner = nav_owner(endpoint, source)
    state = {key: False for key in _NAV_KEYS}
    if owner in state:
        state[owner] = True
    # 「开单」是父条目：销售/退货开单页同时点亮父与对应子项（沿用既有观感；
    # 拿货/退拿货历史上只点子项，不点父，保持原样）。
    if owner in {"sale_entry", "return_entry"}:
        state["order_entry"] = True
    return state
