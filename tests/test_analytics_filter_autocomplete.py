"""数据分析页的客户 / 商品名称筛选改「输入即模糊匹配 + 候选点选」（2026-10-17 用户要求）。

用户原话：「数据分析页的客户和商品名称也没有做模糊匹配，也是只能选择」；
用户确认的口径：候选层模糊（中文子串 / 拼音首字母 / 全拼），**选中项本身仍是精确单品**。

契约（本文件钉住）：
1. 两个字段都是 combobox 输入框 + 候选浮层，不再是全量 `<select>`（商品多时全量下拉尤其难用）；
2. 客户提交值仍是 hidden `customer_id`（后端校验、chip、view_url 都按 id 工作）；商品提交完整名称；
3. 候选来源：客户 `/orders/api/customers`、商品 `/products/api/suggestions`；
4. 只输入名称、没点候选 → 明确提示，既不静默变成「全部」，也不静默返回空结果；
5. 原先无人消费的全量商品 JSON（`#analysisProductOptions`）不再下发。
"""
from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product


def _seed():
    pid = create_product("联想分析商品", "个")
    cid = create_customer("联想分析客户")
    create_order_from_typed_rows(
        cid,
        "MD202610170902",
        [{"product_id": pid, "product_name": "联想分析商品", "unit": "个",
          "unit_price_yuan": "30", "quantity": "1"}],
        status="saved",
        order_date="2026-10-17",
    )
    return cid, pid


def test_analytics_filter_fields_are_combobox_not_select():
    init_db()
    _seed()
    html = create_app().test_client().get("/analytics/").get_data(as_text=True)
    assert 'id="customerFilterInput"' in html and 'name="customer_name"' in html
    assert 'id="customerFilter" name="customer_id"' in html
    assert 'id="analytics_customer_suggestions"' in html
    assert 'id="productFilter"' in html and 'name="product_name"' in html
    assert 'id="analytics_product_suggestions"' in html
    assert "/orders/api/customers?q=" in html and "/products/api/suggestions?q=" in html
    # 旧的全量下拉与无人消费的商品 JSON 都不再下发
    assert 'id="customerFilter" class="form-select"' not in html
    assert 'id="productFilter" class="form-select"' not in html
    assert 'id="analysisProductOptions"' not in html


def test_analytics_unpicked_customer_name_shows_hint():
    init_db()
    _seed()
    response = create_app().test_client().get("/analytics/?customer_name=联想")
    html = response.get_data(as_text=True)
    assert "客户名称未匹配到已建档客户" in html
    assert response.status_code == 400
    # 输入内容回显，用户不必重打；结果区不渲染
    assert 'value="联想"' in html
    assert 'id="analysisMetrics"' not in html


def test_analytics_unpicked_product_name_shows_hint():
    init_db()
    _seed()
    response = create_app().test_client().get("/analytics/?product_name=联想")
    html = response.get_data(as_text=True)
    assert "商品名称未匹配到已建档商品" in html
    assert response.status_code == 400
    assert 'value="联想"' in html
    assert 'id="analysisMetrics"' not in html


def test_analytics_selected_customer_and_product_render_results():
    init_db()
    cid, _ = _seed()
    html = create_app().test_client().get(
        f"/analytics/?customer_id={cid}&customer_name=联想分析客户&product_name=联想分析商品"
    ).get_data(as_text=True)
    assert 'id="analysisMetrics"' in html
    assert f'id="customerFilter" name="customer_id" value="{cid}"' in html
    assert 'value="联想分析客户"' in html
    assert 'value="联想分析商品"' in html
    assert "客户名称未匹配到已建档客户" not in html
    assert "商品名称未匹配到已建档商品" not in html


def test_analytics_selected_customer_name_is_resolved_from_id():
    """只带 id（chip 移除、页内切换等路径）时，可见框仍要回显客户名，不能空着。"""
    init_db()
    cid, _ = _seed()
    html = create_app().test_client().get(f"/analytics/?customer_id={cid}").get_data(as_text=True)
    assert 'value="联想分析客户"' in html


def test_analytics_exact_product_name_still_single_product_scope():
    """候选是模糊的、选中项是精确的：提交完整名称仍只筛这一个商品（口径未变）。"""
    init_db()
    cid, _ = _seed()
    other = create_product("联想分析商品二号", "个")
    create_order_from_typed_rows(
        cid,
        "MD202610170903",
        [{"product_id": other, "product_name": "联想分析商品二号", "unit": "个",
          "unit_price_yuan": "10", "quantity": "1"}],
        status="saved",
        order_date="2026-10-17",
    )
    html = create_app().test_client().get(
        f"/analytics/?customer_id={cid}&product_name=联想分析商品"
    ).get_data(as_text=True)
    rows_html = html.split('id="analysisRows"')[1].split("</tbody>")[0]
    assert "联想分析商品二号" not in rows_html
    assert rows_html.count("<tr") == 1
