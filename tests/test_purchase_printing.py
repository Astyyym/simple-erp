"""C3-B purchase prints: real isolated services, PDFs and public boundaries."""

import re
import shutil
import subprocess
import zlib

import pytest

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_product
from erp.services.inventory import create_purchase_order, initialize_product
from erp.services import purchase_returns



def seed(quantity='3', price='12.34', unit='米', return_quantity='0.25'):
    init_db()
    cid = create_customer('虚构采购卖方', opening_balance_cents=12345)
    pid = create_product('虚构采购商品', 'C3-B', unit, 2000)
    initialize_product(pid, '0', '0', '2026-10-03', '虚构期初', 'c3-print-init', confirm_zero=True)
    purchase = create_purchase_order('NH202610030001', '2026-10-03',
        [{'product_id': pid, 'quantity': quantity, 'unit_cost_yuan': price}],
        customer_id=cid, request_key='c3-print-source', source_notes='INTERNAL_SOURCE_SENTINEL')
    with get_db() as conn:
        iid = conn.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=?', (purchase['order_id'],)).fetchone()[0]
    returned = purchase_returns.create(customer_id=cid, source_order_id=purchase['order_id'],
        business_date='2026-10-03', items=[{'source_item_id': iid, 'quantity': return_quantity}],
        request_key='c3-print-return', notes='INTERNAL_RETURN_SENTINEL')
    return cid, pid, purchase['order_id'], returned['order_id'], iid


def pdf_text(data, tmp_path):
    extractor = shutil.which('pdftotext')
    if not extractor:
        pytest.skip('installed PDF text extractor is unavailable')
    path = tmp_path / 'actual.pdf'
    path.write_bytes(data)
    result = subprocess.run([extractor, '-enc', 'UTF-8', str(path), '-'], capture_output=True, check=True)
    return result.stdout.decode('utf-8')


@pytest.mark.parametrize('kind,title,direction,total', [
    ('purchase', '拿货清单', '本店买入', '37.02'),
    ('purchase_return', '退拿货清单', '本店退给对方', '3.09'),
])
def test_saved_purchase_real_pdf_business_projection(tmp_path, kind, title, direction, total):
    _, _, oid, rid, _ = seed()
    source = f'/purchases/{oid}' if kind == 'purchase' else f'/purchases/return/{rid}'
    response = create_app().test_client().get(source + '/pdf')
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    every_page_box(response.data)
    text = pdf_text(response.data, tmp_path)
    for public in (title, direction, '往来对象（本单卖方）', '虚构采购卖方', '虚构采购商品', 'C3-B', '12.34', total):
        assert public in text
    assert '退货清单' not in text


@pytest.mark.parametrize('is_return', [False, True])
def test_purchase_template_receives_only_explicit_public_dictionaries(monkeypatch, is_return):
    from erp.utils import purchase_pdf
    from erp.config import load_config, save_config
    _, _, oid, rid, _ = seed()
    cfg = load_config()
    cfg['private_config'] = 'INTERNAL_CONFIG_SENTINEL'
    cfg['print_legal_note'] = 'SALES_ONLY_LEGAL_SENTINEL'
    cfg['print_receiver_label'] = 'SALES_ONLY_RECEIVER_SENTINEL'
    save_config(cfg)
    captured = {}
    def capture(**context):
        captured.update(context)
        return '<html>public</html>'
    app = create_app()
    monkeypatch.setattr(app.jinja_env.get_template('orders/print_template.html'), 'render', capture)
    with app.app_context():
        purchase_pdf.generate_purchase_pdf(rid if is_return else oid, is_return=is_return)
    assert set(captured['order']) == {'order_no', 'order_date', 'customer_name', 'notes', 'total_amount_cents'}
    assert captured['order']['notes'] == ''
    assert all(set(item) == {'product_name', 'spec', 'unit', 'quantity', 'unit_price_cents', 'subtotal_cents'} for item in captured['items'])
    assert set(captured['config']) == {
        'shop_name', 'order_pdf_page_width_mm', 'order_pdf_page_height_mm',
        'print_offset_x_mm', 'print_offset_y_mm', 'print_scale',
        'print_order_phone', 'print_order_address', 'print_main_business', 'print_maker_name',
    }
    assert not any(word in repr(captured) for word in ('INTERNAL_', 'SALES_ONLY_', 'unit_cost_micro', 'cost_total_micro', 'posting_seq', 'source_item_id'))


