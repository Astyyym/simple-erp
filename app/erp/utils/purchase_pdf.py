"""Single purchase documents: business snapshots, never inventory valuation."""
from pathlib import Path

from flask import current_app

from erp.config import load_config, project_path, runtime_root
from erp.db import get_db
from erp.utils.errors import RecordNotFound
from erp.utils.money import cents_to_yuan
from erp.utils.quantity import format_quantity_3dp

# Deferred WeasyPrint seams: WeasyPrint is imported on first use so app boot does
# not pay its cost. A PEP 562 module `__getattr__` keeps `purchase_pdf.HTML` /
# `.CSS` readable as the real classes (tests/test_purchase_printing.py reads
# `purchase_pdf.HTML`), and `_weasy()` still honours an explicit module-level
# override such as `monkeypatch.setattr(purchase_pdf, "HTML", ...)`.
def __getattr__(name):
    if name in ("CSS", "HTML"):
        from weasyprint import CSS as _CSS, HTML as _HTML
        return {"CSS": _CSS, "HTML": _HTML}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _weasy():
    global CSS, HTML
    globals_css = globals().get("CSS")
    globals_html = globals().get("HTML")
    if globals_css is None:
        from weasyprint import CSS as _CSS
        CSS = globals_css = _CSS
    if globals_html is None:
        from weasyprint import HTML as _HTML
        HTML = globals_html = _HTML
    return globals_css, globals_html


# Shared sales styling pads the first/last fragment only. Purchase-only paged
# overrides repeat the same 12mm frame on all sheets, without changing sales PDFs.
_PURCHASE_PAGE_FRAME = '''
@page { size: 210mm 297mm !important; margin: 12mm !important; }
.page { width: auto !important; min-height: 0 !important; padding: 0 !important; }
'''


def generate_purchase_pdf(order_id: int, *, is_return: bool = False) -> Path:
    table = 'purchase_return_orders' if is_return else 'purchase_orders'
    item_table = 'purchase_return_items' if is_return else 'purchase_order_items'
    owner = 'purchase_return_id' if is_return else 'purchase_order_id'
    price = 'unit_price_cents' if is_return else 'unit_cost_cents'
    label = '退拿货单' if is_return else '拿货单'
    with get_db() as conn:
        # Validate the header and read its business lines from one SQLite snapshot.
        conn.execute('BEGIN')
        order = conn.execute(
            f'SELECT order_no,business_date,customer_id,customer_name,total_amount_cents,status,deleted_at '
            f'FROM {table} WHERE id=?', (order_id,),
        ).fetchone()
        if order is None or order['deleted_at'] is not None:
            raise RecordNotFound(f'{label}不存在或已删除，不能打印')
        if order['status'] != 'saved':
            raise ValueError(f'{label}仅正式有效单据可以打印；草稿、作废或未知状态不能打印')
        if not order['customer_id'] or not (order['customer_name'] or '').strip():
            raise ValueError(f'{label}未关联明确往来对象，需人工确认，不能打印')
        items = conn.execute(
            f'SELECT product_name,spec,unit,quantity_3dp,{price} AS unit_price_cents,subtotal_cents '
            f'FROM {item_table} WHERE {owner}=? ORDER BY id', (order_id,),
        ).fetchall()
    public_order = {
        'order_no': order['order_no'], 'order_date': order['business_date'],
        'customer_name': order['customer_name'], 'total_amount_cents': int(order['total_amount_cents']),
        'notes': '',
    }
    public_items = [{
        'product_name': i['product_name'], 'spec': i['spec'] or '', 'unit': i['unit'],
        'quantity': format_quantity_3dp(i['quantity_3dp'], i['unit']),
        'unit_price_cents': int(i['unit_price_cents']), 'subtotal_cents': int(i['subtotal_cents']),
    } for i in items]
    title = '退拿货清单' if is_return else '拿货清单'
    config = load_config()
    public_config = {key: config[key] for key in (
        'shop_name', 'order_pdf_page_width_mm', 'order_pdf_page_height_mm',
        'print_offset_x_mm', 'print_offset_y_mm', 'print_scale',
        'print_order_phone', 'print_order_address', 'print_main_business', 'print_maker_name',
    )}
    # Purchase documents follow the current A4 contract, including legacy user configs.
    # Reuse only the sales calibration, never screen zoom or obsolete paper dimensions.
    public_config['order_pdf_page_width_mm'] = 210
    public_config['order_pdf_page_height_mm'] = 297
    # Direct Jinja rendering avoids Flask context processors re-injecting full erp_config.
    html = current_app.jinja_env.get_template('orders/print_template.html').render(
        order=public_order, items=public_items,
        config=public_config, cents_to_yuan=cents_to_yuan, document_title=title,
        document_type='purchase_return' if is_return else 'purchase',
        document_direction='本店退给对方' if is_return else '本店买入',
        object_role='往来对象（本单卖方）',
    )
    out = project_path('temp_pdf', f"{'purchase_return' if is_return else 'purchase'}_{int(order_id)}.pdf")
    css_cls, html_cls = _weasy()
    html_cls(string=html, base_url=str(runtime_root())).write_pdf(
        out, stylesheets=[css_cls(string=_PURCHASE_PAGE_FRAME)],
    )
    return out
