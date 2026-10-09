"""商品管理 / 客户管理顶部筛选与新增区的排版契约（2026-10-09 重排）。

背景：两页顶部原本把「导入 / 查找 / 档案筛选 / 新增」堆成四条整宽玻璃条，
`form.row` 的等分 `col` 把控件按格子分而不是按语义分，1280 窗口下：
- 导入区文件框 + 同名下拉已占 488px，「预检并导入」被迫掉到第二行；
- 查找行/新增行各占 78px 高却只装 40px 控件，行内空、行间只剩 8px；
- 1100 窗口下「默认单价(元)」占位文字需要 79px、可用只有 71px，文字被裁。

现在两页共用 base.html 的 `.list-*` 类：导入独立成卡片，查找/筛选/新增
合成一张 `.list-tools`，字段用等宽网格（给舒适下限，宁可整齐换行也不挤成一排）。
这些断言锁的是**结构钩子**（class / 元素），不是可见文案，避免注释或提示
文字里出现同名短语就假通过。
"""
import io
import re
import uuid

from erp import create_app
from erp.db import init_db

PAGES = ("/products/", "/customers/")


def _html(path):
    init_db()
    return create_app().test_client().get(path).get_data(as_text=True)


def _preview_json(html: str) -> str:
    """从导入预览页取出 preview_json 隐藏域（确认落库需要它）。"""
    import html as html_module

    match = re.search(r'name="preview_json" value="([^"]*)"', html)
    assert match, "预检页必须携带 preview_json 隐藏域"
    return html_module.unescape(match.group(1))


def test_master_data_pages_share_one_tool_card_and_import_card():
    """两页都用同一套 .list-tools / .list-import-card 结构，不再各写一份 row/col。"""
    for path in PAGES:
        html = _html(path)
        assert 'class="list-import-card"' in html, path
        assert 'class="list-tools"' in html, path
        for hook in (
            'class="list-search"',
            'class="list-facet"',
            'class="list-create"',
            'class="list-create-fields"',
            'class="list-import-row"',
            'class="list-import-form"',
            'class="list-import-hint"',
        ):
            assert hook in html, f"{path} 缺少 {hook}"
        # 旧结构必须彻底消失：等分 col 会让控件按格子分而不是按语义分。
        assert 'class="row g-2 mb-3"' not in html, f"{path} 仍在用 row/col 平铺"
        assert '<button class="btn btn-primary col-2">' not in html, path


def test_shared_layout_classes_are_defined_once_in_base():
    """两个页面共用一套类，CSS 只写在 base.html 里（避免两份实现漂移）。"""
    html = _html("/products/")
    for rule in (
        ".list-tools,.list-import-card{",
        ".list-search{display:flex;flex-wrap:wrap",
        ".list-search .form-control{flex:1 1 260px",
        ".list-facet{display:flex;flex-wrap:wrap",
        ".list-create{display:flex;flex-wrap:wrap",
        ".list-create-fields{",
        "grid-template-columns:repeat(auto-fit,minmax(180px,1fr))",
        ".list-import-form{display:flex;flex-wrap:wrap",
    ):
        assert rule in html, rule
    # 字段网格必须有舒适下限，否则 6 个字段会被压到 116px 挤成一排。
    assert "minmax(112px" not in html
    # 顶部工具区的类只在 base.html 定义，两个页面模板里不得再自带覆盖。
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "app" / "erp" / "templates"
    for rel in ("products/list.html", "customers/list.html"):
        source = (templates / rel).read_text(encoding="utf-8")
        assert "row g-2" not in source, rel
        assert ".list-search{" not in source, rel
        assert ".list-tools{" not in source, rel


def test_search_row_keeps_input_and_both_buttons_in_one_flex_row():
    """查找行：输入框 flex 撑开、两个按钮固定宽，同一行内不靠 col 分格。"""
    for path, action in zip(PAGES, ("/products/", "/customers/")):
        html = _html(path)
        search = html.split('class="list-search"', 1)[1].split("</form>", 1)[0]
        assert f'action="{action}"' in search, path
        assert 'class="form-control"' in search
        assert "查找商品" in search or "查找客户" in search
        assert "清除信息" in search
        assert 'list="product_name_suggestions"' in search or 'list="customer_name_suggestions"' in search
        # 清除信息仍是回到本页的链接，不改成按钮。
        assert f'href="{action}"' in search, path