@pytest.mark.parametrize('is_return', [False, True])
def test_purchase_pdf_excludes_internal_sentinels_and_sales_only_legal_text(tmp_path, is_return):
    from erp.config import load_config, save_config
    cid, pid, oid, rid, _ = seed()
    with get_db() as conn:
        conn.execute("UPDATE products SET notes='INTERNAL_PRODUCT_SENTINEL', image_path='INTERNAL_IMAGE_SENTINEL' WHERE id=?", (pid,))
        conn.execute("UPDATE customers SET notes='INTERNAL_CUSTOMER_SENTINEL', phone='INTERNAL_PHONE_SENTINEL', address='INTERNAL_ADDRESS_SENTINEL' WHERE id=?", (cid,))
        conn.execute('UPDATE purchase_return_items SET unit_cost_micro=987654321000, cost_total_micro=987654321000')
        conn.execute("UPDATE customers SET name='NEW_NAME_MUST_NOT_REPLACE_SNAPSHOT' WHERE id=?", (cid,))
        conn.execute("UPDATE products SET name='NEW_PRODUCT_MUST_NOT_REPLACE_SNAPSHOT' WHERE id=?", (pid,))
    cfg = load_config()
    cfg['print_legal_note'] = 'SALES_ONLY_LEGAL_SENTINEL'
    cfg['print_receiver_label'] = 'SALES_ONLY_RECEIVER_SENTINEL'
    save_config(cfg)
    path = f'/purchases/return/{rid}/pdf' if is_return else f'/purchases/{oid}/pdf'
    response = create_app().test_client().get(path)
    assert response.status_code == 200
    text = pdf_text(response.data, tmp_path)
    assert '虚构采购卖方' in text and '虚构采购商品' in text
    for private in ('INTERNAL_', 'SALES_ONLY_', '987654.32', 'NEW_NAME_', 'NEW_PRODUCT_', '本销售单', '收货人', '移动成本', '库存', '利润'):
        assert private not in text
    assert '对方确认' in text


@pytest.mark.parametrize('is_return,invalid', [
    (is_return, invalid) for is_return in (False, True)
    for invalid in ('draft', 'void', 'unknown', 'deleted', 'missing', 'unbound', 'empty_name')
    if not (is_return and invalid == 'unbound')
])
def test_invalid_purchase_print_is_friendly_read_only_rejection(is_return, invalid):
    _, _, oid, rid, _ = seed()
    client = create_app().test_client()
    table = 'purchase_return_orders' if is_return else 'purchase_orders'
    order_id = rid if is_return else oid
    with get_db() as conn:
        if invalid in ('draft', 'void', 'unknown'):
            conn.execute('PRAGMA ignore_check_constraints=ON')
            conn.execute(f'UPDATE {table} SET status=? WHERE id=?', (invalid, order_id))
        elif invalid == 'deleted':
            conn.execute(f"UPDATE {table} SET deleted_at='2026-10-03 00:00:00' WHERE id=?", (order_id,))
        elif invalid == 'unbound':
            # Legacy purchases alone support NULL counterparties.
            conn.execute(f"UPDATE {table} SET customer_id=NULL, customer_name='不可从货源猜对象' WHERE id=?", (order_id,))
        elif invalid == 'empty_name':
            conn.execute(f"UPDATE {table} SET customer_name='' WHERE id=?", (order_id,))
    if invalid == 'missing':
        order_id = 999999
    path = f'/purchases/return/{order_id}/pdf' if is_return else f'/purchases/{order_id}/pdf'
    before = business_facts()
    for query in ('', '?desktop_preview=1'):
        response = client.get(path + query)
        assert response.status_code == 400
        assert response.mimetype == 'text/html'
        text = response.get_data(as_text=True)
        assert '不能打印' in text or '不存在' in text
        assert 'INTERNAL_' not in text
    assert business_facts() == before
    detail = client.get(path.removesuffix('/pdf'))
    if detail.status_code == 200:
        from tests.test_page_action_conventions import Elements
        assert not any(n['tag'] == 'a' and '/pdf' in n['attrs'].get('href', '')
                       for n in Elements(detail.get_data(as_text=True)).nodes)


def business_facts():
    tables = ('purchase_orders', 'purchase_order_items', 'purchase_return_orders', 'purchase_return_items',
              'products', 'customers', 'product_inventory_state', 'inventory_postings',
              'orders', 'order_items', 'customer_prices', 'payments', 'adjustments')
    with get_db() as conn:
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in tables}


