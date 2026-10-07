"""Pure current-inventory classification; no database or monetary projection."""


def project_inventory_status(*, enabled, quantity_3dp, safety_stock_3dp) -> dict:
    inventory_enabled = bool(enabled)
    if not inventory_enabled:
        status = "未启用"
    elif quantity_3dp is None:
        status = "未知"
    elif quantity_3dp == 0:
        status = "缺货"
    elif quantity_3dp <= safety_stock_3dp:
        status = "库存告急"
    else:
        status = "正常"
    return {
        "inventory_enabled": inventory_enabled,
        "enable_status": "已启用" if inventory_enabled else "未启用",
        "inventory_status": status,
        "inventory_alert": status in {"缺货", "库存告急"},
    }
