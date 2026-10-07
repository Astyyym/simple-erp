"""统一错误呈现 + 删除不再被有效退货阻止的行为回归（2026-10-07 用户决策）。"""
import json

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
    customer_balance_cents,
)
from erp.services.inventory import create_purchase_order, initialize_product


def _product(spec="ERR"):
    product_id = create_product("错误呈现商品", spec, "个", 2000)
    initialize_product(product_id, "10", "10", "2026-10-01", "系统上线期初", f"err-init-{spec}", confirm_zero=False)
    return product_id


def _sale(customer_id, product_id, order_no, qty="1", date="2026-10-07"):
    return create_order_from_typed_rows(
        customer_id,
        order_no,
        [{"product_id": product_id, "product_name": "错误呈现商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": qty}],
        status="saved",
        order_date=date,
    )


def test_sale_with_live_return_can_be_deleted_and_return_keeps_working():
    """2026-10-07 用户决策：有有效退货指向的销售单也能删。

    退货单本身继续有效（库存与金额照常），只是失去「原销售单」追溯指向。
    """
    init_db()
    product_id = _product(spec="RET-DEL")
    customer_id = create_customer("退货引用客户")
    sale_id = _sale(customer_id, product_id, "MD202610070701", qty="3")
    return_id = create_order_from_typed_rows(
        customer_id,
        "MD202610070702",
        [{"product_id": product_id, "product_name": "错误呈现商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-10-07",
        order_type="return",
    )
    # 让退货单指向那张销售单。
    with get_db() as conn:
        conn.execute("UPDATE orders SET source_order_id=? WHERE id=?", (sale_id, return_id))

    balance_before = customer_balance_cents(customer_id)
    client = create_app().test_client()
    response = client.post(f"/orders/{sale_id}/delete")
    assert response.status_code == 302

    with get_db() as conn:
        sale = conn.execute("SELECT status, deleted_at FROM orders WHERE id=?", (sale_id,)).fetchone()
        ret = conn.execute("SELECT status, deleted_at, source_order_id FROM orders WHERE id=?", (return_id,)).fetchone()
    assert sale["status"] == "void" and sale["deleted_at"] is not None
    # 退货单本身不受影响，仍指向原 id（只是那个 id 已删除）。
    assert ret["status"] == "saved" and ret["deleted_at"] is None and ret["source_order_id"] == sale_id
    # 销售 +90 被冲回（退货 −30 仍在）→ 余额比删除前少 90 元。
    assert customer_balance_cents(customer_id) == balance_before - 9000

    # 退货详情页不再输出指向已删单的死链接。
    detail = client.get(f"/orders/{return_id}").get_data(as_text=True)
    assert "原单已删除" in detail
    assert f'href="/orders/{sale_id}"' not in detail


def test_row_delete_failure_returns_json_reason_for_fetch_callers():
    """行内删除失败时，fetch 调用方必须拿到真实原因（旧版固定弹「删除失败，请刷新页面后重试」）。"""
    init_db()
    product_id = _product(spec="ERR-JSON")
    customer_id = create_customer("错误呈现客户")
    # 制造一个必然失败的场景：已删除的单据再删一次。
    sale_id = _sale(customer_id, product_id, "MD202610070801")
    client = create_app().test_client()
    assert client.post(f"/orders/{sale_id}/delete").status_code == 302
    again = client.post(f"/orders/{sale_id}/delete", headers={"X-Requested-With": "fetch"})
    assert again.status_code == 400
    assert again.headers["Content-Type"].startswith("application/json")
    payload = json.loads(again.get_data(as_text=True))
    assert payload["error"] and "不存在" in payload["error"]


def test_browser_navigation_failure_renders_error_page_not_bare_text():
    """浏览器直接提交失败时，得到的是带侧栏的错误页，而不是一行裸文本。"""
    init_db()
    product_id = _product(spec="ERR-PAGE")
    customer_id = create_customer("错误页面客户")
    sale_id = _sale(customer_id, product_id, "MD202610070802")
    client = create_app().test_client()
    assert client.post(f"/orders/{sale_id}/delete").status_code == 302
    page = client.post(f"/orders/{sale_id}/delete")
    assert page.status_code == 400
    html = page.get_data(as_text=True)
    assert html.lstrip().startswith("<!doctype html>") or html.lstrip().startswith("<!DOCTYPE")
    assert "error-panel" in html
    # 标题按场景具体化，而不是笼统的「操作未完成」。
    assert "单据未能删除" in html
    # 侧栏仍在 → 用户没被踢出应用。
    assert "sidebar" in html and "回收站" in html
    # 真实原因可见。
    assert "不存在" in html


def test_recycle_purge_failure_renders_error_page_for_navigation():
    """回收站永久删除失败时同样给页面而非裸文本。

    这里用「有未删除单据还在使用该商品」作为必然失败场景（2026-10-07 起库存历史本身不再阻止删除）。
    """
    init_db()
    product_id = _product(spec="ERR-PURGE")
    customer_id = create_customer("回收站错误客户")
    _sale(customer_id, product_id, "MD202610070803")  # 活单据 → 商品不能删
    client = create_app().test_client()
    client.post(f"/products/{product_id}/delete")
    page = client.post(f"/recycle/product/{product_id}/purge")
    assert page.status_code == 400
    html = page.get_data(as_text=True)
    assert "error-panel" in html and "未删除的单据还在使用" in html


def test_customer_purge_with_history_reports_failure_instead_of_silent_noop():
    """客户 purge 的 guard 不满足时曾静默无效果（302 + 无变化），现在必须报出原因。"""
    init_db()
    customer_id = create_customer("有账务的客户")
    product_id = _product(spec="ERR-CUST")
    _sale(customer_id, product_id, "MD202610070804")
    client = create_app().test_client()
    client.post(f"/customers/{customer_id}/delete")

    page = client.post(f"/recycle/customer/{customer_id}/purge")
    assert page.status_code == 400
    assert "只能保留" in page.get_data(as_text=True)
    # 客户仍在（没有被误删），且仍在回收站。
    with get_db() as conn:
        row = conn.execute("SELECT deleted_at FROM customers WHERE id=?", (customer_id,)).fetchone()
    assert row is not None and row["deleted_at"] is not None

    # 批量路径同样报错，不再静默成功。
    bulk = client.post("/recycle/bulk/customer/purge", data={"ids": str(customer_id)})
    assert bulk.status_code == 400
    assert "只能保留" in bulk.get_data(as_text=True)


def test_customer_purge_without_history_still_succeeds():
    """没有账务的干净客户仍然可以真的永久删除（不能因为加了报错就把正常路径弄坏）。"""
    init_db()
    customer_id = create_customer("干净客户")
    client = create_app().test_client()
    client.post(f"/customers/{customer_id}/delete")
    assert client.post(f"/recycle/customer/{customer_id}/purge").status_code == 302
    with get_db() as conn:
        assert conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone() is None