def test_facet_row_keeps_cap_and_chips_together():
    """档案筛选：标签 + 全部/待补全 chip 同属一行，且仍沿用 btn-sm 语义。"""
    for path in PAGES:
        html = _html(path)
        facet = html.split('class="list-facet"', 1)[1].split("</div>", 1)[0]
        assert 'class="list-facet-cap"' in facet, path
        assert "档案筛选" in facet
        assert "全部" in facet and "待补全" in facet
        assert facet.count("btn-sm") == 2, path


def test_facet_hint_uses_shared_hint_class_when_todo_filter_active():
    """待补全筛选生效时，说明文字走 .list-facet-hint，不再用 Bootstrap text-muted small。"""
    for path, keyword in zip(PAGES, ("型号为空", "电话与地址都为空")):
        html = _html(f"{path}?quality=todo")
        facet = html.split('class="list-facet"', 1)[1].split("</div>", 1)[0]
        assert 'class="list-facet-hint"' in facet, path
        assert keyword in facet, path
        # 旧写法（档案筛选行里用 text-muted small）应已移除。
        assert 'class="text-muted small"' not in facet, path


def test_create_forms_keep_their_fields_and_action_endpoints():
    """新增表单字段与端点不变，只是排版换了容器（不能因重排丢字段）。"""
    products = _html("/products/")
    create = products.split('class="list-create"', 1)[1].split("</form>", 1)[0]
    assert 'action="/products/create"' in create
    for name in ("name", "spec", "brand", "unit", "default_price", "safety_stock"):
        assert f'name="{name}"' in create, name
    assert 'name="name"' in create and "required" in create

    customers = _html("/customers/")
    create = customers.split('class="list-create"', 1)[1].split("</form>", 1)[0]
    assert 'action="/customers/create"' in create
    for name in ("name", "phone", "address", "opening_balance"):
        assert f'name="{name}"' in create, name
    assert 'value="0"' in create


def test_create_panel_is_collapsed_by_default():
    """新增表单默认收起：单条新增是低频动作，常驻展开白占一行高度。"""
    for path, label in zip(PAGES, ("新增商品", "新增客户")):
        html = _html(path)
        assert 'class="list-create-panel"' in html, path
        assert 'class="list-create-toggle"' in html, path
        assert f">{label}</summary>" in html, path
        # 未带 create_open 时不得带 open 属性（默认收起）。
        panel = html.split('class="list-create-panel"', 1)[1].split(">", 1)[0]
        assert "open" not in panel, f"{path} 默认应收起，实际带 open"
        # 提交按钮改名，避免与切换标题同名造成两个「新增商品」按钮。
        assert "确认新增" in html, path


def test_collapsed_panel_hides_the_form_via_css():
    """收起态必须真的隐藏面板内容：面板里的 form 自带 display:flex（作者样式），
    会盖掉 UA 的默认隐藏，所以需要显式规则，否则收起后表单仍然可见（实测踩过）。"""
    html = _html("/products/")
    for selector in (".list-create-panel:not([open])>*:not(summary)", ".list-import-panel:not([open])>*:not(summary)"):
        assert selector in html, selector


