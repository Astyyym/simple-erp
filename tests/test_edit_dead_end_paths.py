"""编辑入口一致性（2026-10-08 第二轮修复；2026-10-16 决策 ④ 全面放开编辑锁）。

决策 ④（2026-10-16 用户拍板）：**除「已作废」外所有单据都可重编辑** —— 已过账锁、
补录成本锁、正式拿货/退拿货锁全部撤除（库存与成本系统已整体删除）。本文件守住放开后的口径：

- 单据管理列表：非作废单（含正式销售单、正式拿货单）渲染真「编辑」链接；
  已作废单仍渲染**禁用按钮 + 原因**，按钮不消失、列表列宽一致
- 详情页按同一口径渲染链接或禁用按钮
- GET 编辑入口：正式单可直接进表单；只有已作废单返回中文错误页
- 编辑页（各开单页）含 beforeunload 未保存提醒
- 开单页当天历史面板：销售单与拿货/退拿货单一律同时提供「查看」+「编辑」两个入口
"""
import re

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product, void_order
from erp.services.purchases import create_purchase_order


def _product(spec):
    """建一个商品档案（库存系统删除后，商品不再分「已启用库存 / 未启用」）。"""
    return create_product("编辑死路商品" + spec, "个")


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

def test_listed_saved_sales_all_keep_edit_link():
    """决策 ④：正式销售单不再有「已过账」禁用态，列表里两张都渲染真「编辑」链接。"""
    init_db()
    customer_id = create_customer("列表编辑客户")
    first_pid = _product("LIST-FIRST")
    second_pid = _product("LIST-SECOND")
    first_sale = _sale(customer_id, first_pid, "MD202610070601")
    second_sale = _sale(customer_id, second_pid, "MD202610070602")

    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    first_row = _row_html(html, "MD202610070601")
    second_row = _row_html(html, "MD202610070602")

    assert f"/orders/{first_sale}/edit" in first_row
    assert f"/orders/{second_sale}/edit" in second_row
    # 两张都渲染出「编辑」链接 —— 操作列不再长短不一。
    assert ">编辑<" in first_row and ">编辑<" in second_row
    # 已过账锁已撤：列表里不该再出现旧锁文案。
    assert "库存已过账" not in html
    # 查看与打印都保留。
    assert "MD202610070601" in html


def test_voided_document_in_list_shows_disabled_edit_with_reason():
    init_db()
    customer_id = create_customer("作废列表客户")
    product_id = _product("LIST-VOID")
    sale_id = _sale(customer_id, product_id, "MD202610070603")
    void_order(sale_id, "客户取消")  # 正式保存的单据才可作废；已作废单仍留在列表（禁用编辑）

    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    row = _row_html(html, "MD202610070603")
    assert row, "作废单仍应出现在列表"
    assert "已作废" in row
    assert f"/orders/{sale_id}/edit" not in row
    assert 'disabled' in row and ">编辑<" in row
    assert "已作废单据不能编辑" in row


def test_purchase_list_links_edit_for_formal_and_draft():
    """决策 ④：正式拿货单也恢复可编辑 → 列表里正式单与草稿都渲染真「编辑」链接。"""
    init_db()
    product_id = _product("LIST-PUR")
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
    assert f"/purchases/{formal['order_id']}/edit" in formal_row
    assert ">编辑<" in formal_row
    assert f"/purchases/{draft['order_id']}/edit" in _row_html(html, "NH202610070602")


# ---------------------------------------------------------------------------
# B2：详情页按钮
# ---------------------------------------------------------------------------

def test_detail_page_disables_edit_for_voided_sale_only():
    """详情页的禁用态现在只由「已作废」触发；原因文案与列表口径一致。"""
    init_db()
    customer_id = create_customer("详情编辑客户")
    product_id = _product("DETAIL-VOID")
    sale_id = _sale(customer_id, product_id, "MD202610070604")
    void_order(sale_id, "客户取消")
    html = create_app().test_client().get(f"/orders/{sale_id}").get_data(as_text=True)
    assert f"/orders/{sale_id}/edit" not in html
    assert 'disabled' in html and ">编辑<" in html
    assert "已作废单据不能编辑" in html
    assert f"/orders/{sale_id}/pdf" in html


