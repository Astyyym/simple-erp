"""Batch D：桌面预览一次点击只渲染一次 PDF（渲染次数 2 → 1）。

用户反馈「保存并打印有卡顿」。实测根因不是保存慢（POST 仅 0.22~0.38s），而是
一次点击把 PDF 渲染了两遍：桌面预览页先渲染一遍，iframe 再请求同一路径又渲染一遍
（纯渲染合计约 1.9s）。

契约：
- `?desktop_preview=1` 时预览页**不再生成 PDF**，只渲染工具栏；PDF 交给 iframe 请求一次。
- 预览模式仍保住错误契约（订单不存在 404、状态非法 400），不能因为「不渲染」就放过校验。
- 浏览器直接访问（无 `desktop_preview`）行为不变：仍返回真实 PDF。
- `window.pywebview.api.save_pdf` 保存链路不变（按钮按需 fetch 同一路径取字节）。
"""

import re
from uuid import uuid4

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows


def _make_order():
    suffix = uuid4().hex[:8]
    customer_id = create_customer(f"渲染计数-{suffix}")
    order_id = create_order_from_typed_rows(
        customer_id,
        f"RENDER-{suffix}",
        [{"product_name": "计数阀", "unit": "只", "unit_price_yuan": "15", "quantity": "1"}],
        status="saved",
    )
    return customer_id, order_id


def _count_renders(monkeypatch, client, paths):
    """计数 `generate_order_pdf` 的真实调用次数。"""
    from erp.utils import pdf as pdf_module

    calls = {"count": 0}
    original = pdf_module.generate_order_pdf

    def counting(order_id):
        calls["count"] += 1
        return original(order_id)

    monkeypatch.setattr(pdf_module, "generate_order_pdf", counting)
    # 路由模块是 `from ... import generate_order_pdf`，需同步替换它持有的引用。
    from erp.routes import orders as orders_module

    monkeypatch.setattr(orders_module, "generate_order_pdf", counting)
    for path in paths:
        client.get(path)
    return calls["count"]


def test_desktop_preview_page_does_not_render_pdf(monkeypatch):
    """预览页请求本身不再渲染 PDF（iframe 才是唯一渲染方）。"""
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()

    renders = _count_renders(monkeypatch, client, [f"/orders/{order_id}/pdf?desktop_preview=1"])
    assert renders == 0, "预览页不应再渲染 PDF"

    html = client.get(f"/orders/{order_id}/pdf?desktop_preview=1").get_data(as_text=True)
    assert 'id="savePdf"' in html
    assert "<iframe" in html
    # iframe 指向同一路径且不带 desktop_preview —— 那一次请求才会真的渲染。
    frame = re.search(r'<iframe src="([^"]+)"', html).group(1)
    assert "desktop_preview" not in frame
    assert frame == f"/orders/{order_id}/pdf"


def test_one_save_and_print_click_renders_pdf_exactly_once(monkeypatch):
    """完整链路：预览页 + iframe = 一次点击只渲染一次。"""
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()

    # 先取到预览页，拿到 iframe 的真实 src（模拟浏览器的第二次请求）。
    preview = client.get(f"/orders/{order_id}/pdf?desktop_preview=1").get_data(as_text=True)
    frame = re.search(r'<iframe src="([^"]+)"', preview).group(1)

    renders = _count_renders(
        monkeypatch, client, [f"/orders/{order_id}/pdf?desktop_preview=1", frame]
    )
    assert renders == 1, "一次点击必须只渲染一次 PDF"


def test_desktop_preview_still_reports_missing_order_as_404(monkeypatch):
    """不渲染不等于不校验：订单不存在时预览页仍给 404，而不是空白工具栏。"""
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    client = create_app().test_client()

    response = client.get("/orders/999999/pdf?desktop_preview=1")
    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert "不存在" in response.get_data(as_text=True)


def test_desktop_preview_still_reports_deleted_order_as_404(monkeypatch):
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    _, order_id = _make_order()
    with get_db() as conn:
        conn.execute("UPDATE orders SET deleted_at='2026-10-11 00:00:00' WHERE id=?", (order_id,))
    client = create_app().test_client()

    response = client.get(f"/orders/{order_id}/pdf?desktop_preview=1")
    assert response.status_code == 404


