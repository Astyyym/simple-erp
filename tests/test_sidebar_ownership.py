"""侧栏高亮归属：按路由 owner 判定，跨模块页面用来源参数覆盖。

覆盖 2026-10-10 反馈：
- 账款管理 → 「往来对账」后侧栏不再跳到「数据分析」；
- 同一页最多一项高亮（去掉旧的 `current` 半高亮）。
"""
import re

import pytest

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _active_labels(html: str) -> list[str]:
    """侧栏中带 active 类的条目文字。"""
    nav = re.search(r'<nav class="sidebar">.*?</nav>', html, re.S)
    assert nav, "侧栏未渲染"
    return [
        re.sub(r"<[^>]+>", "", body).strip()
        for cls, body in re.findall(r'<a class="([^"]*)"[^>]*>(.*?)</a>', nav.group(0), re.S)
        if "active" in cls.split()
    ]


def _nav_html(client, path: str) -> str:
    return re.search(
        r'<nav class="sidebar">.*?</nav>',
        client.get(path).get_data(as_text=True),
        re.S,
    ).group(0)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", "工作台"),
        ("/orders/", "单据管理"),
        ("/orders/new", "销售单"),
        ("/orders/return/new", "退货单"),
        ("/purchases/new", "拿货单"),
        ("/purchases/return/new", "退拿货单"),
        ("/accounts/", "账款管理"),
        ("/analytics/", "数据分析"),
        ("/products/", "商品管理"),
        ("/customers/", "客户管理"),
        ("/recycle/", "回收站"),
        ("/settings/", "系统设置"),
    ],
)
def test_sidebar_highlights_exactly_one_owner(path, expected):
    init_db()
    labels = _active_labels(_nav_html(create_app().test_client(), path))
    assert expected in labels, f"{path} 未高亮 {expected}，实际 {labels}"
    # 开单页允许父项「开单」与子项同时点亮；其余页必须只有一项。
    allowed = {"开单", "销售单", "退货单"} if path in ("/orders/new", "/orders/return/new") else {expected}
    assert set(labels) <= allowed, f"{path} 出现多余高亮：{labels}"


def test_reconciliation_from_accounts_keeps_accounts_highlighted():
    """从账款管理进入往来对账 → 侧栏仍归账款管理（与「返回账款管理」一致）。"""
    init_db()
    customer_id = create_customer("对账归属客户")
    create_order_from_typed_rows(
        customer_id, "MD202610101001",
        [{"product_name": "灭火器", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date="2026-10-10",
    )
    client = create_app().test_client()

    html = client.get(
        f"/analytics/reconciliation?customer_id={customer_id}&from=accounts"
    ).get_data(as_text=True)
    nav = re.search(r'<nav class="sidebar">.*?</nav>', html, re.S).group(0)
    assert 'href="/accounts/">账款管理</a>' in nav
    assert re.search(r'class="active" href="/accounts/">账款管理</a>', nav), nav
    assert not re.search(r'class="active" href="/analytics/">数据分析</a>', nav)
    # 返回按钮与侧栏归属一致。
    assert "返回账款管理" in html


def test_reconciliation_without_source_stays_in_analytics():
    init_db()
    customer_id = create_customer("对账默认归属客户")
    nav = _nav_html(
        create_app().test_client(),
        f"/analytics/reconciliation?customer_id={customer_id}",
    )
    assert re.search(r'class="active" href="/analytics/">数据分析</a>', nav), nav
    assert not re.search(r'class="active" href="/accounts/">账款管理</a>', nav)


def test_sidebar_has_no_ancestor_current_class():
    """旧的 `current` 半高亮已移除：同页不再出现第二项像选中的条目。"""
    init_db()
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    nav = re.search(r'<nav class="sidebar">.*?</nav>', html, re.S).group(0)
    classes = re.findall(r'class="([^"]*)"', nav)
    assert not [c for c in classes if "current" in c.split()], classes
    assert ".nav-section.current" not in html
