"""Exercise the real ledger PDF without changing any accounting semantics."""
from erp import create_app
from erp.db import get_db
from erp.services import accounting
from erp.services.inventory import initialize_product
from erp.utils import pdf
from pdf_test_utils import assert_a4_portrait_pdf


def _two_item_sale():
    app = create_app()
    customer_id = accounting.create_customer("明细测试往来", opening_balance_cents=10000)
    for name, spec, unit, price in (("明细消防器材10","DN65","个",2850),("明细消防器材12","20m","米",3050)):
        product_id = accounting.create_product(name,spec,unit,price)
        initialize_product(product_id,"20","10.00","2026-09-01","虚构PDF测试期初",f"ledger-item-init-{product_id}")
    sale_id = accounting.create_order_from_typed_rows(
        customer_id, "MD202610020950",
        [
            {"product_name":"明细消防器材10", "spec":"DN65", "unit":"个", "quantity":"1", "unit_price_yuan":"28.50"},
            {"product_name":"明细消防器材12", "spec":"20m", "unit":"米", "quantity":"1", "unit_price_yuan":"30.50"},
        ],status="saved",order_date="2026-10-02",
    )
    return app, customer_id, sale_id


def _capture_print(app, customer_id, monkeypatch, start="", end=""):
    captured = {}
    render = pdf.render_template
    def capture(template, **context):
        captured.update(context)
        captured["html"] = render(template, **context)
        return captured["html"]
    monkeypatch.setattr(pdf, "render_template", capture)
    with app.app_context():
        output = pdf.generate_account_ledger_pdf(customer_id,start,end)
    captured["pdf"] = output.read_bytes()
    return captured


def test_multi_product_ledger_pdf_expands_exact_lines_counts_order_once_and_excludes_internal_fields(monkeypatch):
    app, customer_id, sale_id = _two_item_sale()
    with get_db() as conn:
        conn.execute("UPDATE order_items SET unit_cost_micro=987654321,cost_total_micro=987654321 WHERE order_id=?",(sale_id,))
        conn.execute("UPDATE products SET notes='INTERNAL_LEDGER_SENTINEL'")
    before = accounting.get_customer_account_ledger(customer_id)
    captured = _capture_print(app,customer_id,monkeypatch)
    entry = captured["ledger"]["entries"][0]
    assert len(entry["items"]) == 2
    allowed = {"product_name","spec","unit","quantity","unit_price_cents","subtotal_cents"}
    assert all(set(item) == allowed for item in entry["items"])
    assert [item["quantity"] for item in entry["items"]] == ["1","1"]
    assert [item["subtotal_cents"] for item in entry["items"]] == [2850,3050]
    html = captured["html"]
    table = html[html.index("<table"):html.index("</table>")+8]
    assert "余额影响" not in html
    assert "<th>商品</th>" in html and "<th>型号</th>" in html
    assert "<th>数量</th>" in html and "<th>单位</th>" in html
    assert "明细消防器材10" in table and "明细消防器材12" in table
    assert "DN65" in table and "20m" in table
    assert table.count("MD202610020950") == 1
    # 单据金额写在每一行上：续页行也能看出属于哪张单，且不出现假分段标注。
    assert 'rowspan=' not in table
    assert "第 1/1 段" not in table and "（续）" not in table
    assert table.count("+¥59.00") == 2          # 两行明细各带一次整单金额
    assert table.count("+¥28.50") == 1 and table.count("+¥30.50") == 1
    assert "INTERNAL_LEDGER_SENTINEL" not in html and "987654321" not in html
    assert "unit_cost_micro" not in repr(captured["ledger"])
    assert accounting.get_customer_account_ledger(customer_id) == before
    assert captured["ledger"]["current_balance_cents"] == 15900
    assert captured["ledger"]["period"]["net_change_cents"] == 5900
    assert_a4_portrait_pdf(captured["pdf"])


def test_multiline_document_is_a_keep_together_print_group(monkeypatch):
    app, customer_id, _ = _two_item_sale()
    captured = _capture_print(app,customer_id,monkeypatch)
    assert '<tbody class="ledger-entry">' in captured["html"]
    assert '.ledger-entry { break-inside: avoid; }' in captured["html"]