def test_detail_page_keeps_edit_link_for_saved_sale():
    """决策 ④：正式保存的销售单在详情页也保留真「编辑」链接（不作废即可编辑）。"""
    init_db()
    customer_id = create_customer("详情编辑链接客户")
    product_id = _product("DETAIL-SAVED")
    sale_id = _sale(customer_id, product_id, "MD202610070605")
    html = create_app().test_client().get(f"/orders/{sale_id}").get_data(as_text=True)
    assert f"/orders/{sale_id}/edit" in html
    assert "库存已过账" not in html


# ---------------------------------------------------------------------------
# B3：GET 编辑入口拦截
# ---------------------------------------------------------------------------

def test_get_edit_saved_sale_opens_the_form():
    """决策 ④：正式单不再被 GET 编辑入口拦截 —— 直接进表单，且带未保存提醒。"""
    init_db()
    customer_id = create_customer("放行编辑客户")
    product_id = _product("BLOCK-SAVED")
    sale_id = _sale(customer_id, product_id, "MD202610070606")
    response = create_app().test_client().get(f"/orders/{sale_id}/edit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "beforeunload" in html
    assert "库存已过账" not in html


def test_get_edit_voided_sale_returns_friendly_page():
    init_db()
    customer_id = create_customer("拦截作废客户")
    product_id = _product("BLOCK-VOID")
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
    product_id = _product("GUARD-PID")
    client = create_app().test_client()

    # 可编辑的销售单（无流水商品）
    plain_pid = _product("GUARD-PLAIN")
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

def test_today_history_offers_edit_for_every_saved_order():
    """决策 ④：当天历史面板对所有正式单都渲染真「编辑」入口（服务端渲染 + API 字段）。"""
    init_db()
    from datetime import date
    today = date.today().isoformat()
    customer_id = create_customer("当天历史客户")
    first_pid = _product("TODAY-FIRST")
    second_pid = _product("TODAY-SECOND")
    first = create_order_from_typed_rows(
        customer_id, "MD202610070701",
        [{"product_id": first_pid, "product_name": "编辑死路商品", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date=today,
    )
    second = create_order_from_typed_rows(
        customer_id, "MD202610070702",
        [{"product_id": second_pid, "product_name": "编辑死路商品", "unit": "个", "unit_price_yuan": "30", "quantity": "1"}],
        status="saved", order_date=today,
    )
    client = create_app().test_client()
    html = client.get("/orders/new").get_data(as_text=True)
    assert f"/orders/{first}/edit" in html
    assert f"/orders/{second}/edit" in html
    # 每行都同时给「查看」与「编辑」两个入口。
    first_row = _row_html(html, f"/orders/{first}/edit")
    assert ">查看<" in first_row and ">编辑<" in first_row

    payload = client.get("/orders/api/today_history").get_json()
    by_id = {row["id"]: row for row in payload["orders"]}
    assert by_id[first]["editable"] is True
    assert by_id[second]["editable"] is True
    # 可编辑时原因为空串（字段保留，供作废等未来状态使用）。
    assert by_id[first]["edit_block_reason"] == ""
    assert by_id[second]["edit_block_reason"] == ""


def test_purchase_today_history_offers_edit_for_saved_order():
    """决策 ④（反转旧断言）：正式拿货单在当天历史里同时有「查看」与「编辑」两个入口。

    旧用例断言拿货页当天历史**不出现** `/purchases/<id>/edit`（那是正式拿货单编辑锁的直接编码）；
    2026-10-16 用户拍板「正式拿货/退拿货单也恢复可编辑」，因此这里反向断言编辑入口存在。
    """
    init_db()
    from datetime import date
    today = date.today().isoformat()
    product_id = _product("PTODAY")
    customer_id = create_customer("拿货当天历史客户")
    created = create_purchase_order(
        "NH" + today.replace("-", "") + "0001", today,
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="purchase-today-history", status="saved",
    )
    order_id = created["order_id"]
    html = create_app().test_client().get("/purchases/new").get_data(as_text=True)
    assert f"/purchases/{order_id}/edit" in html, "正式拿货单在当天历史里要有编辑入口"
    assert f"/purchases/{order_id}" in html  # 回看链接仍在
    assert ">查看<" in html and ">编辑<" in html

    return_html = create_app().test_client().get("/purchases/return/new").get_data(as_text=True)
    assert f"/purchases/{order_id}/edit" in return_html
