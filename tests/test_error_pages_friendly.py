"""Batch A：错误呈现与输入校验（2026-10-08 第二轮修复）。

覆盖审查证据 #1–3、#5、#12–15：
- 单据/拿货/退拿货/商品/客户不存在 → 中文错误页（不再英文 500 或裸文本）
- 输入校验：客户期初、商品价格、收款/调整金额
- 已删除单据三入口（详情/编辑/PDF）按「不存在」处理
- 导出/PDF 裸文本收口
- error_response JSON 双键契约（error + message）
"""
import json

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product
from erp.services.inventory import create_purchase_order, initialize_product
from erp.utils.errors import error_response


def _product(spec="A", price=2000):
    pid = create_product("错误页商品", spec, "个", price)
    initialize_product(pid, "10", "10", "2026-10-01", "系统上线期初", f"err-page-{spec}", confirm_zero=False)
    return pid


def _sale(customer_id, product_id, order_no, qty="1"):
    return create_order_from_typed_rows(
        customer_id, order_no,
        [{"product_id": product_id, "product_name": "错误页商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": qty}],
        status="saved", order_date="2026-10-07",
    )


def _is_error_page(response):
    html = response.get_data(as_text=True)
    return ("error-panel" in html) and ("sidebar" in html) and html.lstrip().lower().startswith("<!doctype")


# ---------------------------------------------------------------------------
# A1：单据 404 / 已删除单据三入口
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suffix", ["", "/edit", "/pdf"])
def test_missing_sale_order_returns_chinese_404_page(suffix):
    init_db()
    client = create_app().test_client()
    response = client.get(f"/orders/999999{suffix}")
    assert response.status_code == 404
    assert _is_error_page(response)
    assert "不存在" in response.get_data(as_text=True)


@pytest.mark.parametrize("suffix", ["", "/edit", "/pdf"])
def test_recycled_sale_order_is_treated_as_missing(suffix):
    """回收站中的单据：详情/编辑/PDF 三入口都按「不存在」处理，不再 200。"""
    init_db()
    product_id = _product("RECY-SALE")
    customer_id = create_customer("回收站单据客户")
    sale_id = _sale(customer_id, product_id, "MD202610070301")
    client = create_app().test_client()
    assert client.post(f"/orders/{sale_id}/delete").status_code == 302

    response = client.get(f"/orders/{sale_id}{suffix}")
    assert response.status_code == 404
    assert _is_error_page(response)


# ---------------------------------------------------------------------------
# A4：拿货侧 404
# ---------------------------------------------------------------------------

