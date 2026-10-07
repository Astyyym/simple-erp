"""Pure inventory classification shared by read-only page projections."""
import importlib
import importlib.util

import pytest


@pytest.mark.parametrize(
    "enabled, quantity, safety, status, alert",
    [
        (None, None, 1000, "未启用", False),
        (0, 0, 0, "未启用", False),
        (False, 500, 1000, "未启用", False),
        (1, None, 1000, "未知", False),
        (1, 0, 0, "缺货", True),
        (True, 500, 1000, "库存告急", True),
        (1, 1000, 1000, "库存告急", True),
        (1, 1001, 1000, "正常", False),
    ],
)
def test_pure_inventory_projection_has_independent_enable_and_stock_axes(enabled, quantity, safety, status, alert):
    module_name = "erp.services.inventory_status"
    assert importlib.util.find_spec(module_name) is not None, "shared pure inventory projector is missing"
    project = importlib.import_module(module_name).project_inventory_status
    assert project(enabled=enabled, quantity_3dp=quantity, safety_stock_3dp=safety) == {
        "inventory_enabled": bool(enabled),
        "enable_status": "已启用" if enabled else "未启用",
        "inventory_status": status,
        "inventory_alert": alert,
    }
