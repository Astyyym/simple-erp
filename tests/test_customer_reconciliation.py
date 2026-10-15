from pathlib import Path

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    add_adjustment,
    add_payment,
    create_customer,
    create_order_from_typed_rows,
    create_product,
    void_payment,
)
from erp.services.inventory import initialize_product
from erp.services.reconciliation import (
    create_reconciliation_snapshot,
    get_reconciliation_snapshot,
    update_reconciliation_selection,
    toggle_reconciliation_selection,
    validate_reconciliation_snapshot,
)
from erp.utils import pdf
from pdf_test_utils import assert_a4_portrait_pdf


def _seed_reconciliation_data():
    init_db()
    product = create_product("对账灭火器4kg", "个", 3000)
    initialize_product(product, "20", "10", "2026-10-01", "期初", "reconciliation-init")
    customer_id = create_customer("对账客户")
    sale_id = create_order_from_typed_rows(
        customer_id,
        "MD202610010201",
        [{"product_id": product, "product_name": "对账灭火器4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "2"}],
        status="saved",
        order_date="2026-10-01",
        notes="客户可见备注",
    )
    with get_db() as conn:
        sale_item_id = conn.execute("SELECT id FROM order_items WHERE order_id=?", (sale_id,)).fetchone()["id"]
    from erp.services.accounting import create_return_order_from_source

    return_id = create_return_order_from_source(
        customer_id,
        "MD202610020202",
        sale_id,
        [{"source_item_id": sale_item_id, "quantity": "1"}],
        status="saved",
        order_date="2026-10-02",
        notes="退货备注",
    )
    other_customer = create_customer("其他客户")
    other_order_id = create_order_from_typed_rows(
        other_customer,
        "MD202610020203",
        [{"product_id": product, "product_name": "对账灭火器4kg", "unit": "个", "unit_price_yuan": "20", "quantity": "1"}],
        status="saved",
        order_date="2026-10-02",
    )
    return customer_id, sale_id, return_id, other_order_id


def test_reconciliation_default_selects_full_customer_scope_and_exclusion_is_exact():
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()

    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    snapshot = get_reconciliation_snapshot(snapshot_id)
    assert snapshot["scope_ids"] == [f"order:{sale_id}", f"order:{return_id}"]
    assert snapshot["selected_ids"] == [f"order:{sale_id}", f"order:{return_id}"]
    assert snapshot["selected_total_cents"] == 2000

    update_reconciliation_selection(snapshot_id, mode="all", excluded_ids=[f"order:{return_id}"])
    selected = get_reconciliation_snapshot(snapshot_id)
    assert selected["selected_ids"] == [f"order:{sale_id}"]
    assert selected["selected_total_cents"] == 4000
    assert selected["selected_count"] == 1


def test_reconciliation_rejects_scope_change_and_cross_customer_selection():
    customer_id, sale_id, _, other_order_id = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    with get_db() as conn:
        conn.execute("UPDATE orders SET total_amount_cents=9999 WHERE id=?", (sale_id,))
    error = validate_reconciliation_snapshot(snapshot_id)
    assert error == "查询结果已变化，请重新查询后打印"

    with __import__("pytest").raises(ValueError, match="只能选择当前客户的单据"):
        update_reconciliation_selection(snapshot_id, mode="explicit", selected_ids=[f"order:{sale_id}", f"order:{other_order_id}"])


def test_reconciliation_statement_summary_block_and_keeps_print_whitelist(tmp_path, monkeypatch):
    """2026-10-15 单段式：业务单据明细 + 四类框/三行文字的汇总段；白名单哨兵不放松。

    2026-10-14 决策（打印账款流水 PDF 的收款/调整/余额并入对账单）保留，但 2026-10-15
    用户把纸面收紧为：只印四类开单各自总额 + 资金流水合计 + 流水总额 + 汇总，
    逐笔资金明细、四格余额、说明小字、免责声明全部不再打印。
    内部字段（成本/毛利/库存/货源）仍不得出现在纸面；打印本身不写任何资金记录。
    """
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    # 区间内一笔生效收款（微信 + 备注）、一笔生效调整、一笔作废收款。
    add_payment(customer_id, 1500, payment_date="2026-10-01", method="微信", notes="部分货款")
    adjustment_id = add_adjustment(customer_id, 300, "差额补记")
    voided = add_payment(customer_id, 999, payment_date="2026-10-02", method="现金")
    void_payment(voided, "重复登记")
    with get_db() as conn:
        conn.execute("UPDATE adjustments SET created_at=? WHERE id=?", ("2026-10-02 12:00:00", adjustment_id))

    app = create_app()
    client = app.test_client()
    page = client.get(
        "/accounts/reconciliation",
        query_string={"customer_id": customer_id, "start_date": "2026-10-01", "end_date": "2026-10-02"},
    )
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "往来对账" in html
    assert "默认选中" in html
    assert str(sale_id) in html and str(return_id) in html

    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    update_reconciliation_selection(snapshot_id, mode="all", excluded_ids=[f"order:{return_id}"])
    captured = {}

    class CapturingHtml:
        def __init__(self, string, base_url=None):
            captured["html"] = string

        def write_pdf(self, output_path):
            Path(output_path).write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(pdf, "HTML", CapturingHtml)
    with app.app_context():
        generated = pdf.generate_reconciliation_pdf(snapshot_id)
    rendered = captured["html"]
    assert generated.read_bytes().startswith(b"%PDF")

    # 业务单据段：净额只算选中单据（排除退货后只剩销售 40.00）。
    assert "流水总额：+¥40.00" in rendered
    assert "对账灭火器" in rendered
    assert "MD202610020202" not in rendered

    # 汇总段（2026-10-15 用户要求）：四类各一个矩形框，框下居中三行文字。
    assert "对账汇总" in rendered
    for label in ("销售（+）", "客户退货（−）", "拿货（−）", "退拿货（+）"):
        assert f'<div class="type-label">{label}</div>' in rendered
    assert rendered.count('class="type-cell"') == 4
    # 选中范围只剩销售 40.00；退货已取消勾选，另外三个框是 0.00。
    assert '<div class="type-value">¥40.00</div>' in rendered
    assert rendered.count('<div class="type-value">¥0.00</div>') == 3
    # 资金流水合计 = 收款 −15.00 + 调整 +3.00（作废收款 999 不计）；汇总 = 40.00 − 12.00。
    assert "资金流水合计：−¥12.00" in rendered
    assert "汇总：+¥28.00" in rendered
    # 旧的「资金往来与余额」整段（说明小字 + 逐笔资金明细 + 四格余额卡片）已按用户决策删除。
    for removed in ('class="section-note"', 'class="funds-table"', 'class="balance-grid"', 'class="summary"'):
        assert removed not in rendered, removed
    assert "期初余额" not in rendered and "区间截止欠款" not in rendered
    assert "微信" not in rendered and "差额补记" not in rendered
    assert "重复登记" not in rendered and "¥9.99" not in rendered

    # 整单交易总额独立竖列：只在「日期 · 单号 · 类型」那一行印一次，
    # 商品行留空单元格（保住整列底色），不再逐行重复该单总额。
    assert '<th class="col-doc-total">整单交易总额</th>' in rendered
    assert rendered.count('class="col-doc-total">¥40.00<') == 1
    assert rendered.count('class="col-doc-total"></td>') == 1
    assert "document-total" not in rendered  # 旧的合计横条已删除

    # 对外打印白名单哨兵：内部字段一律不得出现。
    for sentinel in ("unit_cost", "毛利润", "库存", "货源", "成本价"):
        assert sentinel not in rendered, sentinel
    # 打印不写资金记录：库里只有我们自建的两笔（一生效一作废）。
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM payments").fetchone()["count"] == 2


def test_reconciliation_pdf_endpoint_returns_a4_portrait_pdf():
    customer_id, _, _, _ = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    response = create_app().test_client().get(f"/accounts/reconciliation/pdf/{snapshot_id}")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert_a4_portrait_pdf(response.data)


def test_reconciliation_type_filter_and_empty_selection_are_explicit():
    from erp.utils.pdf import generate_reconciliation_pdf

    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    sale_snapshot = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "sale")
    sale_result = get_reconciliation_snapshot(sale_snapshot)
    assert sale_result["scope_ids"] == [f"order:{sale_id}"]

    empty_snapshot = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    update_reconciliation_selection(empty_snapshot, mode="explicit", selected_ids=[])
    with __import__("pytest").raises(ValueError, match="未选择任何单据"):
        generate_reconciliation_pdf(empty_snapshot)


def test_reconciliation_single_toggle_persists_across_snapshot_page_reads():
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    toggle_reconciliation_selection(snapshot_id, f"order:{return_id}", False)
    assert get_reconciliation_snapshot(snapshot_id)["selected_ids"] == [f"order:{sale_id}"]
    toggle_reconciliation_selection(snapshot_id, f"order:{return_id}", True)
    assert get_reconciliation_snapshot(snapshot_id)["selected_ids"] == [f"order:{sale_id}", f"order:{return_id}"]


def test_reconciliation_order_detail_endpoint_is_limited_to_snapshot_scope():
    customer_id, sale_id, _, other_order_id = _seed_reconciliation_data()
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    client = create_app().test_client()
    detail = client.get(f"/accounts/reconciliation/{snapshot_id}/order/{sale_id}")
    assert detail.status_code == 200
    assert detail.get_json()["items"][0]["product_name"] == "对账灭火器4kg"
    rejected = client.get(f"/accounts/reconciliation/{snapshot_id}/order/{other_order_id}")
    assert rejected.status_code == 404


def test_reconciliation_keeps_selection_across_result_pages():
    customer_id, sale_id, return_id, _ = _seed_reconciliation_data()
    with get_db() as conn:
        product_id = conn.execute("SELECT product_id FROM order_items WHERE order_id=?", (sale_id,)).fetchone()["product_id"]
    for index in range(49):
        create_order_from_typed_rows(
            customer_id,
            f"MD20261002{index + 3000:04d}",
            [{"product_id": product_id, "product_name": "对账灭火器4kg", "unit": "个", "unit_price_yuan": "1", "quantity": "0.1"}],
            status="saved",
            order_date="2026-10-02",
        )
    snapshot_id = create_reconciliation_snapshot(customer_id, "2026-10-01", "2026-10-02", "all")
    page_two = create_app().test_client().get(f"/accounts/reconciliation?snapshot_id={snapshot_id}&page=2")
    assert page_two.status_code == 200
    assert "第 2 / 2 页" in page_two.get_data(as_text=True)
    snapshot = get_reconciliation_snapshot(snapshot_id)
    second_page_id = snapshot["scope_ids"][-1]
    toggle_reconciliation_selection(snapshot_id, second_page_id, False)
    updated = get_reconciliation_snapshot(snapshot_id)
    assert updated["selected_count"] == len(snapshot["scope_ids"]) - 1
    assert second_page_id not in updated["selected_ids"]