@pytest.mark.parametrize('is_return', [False, True])
@pytest.mark.parametrize('desktop', [False, True])
def test_detail_pdf_action_shared_preview_exact_source_read_only(monkeypatch, tmp_path, is_return, desktop):
    from urllib.parse import parse_qs, urlencode, urlsplit
    from tests.test_page_action_conventions import Elements
    _, _, oid, rid, _ = seed()
    if desktop:
        monkeypatch.setenv('ERP_DESKTOP', '1')
    client = create_app().test_client()
    path = f'/purchases/return/{rid}' if is_return else f'/purchases/{oid}'
    source = path + '?' + urlencode({'source': '中文 & + /', 'page': '2'})
    before = business_facts()
    dom = Elements(client.get(source).get_data(as_text=True))
    returning = dom.by_class('page-return')[0]
    actions = returning['parent']
    children = [node for node in dom.nodes if node['parent'] is actions]
    assert children[-1] is returning
    assert len(children) >= 2, 'saved purchase detail must expose a PDF action before Return'
    pdf_link = children[-2]
    assert pdf_link['tag'] == 'a'
    assert urlsplit(pdf_link['attrs']['href']).path == path + '/pdf'
    query = parse_qs(urlsplit(pdf_link['attrs']['href']).query)
    if desktop:
        assert query == {'desktop_preview': ['1'], 'return_to': [source]}
        assert 'target' not in pdf_link['attrs']
    else:
        assert not query
    for _ in range(2):
        response = client.get(pdf_link['attrs']['href'])
        if desktop:
            assert response.status_code == 200 and response.mimetype == 'text/html'
            preview = Elements(response.get_data(as_text=True))
            save = preview.by_id('savePdf')
            back = preview.by_id('returnToErp')
            assert save['parent'] is back['parent']
            assert back['attrs']['href'] == source
            assert client.get(back['attrs']['href']).status_code == 200
            frame = next(n for n in preview.nodes if n['tag'] == 'iframe')
            assert 'desktop_preview' not in frame['attrs']['src']
            response = client.get(frame['attrs']['src'])
        assert response.status_code == 200 and response.mimetype == 'application/pdf'
        every_page_box(response.data)
        assert '虚构采购卖方' in pdf_text(response.data, tmp_path)
    assert business_facts() == before


