"""Phase 3 S6：三张列表页搜索框从原生 datalist 改为自有 popover 联想。

计划 §13.2 S6：`customers/products/orders` 列表搜索框接入 `order-entry-common.js`
的 `bindAutocomplete`；初始化脚本放在 `data-refresh-fragment` **之外**（`page-refresh.mjs`
明写被替换区块内的 `<script>` 不会重新执行，放错就是静默失效）。

本文件锁：三页均无 `<datalist>`、有同 id 的 popover 容器、引入了共享脚本，且脚本块在 fragment 之外。
"""
from __future__ import annotations

import re

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_product

PAGES = {
    "/customers/": "customer_name_suggestions",
    "/products/": "product_name_suggestions",
    "/orders/": "order_customer_suggestions",
}


def _seed():
    init_db()
    create_customer("列表页联想客户")
    create_product("列表页联想商品", "个", 1000)


def _html(path: str) -> str:
    return create_app().test_client().get(path).get_data(as_text=True)


def _fragment_span(html: str):
    """返回第一个 data-refresh-fragment 容器的 (开始, 结束) 下标。"""
    start = html.find("data-refresh-fragment")
    if start == -1:
        return -1, -1
    close = html.find("</section>", start)
    return start, close if close != -1 else len(html)


def test_list_pages_drop_datalist_for_shared_popover():
    _seed()
    for path, box_id in PAGES.items():
        html = _html(path)
        assert "<datalist" not in html, path
        assert f'id="{box_id}"' in html, path
        assert 'role="listbox"' in html, path
        assert "order-entry-common.js" in html, path


def test_list_page_init_script_is_outside_refresh_fragment():
    """bindAutocomplete 的调用点不得落在 data-refresh-fragment 容器内部。"""
    _seed()
    for path in PAGES:
        html = _html(path)
        start, close = _fragment_span(html)
        assert start != -1, path
        in_fragment = html[start:close]
        assert "bindAutocomplete" not in in_fragment, path
        assert "bindAutocomplete" in html, path


def test_orders_list_filter_no_longer_uses_datalist():
    _seed()
    html = _html("/orders/")
    assert 'id="customerFilterInput"' in html
    assert 'id="order_customer_suggestions"' in html