def test_missing_purchase_and_return_detail_and_edit_return_chinese_pages():
    init_db()
    client = create_app().test_client()
    for path in ("/purchases/999999", "/purchases/999999/edit",
                 "/purchases/return/999999", "/purchases/return/999999/edit"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert _is_error_page(response), path
        assert "不存在" in response.get_data(as_text=True)


def test_locked_formal_purchase_edit_is_friendly_page():
    init_db()
    product_id = _product("LOCK-PUR")
    customer_id = create_customer("拿货锁定客户")
    created = create_purchase_order(
        "NH202610010801", "2026-10-01",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="lock-pur", status="saved",
    )
    client = create_app().test_client()
    response = client.get(f"/purchases/{created['order_id']}/edit")
    assert response.status_code == 400
    assert _is_error_page(response)
    assert "已锁定" in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# A6：商品 / 客户编辑死路
# ---------------------------------------------------------------------------

def test_missing_product_edit_get_and_post_are_chinese_404():
    init_db()
    client = create_app().test_client()
    get_response = client.get("/products/999999/edit")
    assert get_response.status_code == 404 and _is_error_page(get_response)
    post_response = client.post("/products/999999/edit", data={
        "name": "不存在商品", "spec": "", "unit": "个", "default_price": "10", "safety_stock": "0",
    })
    assert post_response.status_code == 404 and _is_error_page(post_response)


def test_missing_customer_edit_get_and_post_are_chinese_404():
    init_db()
    client = create_app().test_client()
    get_response = client.get("/customers/999999/edit")
    assert get_response.status_code == 404 and _is_error_page(get_response)
    post_response = client.post("/customers/999999/edit", data={
        "name": "不存在客户", "phone": "", "address": "", "opening_balance": "0",
    })
    # 旧行为是静默 302「成功」；现在是明确的中文 404。
    assert post_response.status_code == 404 and _is_error_page(post_response)


# ---------------------------------------------------------------------------
# A2：输入校验
# ---------------------------------------------------------------------------

def test_invalid_customer_opening_balance_is_friendly_400():
    init_db()
    client = create_app().test_client()
    response = client.post("/customers/create", data={"name": "坏期初客户", "opening_balance": "abc"})
    assert response.status_code == 400
    assert _is_error_page(response)
    assert "期初余额" in response.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers WHERE name='坏期初客户'").fetchone()[0] == 0


def test_invalid_product_price_is_friendly_400():
    init_db()
    client = create_app().test_client()
    response = client.post("/products/create", data={
        "name": "坏价格商品", "spec": "", "unit": "个", "default_price": "abc", "safety_stock": "0",
    })
    assert response.status_code == 400
    assert "有效数字" in response.get_data(as_text=True)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products WHERE name='坏价格商品'").fetchone()[0] == 0


def test_negative_payment_is_friendly_400_not_500():
    init_db()
    customer_id = create_customer("负收款客户")
    client = create_app().test_client()
    response = client.post("/accounts/payment", data={"customer_id": str(customer_id), "amount": "-5", "method": "现金"})
    assert response.status_code == 400
    assert _is_error_page(response)
    assert "必须大于 0" in response.get_data(as_text=True)


def test_non_numeric_payment_is_friendly_400():
    init_db()
    customer_id = create_customer("非数字收款客户")
    client = create_app().test_client()
    response = client.post("/accounts/payment", data={"customer_id": str(customer_id), "amount": "abc"})
    assert response.status_code == 400
    assert "有效数字" in response.get_data(as_text=True)


def test_empty_adjustment_amount_is_friendly_400():
    init_db()
    customer_id = create_customer("空调调整客户")
    client = create_app().test_client()
    response = client.post("/accounts/adjustment", data={"customer_id": str(customer_id), "amount": "", "reason": "补记"})
    assert response.status_code == 400
    assert "有效数字" in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# A5：编辑 POST 裸文本收口 + JSON 契约
# ---------------------------------------------------------------------------

def test_error_response_json_carries_both_error_and_message_keys():
    """base.html 读 `error`，开单页 fetch 读 `message`；两者都必须有真实原因。"""
    import json as _json
    init_db()
    app = create_app()
    with app.test_request_context("/orders/create", method="POST", headers={"X-Requested-With": "fetch"}):
        response = error_response("真实原因")
        payload = _json.loads(response[0].get_data(as_text=True))
    assert payload["error"] == "真实原因"
    assert payload["message"] == "真实原因"


def test_void_order_without_reason_returns_friendly_page():
    init_db()
    product_id = _product("VOID-REASON")
    customer_id = create_customer("作废原因客户")
    sale_id = _sale(customer_id, product_id, "MD202610070302")
    client = create_app().test_client()
    response = client.post(f"/orders/{sale_id}/void", data={"reason": "   "})
    assert response.status_code == 400
    assert _is_error_page(response)
    assert "作废原因" in response.get_data(as_text=True)


def test_void_order_fetch_caller_gets_real_reason_json():
    init_db()
    product_id = _product("VOID-FETCH")
    customer_id = create_customer("作废fetch客户")
    sale_id = _sale(customer_id, product_id, "MD202610070303")
    client = create_app().test_client()
    response = client.post(f"/orders/{sale_id}/void", data={"reason": ""}, headers={"X-Requested-With": "fetch"})
    assert response.status_code == 400 and response.is_json
    payload = json.loads(response.get_data(as_text=True))
    assert "作废原因" in payload["error"] and "作废原因" in payload["message"]


# ---------------------------------------------------------------------------
# A7：导出 / PDF 裸文本收口
# ---------------------------------------------------------------------------

def test_orders_summary_pdf_without_customer_is_friendly_page():
    init_db()
    client = create_app().test_client()
    response = client.get("/orders/summary_pdf")
    assert response.status_code == 400
    assert _is_error_page(response)
    assert "没有可导出" in response.get_data(as_text=True)


def test_accounts_summary_and_ledger_pdf_without_customer_are_friendly():
    """未选客户属请求参数问题 → 400（不是 404）。"""
    init_db()
    client = create_app().test_client()
    for path in ("/accounts/summary_pdf", "/accounts/ledger_pdf"):
        response = client.get(path)
        assert response.status_code == 400, path
        assert _is_error_page(response), path
        assert "选择客户" in response.get_data(as_text=True)


def test_accounts_summary_pdf_with_missing_customer_is_404():
    """A7：客户不存在属资源不存在 → 404（与 ledger_pdf 一致）。"""
    init_db()
    client = create_app().test_client()
    response = client.get("/accounts/summary_pdf?customer_id=999999")
    assert response.status_code == 404
    assert _is_error_page(response)


def test_purchase_pdf_for_missing_order_is_404_friendly_page():
    """A7：单据记录不存在属资源不存在 → 404。"""
    init_db()
    client = create_app().test_client()
    for path in ("/purchases/999999/pdf", "/purchases/return/999999/pdf"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert _is_error_page(response), path


def test_reconciliation_pdf_for_missing_snapshot_is_404():
    """A7：对账快照不存在属资源不存在 → 404。"""
    init_db()
    client = create_app().test_client()
    response = client.get("/analytics/reconciliation/pdf/999999")
    assert response.status_code == 404
    assert _is_error_page(response)


# ---------------------------------------------------------------------------
# A3：全局 404 兜底
# ---------------------------------------------------------------------------

def test_unknown_url_returns_chinese_page_not_english_default():
    init_db()
    client = create_app().test_client()
    response = client.get("/definitely/not/a/route")
    assert response.status_code == 404
    assert _is_error_page(response)
    html = response.get_data(as_text=True)
    assert "页面不存在" in html
    assert "Not Found" not in html