def test_one_order_stays_one_block_and_never_gets_a_fake_segment_split(monkeypatch):
    """整单一块：能一页放下就不分段，也不写「第 N/M 段」。

    旧实现按固定 12 行切块，会让 32 行单据在明明放得下时被切成 3 段。
    """
    from erp.utils import pdf as pdf_module

    # 分块逻辑已删除：不存在固定行数常量与切块函数。
    assert not hasattr(pdf_module, "LEDGER_PRINT_ROWS_PER_BLOCK")
    assert not hasattr(pdf_module, "_ledger_entry_blocks")

    app, customer_id, _ = _two_item_sale()
    captured = _capture_print(app, customer_id, monkeypatch)
    html = captured["html"]
    table = html[html.index("<table"):html.index("</table>") + 8]
    # 两行明细属于同一张单 → 只有一个 tbody，没有分段标注。
    assert html.count('<tbody class="ledger-entry">') == 1
    assert "段" not in table
    assert 'rowspan=' not in html
    # 单据金额写在每一行：两行明细在表格内各出现一次。
    assert table.count("+¥59.00") == 2
    assert table.count("+¥28.50") == 1 and table.count("+¥30.50") == 1


def test_long_ledger_pdf_continues_instead_of_starting_a_blank_page(monkeypatch):
    """45 行单据：整单一块，由排版引擎自然跨页，末页必须有内容。"""
    from erp.services.inventory import initialize_product
    from weasyprint import HTML

    app = create_app()
    customer_id = accounting.create_customer("跨页真实往来", opening_balance_cents=0)
    items = []
    for i in range(1, 46):
        product_id = accounting.create_product(f"跨页真实器材{i:03d}", f"DN{i*10}", "个", 1000 + i)
        initialize_product(product_id, "100", "10.00", "2026-09-01", "虚构期初", f"cont-{product_id}")
        items.append({"product_name": f"跨页真实器材{i:03d}", "spec": f"DN{i*10}", "unit": "个",
                      "quantity": "1", "unit_price_yuan": f"{10 + i}.00"})
    accounting.create_order_from_typed_rows(customer_id, "MD202610010001", items, status="saved", order_date="2026-10-01")

    captured = _capture_print(app, customer_id, monkeypatch)
    # 整单一块：45 行只产生一个 tbody，没有「第 N/M 段」假分段。
    assert captured["html"].count('<tbody class="ledger-entry">') == 1
    assert "第 1/1 段" not in captured["html"] and "（续）" not in captured["html"]
    doc = HTML(string=captured["html"]).render()
    assert len(doc.pages) >= 2, f"45 行必须跨页续排，实际 {len(doc.pages)} 页"

    def walk(box, acc):
        acc.append(box)
        for child in getattr(box, "children", []) or []:
            walk(child, acc)
        return acc

    last_rows = [b for b in walk(doc.pages[-1]._page_box, []) if getattr(b, "element_tag", None) == "tr"]
    assert last_rows, "末页必须有单据行，不能整页空白"
    # 每一行明细都带单据金额 → 续页的行也带，续排后仍能看出属于哪张单。
    total = sum(10 + i for i in range(1, 46))
    expected = f"¥{total}.00"
    table = captured["html"][captured["html"].index("<table"):captured["html"].index("</table>") + 8]
    assert table.count(expected) == 45, f"45 行明细应各带一次单据金额 {expected}"
    assert_a4_portrait_pdf(captured["pdf"])


def test_return_pdf_shows_original_product_and_negative_line_amount(monkeypatch):
    app, customer_id, sale_id = _two_item_sale()
    with get_db() as conn:
        item_id = conn.execute("SELECT id FROM order_items WHERE order_id=? AND unit='米'",(sale_id,)).fetchone()[0]
    return_id = accounting.create_return_order_from_source(customer_id,"MD202610020951",sale_id,
        [{"source_item_id":item_id,"quantity":"0.5"}],status="saved",order_date="2026-10-02")
    before = accounting.get_customer_account_ledger(customer_id)
    captured = _capture_print(app,customer_id,monkeypatch)
    returned = next(entry for entry in captured["ledger"]["entries"] if entry["source_id"] == return_id)
    assert returned["type"] == "return"
    assert returned["items"][0]["quantity"] == "0.5"
    assert returned["items"][0]["subtotal_cents"] == 1525
    assert "−¥15.25" in captured["html"]
    assert accounting.get_customer_account_ledger(customer_id) == before


def test_funds_do_not_inherit_same_integer_id_order_items(monkeypatch):
    app, customer_id, _ = _two_item_sale()
    accounting.add_payment(customer_id,2000,payment_date="2026-10-02",method="现金",notes="资金行测试")
    accounting.add_adjustment(customer_id,-500,"资金调整测试")
    before = accounting.get_customer_account_ledger(customer_id)
    captured = _capture_print(app,customer_id,monkeypatch)
    funds = [entry for entry in captured["ledger"]["entries"] if entry["source"] != "order"]
    assert len(funds) == 2
    assert all(entry["items"] == [] for entry in funds)
    assert "资金行测试" in captured["html"] and "资金调整测试" in captured["html"]
    assert 'colspan="5"' in captured["html"] and 'colspan="2"' in captured["html"]
    assert captured["ledger"]["current_balance_cents"] == before["current_balance_cents"]
    assert captured["ledger"]["period"] == before["period"]


