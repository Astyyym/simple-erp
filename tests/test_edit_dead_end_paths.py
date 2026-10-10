"""Batch B：编辑死路与恢复一致性（2026-10-08 第二轮修复；2026-10-10 改为禁用态）。

覆盖审查证据 #4/#5/#10/#16：
- 单据管理列表：已作废 / 已过账销售单的「编辑」渲染为**禁用按钮**（不再是隐藏），
  未过账的可编辑为真链接；两种状态按钮都存在，列表列宽一致
- 详情页按同一口径渲染链接或禁用按钮
- GET 编辑入口提前拦截（作废 / 过账 → 中文错误页）
- 编辑页（4 个开单页）含 beforeunload 未保存提醒
- 开单页当天历史面板：已过账销售单的「编辑」为禁用按钮，拿货/退拿货面板无编辑入口
"""
import re

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product, void_order
from erp.services.inventory import create_purchase_order, initialize_product


def _posted_product(spec, qty="10"):
    pid = create_product("编辑死路商品", spec, "个", 2000)
    initialize_product(pid, qty, "10", "2026-10-01", "系统上线期初", f"edit-dead-{spec}", confirm_zero=False)
    return pid


def _plain_product(spec):
    """未启用库存的商品：销售单不会产生库存流水 → 应保持可编辑。"""
    return create_product("无库存商品", spec, "个", 2000)