def test_purchase_print_does_not_inject_full_erp_configuration(monkeypatch):
    from erp.utils.purchase_pdf import generate_purchase_pdf
    _, _, oid, _, _ = seed()
    app = create_app()
    template = app.jinja_env.get_template('orders/print_template.html')
    original = template.render
    captured = {}
    def render(*args, **kwargs):
        captured.update(dict(*args, **kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(template, 'render', render)
    with app.test_request_context(f'/purchases/{oid}/pdf'):
        generate_purchase_pdf(oid)
    assert 'erp_config' not in captured, 'print boundary must not re-inject the unfiltered app config'


@pytest.mark.parametrize('is_return', [False, True])
def test_purchase_a4_ignores_legacy_paper_configuration(tmp_path, is_return):
    from erp.config import save_config, load_config
    _, _, oid, rid, _ = seed()
    save_config({'order_pdf_page_width_mm': 241, 'order_pdf_page_height_mm': 140})
    original = load_config()
    path = f'/purchases/return/{rid}/pdf' if is_return else f'/purchases/{oid}/pdf'
    response = create_app().test_client().get(path)
    assert response.status_code == 200
    every_page_box(response.data)
    assert load_config() == original


def every_page_box(data):
    sources = [data]
    for match in re.finditer(rb'stream\r?\n', data):
        end = data.find(b'endstream', match.end())
        if end < 0:
            continue
        try:
            # zlib ignores PDF delimiter newlines after its own stream end. Stripping
            # them can truncate a valid compressed checksum ending in 0x0a/0x0d.
            sources.append(zlib.decompress(data[match.end():end]))
        except zlib.error:
            pass
    boxes = [tuple(float(x) for x in m.groups()) for source in sources
             for m in re.finditer(rb'/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([0-9.]+)\s+([0-9.]+)\s*\]', source)]
    assert boxes
    for width, height in boxes:
        assert width == pytest.approx(595.2756, abs=0.5)
        assert height == pytest.approx(841.8898, abs=0.5)
    return boxes


@pytest.mark.parametrize('is_return', [False, True])
def test_real_many_line_purchase_pdf_pages_repeat_header_and_keep_amounts(tmp_path, is_return):
    cid, pid, _, _, _ = seed()
    result = create_purchase_order('NH202610030002', '2026-10-03',
        [{'product_id': pid, 'quantity': '1.125', 'unit_cost_yuan': '12.34'} for _ in range(120)],
        customer_id=cid, request_key='many-purchase')
    oid = result['order_id']
    if is_return:
        with get_db() as conn:
            ids = [row[0] for row in conn.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id', (oid,))]
        returned = purchase_returns.create(customer_id=cid, source_order_id=oid, business_date='2026-10-03',
            items=[{'source_item_id': iid, 'quantity': '0.5'} for iid in ids], request_key='many-return')
        path = f"/purchases/return/{returned['order_id']}/pdf"
    else:
        path = f'/purchases/{oid}/pdf'
    client = create_app().test_client()
    before = business_facts()
    response = client.get(path)
    assert response.status_code == 200
    text = pdf_text(response.data, tmp_path)
    pages = [page for page in text.split('\f') if page.strip()]
    assert len(pages) > 1
    assert len(every_page_box(response.data)) == len(pages)
    assert text.count('虚构采购商品') == 120
    assert all('名称规格' in page and '小计' in page for page in pages)
    assert text.count('合计') == 1
    assert business_facts() == before


@pytest.mark.parametrize('is_return', [False, True])
def test_purchase_calibration_and_ui_zoom_do_not_change_pdf_geometry(monkeypatch, tmp_path, is_return):
    from erp.config import save_config
    from erp.utils import purchase_pdf
    _, _, oid, rid, _ = seed()
    save_config({'print_offset_x_mm': 2, 'print_offset_y_mm': -1, 'print_scale': 0.98, 'ui_scale': '100'})
    original_html = purchase_pdf.HTML
    captured = []
    def spy_html(*, string, base_url):
        captured.append(string)
        return original_html(string=string, base_url=base_url)
    monkeypatch.setattr(purchase_pdf, 'HTML', spy_html)
    path = f'/purchases/return/{rid}/pdf' if is_return else f'/purchases/{oid}/pdf'
    client = create_app().test_client()
    first = client.get(path)
    save_config({'ui_scale': '150', 'ui_theme': 'dark'})
    second = client.get(path)
    assert first.status_code == second.status_code == 200
    assert every_page_box(first.data) == every_page_box(second.data)
    assert pdf_text(first.data, tmp_path) == pdf_text(second.data, tmp_path)
    assert captured[0] == captured[1]
    assert 'translate(2mm, -1mm) scale(0.98)' in captured[0]
    assert 'padding: 12mm' in captured[0]


def test_return_last_batch_keeps_zero_cent_tail_and_exact_quantity(tmp_path):
    cid, _, oid, _, iid = seed(quantity='0.5', price='0.01')
    # seed already returned the first 0.250, absorbing its rounded 1 cent.
    last = purchase_returns.create(customer_id=cid, source_order_id=oid, business_date='2026-10-03',
        items=[{'source_item_id': iid, 'quantity': '0.25'}], request_key='tail-final')
    assert last['total_amount_cents'] == 0
    response = create_app().test_client().get(f"/purchases/return/{last['order_id']}/pdf")
    assert response.status_code == 200
    text = pdf_text(response.data, tmp_path)
    assert '0.25' in text and '0.01' in text and '¥0.00' in text
    with get_db() as conn:
        assert conn.execute('SELECT subtotal_cents FROM purchase_return_items WHERE purchase_return_id=?', (last['order_id'],)).fetchone()[0] == 0


def test_purchase_real_pdf_keeps_twelve_mm_margins_on_every_fragment(monkeypatch):
    from erp.utils import purchase_pdf
    cid, pid, _, _, _ = seed()
    order = create_purchase_order('NH202610030009', '2026-10-03',
        [{'product_id': pid, 'quantity': '1', 'unit_cost_yuan': '12.34'} for _ in range(120)],
        customer_id=cid, request_key='margin-fragments')
    original_html = purchase_pdf.HTML
    pages = []
    class LayoutAndRealPdf:
        def __init__(self, **kwargs):
            self.html = original_html(**kwargs)
        def write_pdf(self, target, **kwargs):
            document = self.html.render(**kwargs)
            pages.extend(document.pages)
            return document.write_pdf(target)
    monkeypatch.setattr(purchase_pdf, 'HTML', LayoutAndRealPdf)
    response = create_app().test_client().get(f"/purchases/{order['order_id']}/pdf")
    assert response.status_code == 200
    assert len(every_page_box(response.data)) == len(pages) > 1
    for page in pages:
        # Physical fragmentainer margins must repeat, not just .page padding on the first sheet.
        box = page._page_box
        for side in ('margin_left', 'margin_right', 'margin_top', 'margin_bottom'):
            assert getattr(box, side) == pytest.approx(12 * 96 / 25.4, abs=0.01)


@pytest.mark.parametrize('unit,quantity,returned,expected', [
    ('个', '3', '1', ('3', '1')),
    ('米', '3.125', '0.125', ('3.125', '0.125')),
])
def test_purchase_quantity_projection_keeps_unit_specific_precision(monkeypatch, tmp_path, unit, quantity, returned, expected):
    _, _, oid, rid, _ = seed(unit=unit, quantity=quantity, return_quantity=returned)
    app = create_app()
    template = app.jinja_env.get_template('orders/print_template.html')
    original = template.render
    quantities = []
    def render(**context):
        quantities.append(context['items'][0]['quantity'])
        return original(**context)
    monkeypatch.setattr(template, 'render', render)
    client = app.test_client()
    for path, display in zip((f'/purchases/{oid}/pdf', f'/purchases/return/{rid}/pdf'), expected):
        response = client.get(path)
        assert response.status_code == 200
        assert display in pdf_text(response.data, tmp_path)
    assert tuple(quantities) == expected