def test_purchase_ledger_row_shows_negative_document_amount(monkeypatch):
    """拿货单在「单据金额」列必须显示负号（库中 total_amount_cents 恒为正）。

    业务方向：销售 +、退货 −、拿货 −、退拿货 +。旧实现直接用 amount_cents，
    拿货被显示成 +，与账务口径（balance_delta 为负）矛盾。
    """
    from erp.services.inventory import create_purchase_order

    app, customer_id, _ = _two_item_sale()
    with get_db() as conn:
        pid = conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0]
    create_purchase_order(
        "NH202610030001", "2026-10-03",
        [{"product_id": pid, "quantity": "2", "unit_cost_yuan": "30.00"}],
        customer_id=customer_id, request_key="ledger-sign-purchase",
    )
    captured = _capture_print(app, customer_id, monkeypatch)
    entries = {e["type"]: e for e in captured["ledger"]["entries"]}
    assert "purchase" in entries
    purchase_entry = entries["purchase"]
    # 库中为正，业务方向为负。
    assert purchase_entry["amount_cents"] > 0
    assert purchase_entry["doc_amount_cents"] == -purchase_entry["amount_cents"]
    table = captured["html"][captured["html"].index("<table"):captured["html"].index("</table>") + 8]
    assert "−¥60.00" in table, "拿货单单据金额必须带负号"
    assert "+¥60.00" not in table
    # 账面余额口径同步：拿货抵减欠款。
    assert captured["ledger"]["purchase_net_cents"] == -6000


def test_return_ledger_row_keeps_its_own_negative_sign(monkeypatch):
    """退货单库中已是负数，单据金额必须保持负号且不被再翻一次。"""
    app, customer_id, sale_id = _two_item_sale()
    with get_db() as conn:
        item_id = conn.execute(
            "SELECT id FROM order_items WHERE order_id=? AND unit='米'", (sale_id,)).fetchone()[0]
    accounting.create_return_order_from_source(
        customer_id, "MD202610020953", sale_id, [{"source_item_id": item_id, "quantity": "0.5"}],
        status="saved", order_date="2026-10-02")
    captured = _capture_print(app, customer_id, monkeypatch)
    ret = next(e for e in captured["ledger"]["entries"] if e["type"] == "return")
    assert ret["amount_cents"] < 0
    assert ret["doc_amount_cents"] == ret["amount_cents"]
    table = captured["html"][captured["html"].index("<table"):captured["html"].index("</table>") + 8]
    assert "−¥15.25" in table


def test_void_item_history_remains_visible_without_changing_zero_net_change(monkeypatch):
    app, customer_id, sale_id = _two_item_sale()
    accounting.void_order(sale_id,"打印重复作废测试")
    captured = _capture_print(app,customer_id,monkeypatch)
    assert len(captured["ledger"]["entries"][0]["items"]) == 2
    assert "已作废" in captured["html"] and "打印重复作废测试" in captured["html"]
    assert "明细消防器材10" in captured["html"]
    assert captured["ledger"]["current_balance_cents"] == 10000
    assert captured["ledger"]["period"]["net_change_cents"] == 0


def test_print_item_scope_matches_customer_and_date_filter(monkeypatch):
    app, customer_id, _ = _two_item_sale()
    other = accounting.create_customer("其他客户隔离")
    accounting.create_order_from_typed_rows(other,"MD202610020952",
        [{"product_name":"OTHER_CUSTOMER_ONLY","spec":"私人型号","unit":"个","quantity":"1","unit_price_yuan":"12.00"}],
        status="saved",order_date="2026-10-02")
    captured = _capture_print(app,customer_id,monkeypatch,"2026-09-01","2026-09-30")
    assert captured["ledger"]["entries"] == []
    assert "所选日期范围内没有流水记录" in captured["html"]
    assert 'colspan="11"' in captured["html"]
    assert "OTHER_CUSTOMER_ONLY" not in captured["html"] and "明细消防器材10" not in captured["html"]
    assert captured["ledger"]["current_balance_cents"] == 15900
    assert captured["ledger"]["period"]["net_change_cents"] == 0
    assert_a4_portrait_pdf(captured["pdf"])