def _sale(customer_id, product_id, order_no):
    return create_order_from_typed_rows(
        customer_id, order_no,
        [{"product_id": product_id, "product_name": "编辑死路商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date="2026-10-07",
    )


def _row_html(html, order_no):
    """粗略截取包含该单号的表格行，用于按钮断言。"""
    match = re.search(r"<tr[^>]*>(?:(?!</tr>).)*" + re.escape(order_no) + r"(?:(?!</tr>).)*</tr>", html, re.S)
    return match.group(0) if match else ""


# ---------------------------------------------------------------------------
# B1：列表按钮
# ---------------------------------------------------------------------------

def test_listed_posted_sale_shows_disabled_edit_while_plain_sale_keeps_link():
    """不可编辑 → 禁用按钮（仍可见）；可编辑 → 真链接。两种都渲染出「编辑」。"""
    init_db()
    customer_id = create_customer("列表编辑客户")
    posted_pid = _posted_product("LIST-POSTED")
    plain_pid = _plain_product("LIST-PLAIN")
    posted_sale = _sale(customer_id, posted_pid, "MD202610070601")     # 有库存流水
    plain_sale = _sale(customer_id, plain_pid, "MD202610070602")       # 无库存流水（商品未启用库存）

    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    posted_row = _row_html(html, "MD202610070601")
    plain_row = _row_html(html, "MD202610070602")

    # 已过账：没有 edit 链接，但有禁用的「编辑」按钮 + 原因。
    assert f"/orders/{posted_sale}/edit" not in posted_row
    assert 'disabled' in posted_row and ">编辑<" in posted_row
    assert "库存已过账" in posted_row
    # 未过账：真链接。
    assert f"/orders/{plain_sale}/edit" in plain_row
    # 两种状态都渲染出「编辑」按钮 —— 操作列不再长短不一。
    assert ">编辑<" in posted_row and ">编辑<" in plain_row
    # 查看与打印都保留。
    assert "MD202610070601" in html


def test_voided_document_in_list_shows_disabled_edit_with_reason():
    init_db()
    customer_id = create_customer("作废列表客户")
    product_id = _plain_product("LIST-VOID")
    sale_id = _sale(customer_id, product_id, "MD202610070603")
    void_order(sale_id, "客户取消")  # 无库存流水也能直接作废? 仅正式保存可作废

    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    row = _row_html(html, "MD202610070603")
    assert row, "作废单仍应出现在列表"
    assert "已作废" in row
    assert f"/orders/{sale_id}/edit" not in row
    assert 'disabled' in row and ">编辑<" in row
    assert "已作废单据不能编辑" in row


def test_purchase_list_disables_edit_for_formal_but_links_for_draft():
    init_db()
    product_id = _posted_product("LIST-PUR")
    customer_id = create_customer("拿货列表客户")
    formal = create_purchase_order(
        "NH202610070601", "2026-10-07",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="list-pur-formal", status="saved",
    )
    draft = create_purchase_order(
        "NH202610070602", "2026-10-07",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="list-pur-draft", status="draft",
    )
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    formal_row = _row_html(html, "NH202610070601")
    assert f"/purchases/{formal['order_id']}/edit" not in formal_row
    assert 'disabled' in formal_row and ">编辑<" in formal_row
    assert f"/purchases/{draft['order_id']}/edit" in _row_html(html, "NH202610070602")


# ---------------------------------------------------------------------------
# B2：详情页按钮
# ---------------------------------------------------------------------------

def test_detail_page_disables_edit_for_posted_sale():
    init_db()
    customer_id = create_customer("详情编辑客户")
    product_id = _posted_product("DETAIL-POSTED")
    sale_id = _sale(customer_id, product_id, "MD202610070604")
    html = create_app().test_client().get(f"/orders/{sale_id}").get_data(as_text=True)
    assert f"/orders/{sale_id}/edit" not in html
    assert 'disabled' in html and ">编辑<" in html
    assert "库存已过账" in html
    assert f"/orders/{sale_id}/pdf" in html


def test_detail_page_keeps_edit_link_for_plain_sale():
    init_db()
    customer_id = create_customer("详情无流水客户")
    product_id = _plain_product("DETAIL-PLAIN")
    sale_id = _sale(customer_id, product_id, "MD202610070605")
    html = create_app().test_client().get(f"/orders/{sale_id}").get_data(as_text=True)
    assert f"/orders/{sale_id}/edit" in html


# ---------------------------------------------------------------------------
# B3：GET 编辑入口拦截
# ---------------------------------------------------------------------------

def test_get_edit_posted_sale_returns_friendly_page():
    init_db()
    customer_id = create_customer("拦截过账客户")
    product_id = _posted_product("BLOCK-POSTED")
    sale_id = _sale(customer_id, product_id, "MD202610070606")
    response = create_app().test_client().get(f"/orders/{sale_id}/edit")
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "error-panel" in html and "过账" in html


def test_get_edit_voided_sale_returns_friendly_page():
    init_db()
    customer_id = create_customer("拦截作废客户")
    product_id = _posted_product("BLOCK-VOID")
    sale_id = _sale(customer_id, product_id, "MD202610070607")
    void_order(sale_id, "客户取消")
    response = create_app().test_client().get(f"/orders/{sale_id}/edit")
    assert response.status_code == 400
    assert "作废" in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# B4：编辑页含 beforeunload
# ---------------------------------------------------------------------------

def test_edit_forms_include_beforeunload_guard():
    init_db()
    customer_id = create_customer("防丢客户")
    product_id = _posted_product("GUARD-PID", qty="50")
    client = create_app().test_client()

    # 可编辑的销售单（无流水商品）
    plain_pid = _plain_product("GUARD-PLAIN")
    sale_id = _sale(customer_id, plain_pid, "MD202610070608")
    sale_html = client.get(f"/orders/{sale_id}/edit").get_data(as_text=True)
    assert "beforeunload" in sale_html

    # 拿货草稿编辑页
    draft = create_purchase_order(
        "NH202610070608", "2026-10-07",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="guard-pur-draft", status="draft",
    )
    purchase_html = client.get(f"/purchases/{draft['order_id']}/edit").get_data(as_text=True)
    assert "beforeunload" in purchase_html


# ---------------------------------------------------------------------------
# B5：当天历史面板
# ---------------------------------------------------------------------------

def test_today_history_shows_disabled_edit_for_posted_sale_and_link_for_plain():
    """当天历史面板按 has_postings 渲染链接或禁用按钮（服务端渲染 + API 字段）。"""
    init_db()
    from datetime import date
    today = date.today().isoformat()
    customer_id = create_customer("当天历史客户")
    posted_pid = _posted_product("TODAY-POSTED")
    plain_pid = _plain_product("TODAY-PLAIN")
    posted = create_order_from_typed_rows(
        customer_id, "MD202610070701",
        [{"product_id": posted_pid, "product_name": "编辑死路商品", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date=today,
    )
    plain = create_order_from_typed_rows(
        customer_id, "MD202610070702",
        [{"product_id": plain_pid, "product_name": "无库存商品", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date=today,
    )
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert f"/orders/{posted}/edit" not in html
    assert f"/orders/{plain}/edit" in html
    # 不可编辑时仍是可见的禁用按钮（不再直接消失）。
    assert ">编辑<" in html and "disabled" in html

    payload = client.get("/orders/api/today_history").get_json()
    by_id = {row["id"]: row for row in payload["orders"]}
    assert by_id[posted]["editable"] is False
    assert by_id[plain]["editable"] is True
    # API 也带出禁用原因，供 JS 重建分支写进 title。
    assert "库存已过账" in by_id[posted]["edit_block_reason"]
    assert by_id[plain]["edit_block_reason"] == ""


def test_purchase_today_history_has_no_reedit_link():
    init_db()
    from datetime import date
    today = date.today().isoformat()
    product_id = _posted_product("PTODAY")
    customer_id = create_customer("拿货当天历史客户")
    created = create_purchase_order(
        "NH" + today.replace("-", "") + "0001", today,
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="purchase-today-history", status="saved",
    )
    html = create_app().test_client().get("/purchases/new").get_data(as_text=True)
    assert f"/purchases/{created['order_id']}/edit" not in html
    assert f"/purchases/{created['order_id']}" in html  # 回看链接仍在
    return_html = create_app().test_client().get("/purchases/return/new").get_data(as_text=True)
    assert f"/purchases/{created['order_id']}/edit" not in return_html