def test_failed_create_reopens_the_panel():
    """新增失败时表单必须自动展开，否则用户看不到错误也改不了值。"""
    init_db()
    client = create_app().test_client()
    response = client.post(
        "/products/create",
        data={"name": "错误单价商品", "unit": "个", "default_price": "abc"},
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "默认单价不是有效数字" in html
    panel = html.split('class="list-create-panel"', 1)[1].split(">", 1)[0]
    assert "open" in panel, "新增失败后应自动展开面板"


def test_import_forms_keep_multipart_upload_and_same_name_policy():
    """导入表单仍是 multipart，同名策略两个选项都在，且文件名控件不再是写死 280px。"""
    for path, action in zip(PAGES, ("/products/import", "/customers/import")):
        html = _html(path)
        form = html.split('class="list-import-form"', 1)[1].split("</form>", 1)[0]
        assert f'action="{action}"' in form, path
        assert 'enctype="multipart/form-data"' in form, path
        assert 'accept=".xlsx,.csv"' in form, path
        assert 'name="same_name"' in form and "同名：跳过" in form and "同名：更新" in form
        assert 'style="max-width:280px"' not in form, path


def test_import_panel_is_collapsed_but_exports_stay_visible():
    """批量导入默认收起；但「下载Excel模板」「导出Excel」是日常动作，必须留在面板外可见。

    整块卡片一起收起会把导出也藏了——那是高频动作，不能跟着低频的上传一起收。
    """
    for path in PAGES:
        html = _html(path)
        assert 'class="list-import-panel"' in html, path
        assert 'class="list-import-toggle"' in html, path
        panel = html.split('class="list-import-panel"', 1)[1].split(">", 1)[0]
        assert "open" not in panel, f"{path} 默认应收起"
        # 导出/模板链接必须在 <details> 之外（列表前面），不能被收进面板。
        before_panel = html.split('class="list-import-panel"', 1)[0]
        assert "导出Excel" in before_panel, path
        assert "下载Excel模板" in before_panel, path
        # 面板内的上传控件仍在（只是收起后不可见）。
        assert 'class="list-import-form"' in html, path


def test_import_failure_reopens_the_panel():
    """导入失败时面板必须自动展开，否则用户看不到错误原因。"""
    init_db()
    client = create_app().test_client()
    response = client.post(
        "/products/import",
        data={"file": (io.BytesIO(b"x"), "bad.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "仅支持 .xlsx 或 .csv 文件" in html
    panel = html.split('class="list-import-panel"', 1)[1].split(">", 1)[0]
    assert "open" in panel, "导入失败后应自动展开面板"


def test_import_result_reopens_the_panel():
    """导入完成（预览或确认）后面板要展开，让用户看到结果摘要。"""
    init_db()
    client = create_app().test_client()
    name = f"导入面板商品-{uuid.uuid4().hex[:8]}"
    csv_data = f"商品名称,价格\n{name},9.99\n".encode("utf-8-sig")
    preview = client.post(
        "/products/import",
        data={"file": (io.BytesIO(csv_data), "ok.csv"), "same_name": "skip"},
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    preview_html = preview.get_data(as_text=True)
    confirm_url = "/products/import/confirm"
    confirmed = client.post(
        confirm_url,
        data={"preview_json": _preview_json(preview_html), "same_name": "skip"},
        follow_redirects=True,
    )
    assert confirmed.status_code == 200
    html = confirmed.get_data(as_text=True)
    assert name in html
    panel = html.split('class="list-import-panel"', 1)[1].split(">", 1)[0]
    assert "open" in panel, "导入完成后面板应展开显示结果"


def test_customer_table_colgroup_matches_header_column_count():
    """客户表 colgroup 列数必须和表头一致。

    历史缺陷：加了「来源」列却没同步 colgroup（6 列 vs 7 列），
    table-layout:fixed 按首行取宽，导致「电话」「期初」表头被截断成「电…」「期…」。
    """
    html = _html("/customers/")
    colgroup = re.search(r"<colgroup>(.*?)</colgroup>", html, re.S)
    assert colgroup, "客户表应有 colgroup"
    col_count = colgroup.group(1).count("<col")
    thead = re.search(r"<thead>(.*?)</thead>", html, re.S).group(1)
    th_count = thead.count("<th")
    assert col_count == th_count, f"colgroup {col_count} 列 != 表头 {th_count} 列"


def test_customer_table_action_column_leaves_room_for_buttons():
    """操作列宽度必须够放「编辑 + 删除」，且表头也带同样的宽度类。

    table-layout:fixed 下宽度取自首行，宽度类只加在 <td> 上会被忽略。
    必须真造一行客户，空表体的 <tbody> 里没有 <td>，断言会假失败。
    """
    init_db()
    app = create_app()
    client = app.test_client()
    with app.app_context():
        from erp.services.accounting import create_customer

        create_customer(f"操作列测试客户-{uuid.uuid4().hex[:8]}", phone="13900000000")
    html = client.get("/customers/").get_data(as_text=True)
    thead = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert 'class="action-cell"' in thead
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert 'class="action-cell"' in body
    assert '<td class="action-wrap"' not in body