def test_browser_pdf_path_still_renders_real_pdf(monkeypatch):
    """浏览器直接访问（无 desktop_preview）行为不变：一次请求一份真实 PDF。"""
    init_db()
    _, order_id = _make_order()
    client = create_app().test_client()

    renders = _count_renders(monkeypatch, client, [f"/orders/{order_id}/pdf"])
    assert renders == 1

    response = client.get(f"/orders/{order_id}/pdf")
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_purchase_desktop_preview_keeps_validation_without_rendering(monkeypatch):
    """拿货/退拿货同一契约：预览页不渲染，但仍校验单据状态。"""
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    from erp.services.accounting import create_product
    from erp.services.inventory import create_purchase_order, initialize_product
    from erp.utils import purchase_pdf

    customer_id = create_customer("渲染计数采购方")
    product_id = create_product("渲染计数采购商品R", "个", 1000)
    initialize_product(product_id, "0", "0", "2026-10-11", "渲染期初", "render-init", confirm_zero=True)
    purchase = create_purchase_order(
        "NH202610110001", "2026-10-11",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "5"}],
        customer_id=customer_id, request_key="render-purchase",
    )
    order_id = purchase["order_id"]

    calls = {"count": 0}
    original = purchase_pdf.generate_purchase_pdf

    def counting(order_id, **kwargs):
        calls["count"] += 1
        return original(order_id, **kwargs)

    monkeypatch.setattr(purchase_pdf, "generate_purchase_pdf", counting)
    from erp.routes import purchases as purchases_module

    monkeypatch.setattr(purchases_module, "generate_purchase_pdf", counting, raising=False)

    client = create_app().test_client()
    preview = client.get(f"/purchases/{order_id}/pdf?desktop_preview=1")
    assert preview.status_code == 200 and preview.mimetype == "text/html"
    assert calls["count"] == 0, "拿货预览页不应渲染 PDF"
    frame = re.search(r'<iframe src="([^"]+)"', preview.get_data(as_text=True)).group(1)
    real = client.get(frame)
    assert real.mimetype == "application/pdf"
    assert calls["count"] == 1

    # 不存在/已删除 → 404；状态非法 → 400，预览模式下依然成立。
    assert client.get("/purchases/999999/pdf?desktop_preview=1").status_code == 404
    with get_db() as conn:
        conn.execute("UPDATE purchase_orders SET status='draft' WHERE id=?", (order_id,))
    assert client.get(f"/purchases/{order_id}/pdf?desktop_preview=1").status_code == 400


def test_settings_sample_desktop_preview_skips_render(monkeypatch):
    """设置样张与业务数据无关，预览页同样不再预渲染；三个版面都要守这条。"""
    monkeypatch.setenv("ERP_DESKTOP", "1")
    init_db()
    from erp.utils import pdf as pdf_module

    calls = {name: 0 for name in (
        "generate_settings_print_preview_pdf",
        "generate_settings_summary_preview_pdf",
        "generate_settings_reconciliation_preview_pdf",
    )}

    def counting(name):
        original = originals[name]

        def wrapper():
            calls[name] += 1
            return original()

        return wrapper

    from erp.routes import settings as settings_module

    # 先把原函数抓在手里再打补丁：否则第二次 getattr 会拿到第一层包装，计数翻倍。
    originals = {name: getattr(pdf_module, name) for name in calls}
    for name in calls:
        monkeypatch.setattr(pdf_module, name, counting(name))
        monkeypatch.setattr(settings_module, name, counting(name))

    client = create_app().test_client()
    for name, path in (
        ("generate_settings_print_preview_pdf", "/settings/print-preview.pdf"),
        ("generate_settings_summary_preview_pdf", "/settings/print-preview/summary.pdf"),
        ("generate_settings_reconciliation_preview_pdf", "/settings/print-preview/reconciliation.pdf"),
    ):
        preview = client.get(f"{path}?desktop_preview=1")
        assert preview.status_code == 200 and preview.mimetype == "text/html", path
        assert calls[name] == 0, f"{path} 预览页不应渲染 PDF"
        # 浏览器直接访问仍返回真实 PDF。
        direct = client.get(path)
        assert direct.mimetype == "application/pdf", path
        assert calls[name] == 1, path


def test_pdf_engine_prewarm_imports_weasyprint_without_rendering():
    """启动预热只导入 WeasyPrint，不渲染任何 PDF。"""
    from erp.utils import pdf as pdf_module

    thread = pdf_module.prewarm_pdf_engine()
    thread.join(timeout=30)
    assert not thread.is_alive(), "预热线程必须能结束，不能挂住启动"
    assert pdf_module.HTML is not None, "预热后 WeasyPrint 类应已就绪"
