"""动作标签统一：同一个动作在所有页面用同一个词。

2026-10-10 用户反馈「操作这里的标签不一致」：同一动作在不同页面写成
查看/回看、编辑/重编辑/编辑草稿、打印/查看PDF。定案为
**查看 / 编辑 / 打印**，一个动作一个词，所有页面一致。

不纳入统一的动作（语义不同，非同一动作）：
- 回收站「恢复」「确认删除」；
- 设置页「下载回看」（下载归档文件，不是打开单据）。
"""
import re

import pytest

from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows
from erp.services.inventory import create_purchase_order

# 动作列/操作区里不允许再出现的旧叫法。
RETIRED_WORDS = ("回看", "重编辑", "查看PDF", "编辑草稿")


def _client():
    init_db()
    return create_app().test_client()


def _seed_documents():
    """造出四类单据，让列表与详情页都能渲染出操作按钮。"""
    from erp.services.accounting import create_product, delete_order
    from erp.services.inventory import initialize_product

    app = create_app()
    client = app.test_client()
    customer_id = create_customer("标签统一客户")
    # 销售单用「未启用库存」的商品 → 无库存流水 → 可编辑，才能验证「编辑」按钮。
    plain_pid = create_product("标签统一无库存商品", "P1", "个", 3000)
    sale_id = create_order_from_typed_rows(
        customer_id, "MD202610101001",
        [{"product_id": plain_pid, "product_name": "标签统一无库存商品", "spec": "P1", "unit": "个",
          "quantity": "1", "unit_price_yuan": "30"}],
        status="saved", order_date="2026-10-10",
    )
    # 拿货单要求商品已启用库存。
    stock_pid = create_product("标签统一库存商品", "T1", "个", 3000)
    initialize_product(stock_pid, "10", "10", "2026-10-01", "系统上线期初", "label-unify-init", confirm_zero=False)
    purchase = create_purchase_order(
        "NH202610100001", "2026-10-10",
        [{"product_id": stock_pid, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="label-unify", status="saved",
    )
    # 回收站要有已删除记录，否则行内按钮渲染不出来。
    draft_id = create_order_from_typed_rows(
        customer_id, "MD202610101002",
        [{"product_id": plain_pid, "product_name": "标签统一无库存商品", "spec": "P1", "unit": "个",
          "quantity": "1", "unit_price_yuan": "10"}],
        status="draft", order_date="2026-10-10",
    )
    delete_order(draft_id, "标签统一测试")
    return client, customer_id, sale_id, purchase["order_id"]


@pytest.mark.parametrize(
    "path",
    ["/orders/", "/orders/new", "/purchases/new", "/purchases/return/new"],
)
def test_no_retired_action_words_on_document_pages(path):
    client, _, _, _ = _seed_documents()
    html = client.get(path).get_data(as_text=True)
    for word in RETIRED_WORDS:
        assert word not in html, f"{path} 仍出现旧动作文案「{word}」"


def test_order_list_uses_unified_action_vocabulary():
    client, _, sale_id, _ = _seed_documents()
    html = client.get("/orders/").get_data(as_text=True)
    row = re.search(r"<tr[^>]*>(?:(?!</tr>).)*MD202610101001(?:(?!</tr>).)*</tr>", html, re.S)
    assert row, "单据行未渲染"
    cells = row.group(0)
    # 查看 / 编辑 / 打印 三词齐备，且顺序固定。
    for label in ("查看", "编辑", "打印"):
        assert f">{label}<" in cells, f"单据管理操作列缺少「{label}」"
    assert cells.index(">查看<") < cells.index(">编辑<") < cells.index(">打印<")
    # 指向正确端点。
    assert f"/orders/{sale_id}" in cells and f"/orders/{sale_id}/edit" in cells


def test_order_detail_uses_unified_action_vocabulary():
    client, _, sale_id, _ = _seed_documents()
    html = client.get(f"/orders/{sale_id}").get_data(as_text=True)
    assert "单据查看：" in html
    assert ">编辑</a>" in html
    assert ">打印</a>" in html


def test_purchase_detail_uses_unified_action_vocabulary():
    client, _, _, purchase_id = _seed_documents()
    html = client.get(f"/purchases/{purchase_id}").get_data(as_text=True)
    # 正式拿货单：编辑入口不出现（经济字段锁定），打印按钮用统一词。
    assert ">打印</a>" in html
    assert "查看PDF" not in html
    assert "编辑草稿" not in html


def test_recycle_and_archive_actions_keep_their_own_wording():
    """回收站与归档下载是不同动作，不该被卷进「查看/编辑/打印」。"""
    client, _, _, _ = _seed_documents()
    recycle = client.get("/recycle/").get_data(as_text=True)
    assert ">恢复</button>" in recycle
    assert "确认删除" in recycle
    # 设置页的「下载回看」是下载归档文件，不是打开单据；保留原词，且不属于本轮统一范围。
    from pathlib import Path
    settings_src = Path("app/erp/templates/settings/index.html").read_text(encoding="utf-8")
    assert "下载回看" in settings_src
