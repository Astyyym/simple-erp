"""商品管理「全部 / 待补全 / 按拼音排序」（2026-10-14 用户要求）。

背景：v14 把型号并进名称后，商品侧「待补全（型号为空）」随 spec 列一起删除，
列表页顶部只剩「查找 + 新增」，而客户管理还留着「档案筛选」——两页看起来不一样
（用户 2026-10-14 截图对比）。本轮按用户指定的位置（「删除选中」右侧那块空白）
恢复三个按钮，并新增按拼音排序。

待补全口径（与客户侧同构，见 services/master_data_quality.py）：
来源 = 开单自动建档或批量导入（`origin IN ('order','import')`），且缺品牌或缺默认价（默认价为 0）。
"""
from __future__ import annotations

import re

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product, update_product_record
from erp.services.master_data_quality import product_completion_hint, product_needs_completion

# 名字特意选成「按名称排」与「按拼音排」结果相反的一对：
#   名称（Unicode 码位）：水(U+6C34) < 灭(U+706D) → 水枪在前
#   拼音：miehuoqi… < shuiqiang… → 灭火器在前
PINYIN_FIRST = "灭火器排序测试品"
PINYIN_SECOND = "水枪排序测试品"


def _seed_products():
    init_db()
    create_product(PINYIN_FIRST, "个", 1000)
    create_product(PINYIN_SECOND, "个", 1000)


def _seed_todo():
    """一个开单攒出（无品牌，算待补全）+ 一个手工建档（无品牌，不算）。"""
    init_db()
    create_product("手工无品牌测试品", "个", 1000)
    customer_id = create_customer("待补全口径测试客户")
    create_order_from_typed_rows(
        customer_id,
        "MD202610140900",
        [{"product_name": "开单攒出测试品", "unit": "个", "quantity": "1", "unit_price_yuan": "10"}],
        status="saved",
        order_date="2026-10-14",
    )


def _results_body(html: str) -> str:
    """只取结果表体：页面顶部还有建议浮层等其它内容，不能整页搜名字。"""
    section = html.split('id="productsResults"', 1)[1]
    return section.split("<tbody>", 1)[1].split("</tbody>", 1)[0]


# ---------------------------------------------------------------- 判定口径本身

def test_product_needs_completion_covers_order_and_import_sources():
    """口径（2026-10-14 第二轮）：开单攒出 + 批量导入，缺品牌**或**缺默认价都算；手工建档不算。

    导入侧扩进来的理由：门店从旧软件搬档案时字段常常不齐，这些行**照样导入**
    （默认价留空按 0），但必须被提醒补全 —— 否则列表里看着「完整」，开单按 0 计价会算错钱。
    """
    # 开单攒出：缺品牌算。
    assert product_needs_completion({"origin": "order", "brand": "", "default_price_cents": 1000}) is True
    assert product_needs_completion({"origin": "order", "brand": None, "default_price_cents": 1000}) is True
    assert product_needs_completion({"origin": "order", "brand": "远大", "default_price_cents": 1000}) is False
    # 批量导入：同样算。
    assert product_needs_completion({"origin": "import", "brand": "", "default_price_cents": 1000}) is True
    assert product_needs_completion({"origin": "import", "brand": "远大", "default_price_cents": 1000}) is False
    # 默认价为 0（导入留空）：有品牌也算缺。
    assert product_needs_completion({"origin": "import", "brand": "远大", "default_price_cents": 0}) is True
    assert product_needs_completion({"origin": "order", "brand": "远大", "default_price_cents": 0}) is True
    # 手工建档：缺什么都是自己的选择，不算（与客户侧口径一致）。
    assert product_needs_completion({"origin": "manual", "brand": "", "default_price_cents": 0}) is False
    assert product_needs_completion({"origin": None, "brand": "", "default_price_cents": 0}) is False
    # 缺品牌 + 缺价要能一起报出来（徽标 title / 编辑页提示共用）。
    both_missing = product_completion_hint({"origin": "import", "brand": "", "default_price_cents": 0})
    assert "缺品牌" in both_missing and "默认价" in both_missing


# ---------------------------------------------------------------- 按钮就位

def test_view_options_live_in_the_batch_bar_next_to_bulk_delete():
    """三个按钮放在「删除选中」右侧那块原本空着的区域，并带就地刷新钩子。"""
    init_db()
    html = create_app().test_client().get("/products/").get_data(as_text=True)
    bar = html.split('class="list-batch-bar"', 1)[1].split("</div>\n</div>", 1)[0]
    assert 'id="productsViewBar"' in html, "整条批量栏要能就地替换（否则筛选状态不会变）"
    assert "data-refresh-fragment" in html.split('class="list-batch-bar"', 1)[1][:120]
    assert "删除选中" in bar
    for label in ("档案筛选", "全部", "待补全", "按拼音排序"):
        assert label in bar, label
    assert bar.count("data-inplace") == 3, "全部 / 待补全 / 按拼音排序 都要走就地刷新"
    assert 'class="list-view-options"' in bar


def test_todo_filter_only_lists_order_archived_products_without_brand():
    _seed_todo()
    client = create_app().test_client()
    body = _results_body(client.get("/products/?quality=todo").get_data(as_text=True))
    assert "开单攒出测试品" in body
    assert "手工无品牌测试品" not in body


def test_todo_chip_count_matches_the_same_definition():
    _seed_todo()
    client = create_app().test_client()
    with get_db() as conn:
        expected = conn.execute(
            "SELECT COUNT(*) AS c FROM products WHERE deleted_at IS NULL"
            " AND origin IN ('order', 'import')"
            " AND (TRIM(COALESCE(brand, ''))='' OR COALESCE(default_price_cents, 0) <= 0)"
        ).fetchone()["c"]
    assert expected >= 1, "夹具应至少造出一个待补全商品"
    html = client.get("/products/").get_data(as_text=True)
    assert f"待补全（{expected}）" in html


def test_todo_badge_marks_the_same_products_the_filter_lists():
    _seed_todo()
    client = create_app().test_client()
    body = _results_body(client.get("/products/").get_data(as_text=True))
    row = [line for line in body.split("<tr ") if "开单攒出测试品" in line]
    assert row, "待补全商品应在全部列表里"
    assert "待补全</span>" in row[0], "列表里的待补全标记要与筛选同口径"


def test_edit_page_shows_the_completion_hint_and_it_clears_after_filling_brand():
    """商品编辑页也要说清「缺什么」（与客户编辑页对称）——否则筛选只是个死清单。

    2026-10-14「主次与分组」重构：提示从 57px 整条横幅改成「页头 chip + 字段上的 ⚠」。
    断言随之改成钉住**钩子**（`.completion-chip` / `.field-flag` / `.field-warn`）：
    文案再改也不会假通过，标记该出现时不出现会真失败。
    """
    _seed_todo()
    client = create_app().test_client()
    with get_db() as conn:
        todo_id = conn.execute("SELECT id FROM products WHERE name='开单攒出测试品'").fetchone()["id"]
        manual_id = conn.execute("SELECT id FROM products WHERE name='手工无品牌测试品'").fetchone()["id"]

    html = client.get(f"/products/{todo_id}/edit").get_data(as_text=True)
    chip_parts = html.split('class="completion-chip"', 1)
    assert len(chip_parts) == 2, "待补全商品要在页头渲染 chip"
    chip_text = chip_parts[1].split("</span>", 1)[0]
    assert "待补全" in chip_text and "缺品牌" in chip_text
    # 提示必须落到**具体字段**上，用户不用回读页头就知道改哪一格。
    assert 'class="field-warn"' in html
    assert html.count('class="field-flag"') >= 1
    brand_field = html.split('name="brand"', 1)[0].rsplit("<div", 1)[-1]
    assert "field-warn" in brand_field, "品牌字段本身要带待补全标记"

    # 手工建档即使无品牌也不算待补全 → 编辑页不提示。
    # 注意断言要带 `class="` 前缀：共享样式的注释里也有 `.completion-chip` 这样的字面量，
    # 只查裸类名会被注释满足，变成假通过。
    manual_html = client.get(f"/products/{manual_id}/edit").get_data(as_text=True)
    assert 'class="completion-chip"' not in manual_html
    assert 'class="field-flag"' not in manual_html

    # 补上品牌后提示消失（与筛选、徽标同源）。
    update_product_record(todo_id, "开单攒出测试品", "个", 1000, brand="金枪")
    cleared = client.get(f"/products/{todo_id}/edit").get_data(as_text=True)
    assert 'class="completion-chip"' not in cleared
    assert 'class="field-flag"' not in cleared


def test_badge_and_filter_hint_name_the_missing_field():
    """徽标 title 与筛选图例都要写清缺的是「品牌」，不能只说「待补全」。"""
    _seed_todo()
    html = create_app().test_client().get("/products/?quality=todo").get_data(as_text=True)
    assert "缺品牌" in html
    assert "自动移出本筛选" in html


def test_branded_order_product_drops_out_of_todo():
    """补了品牌就自动移出待补全（筛选与标记同源）。"""
    _seed_todo()
    with get_db() as conn:
        product_id = conn.execute("SELECT id FROM products WHERE name='开单攒出测试品'").fetchone()["id"]
    update_product_record(product_id, "开单攒出测试品", "个", 1000, brand="金枪")
    body = _results_body(create_app().test_client().get("/products/?quality=todo").get_data(as_text=True))
    assert "开单攒出测试品" not in body


# ---------------------------------------------------------------- 按拼音排序

def test_default_order_is_by_name_and_pinyin_sort_changes_it():
    _seed_products()
    client = create_app().test_client()
    default_body = _results_body(client.get("/products/").get_data(as_text=True))
    assert default_body.index(PINYIN_SECOND) < default_body.index(PINYIN_FIRST), "默认按名称（码位）排"

    sorted_body = _results_body(client.get("/products/?sort=pinyin").get_data(as_text=True))
    assert sorted_body.index(PINYIN_FIRST) < sorted_body.index(PINYIN_SECOND), "按拼音排：miehuoqi < shuiqiang"
    assert "按拼音排序" in client.get("/products/?sort=pinyin").get_data(as_text=True)


def test_pinyin_sort_survives_search_and_todo_filter():
    _seed_todo()
    client = create_app().test_client()
    html = client.get("/products/?quality=todo&sort=pinyin").get_data(as_text=True)
    body = _results_body(html)
    assert "开单攒出测试品" in body
    # 筛选 + 排序要能一起用，且链接里互相带上对方的参数（否则点一下就丢条件）。
    assert "quality=todo&sort=pinyin" in html


def test_view_links_keep_query_and_the_other_option():
    _seed_products()
    html = create_app().test_client().get("/products/?q=测试品&sort=pinyin").get_data(as_text=True)
    bar = html.split('class="list-batch-bar"', 1)[1].split("</div>\n</div>", 1)[0]
    assert "q=%E6%B5%8B%E8%AF%95%E5%93%81" in bar, "筛选/排序链接必须带上搜索词"
    assert bar.count("sort=pinyin") >= 2, "待补全与全部链接要保持当前排序"


def test_search_keeps_the_active_view_options():
    """在「待补全 + 按拼音排序」下搜索，不能把视图选项丢掉（客户页同款做法）。"""
    _seed_products()
    html = create_app().test_client().get("/products/?quality=todo&sort=pinyin").get_data(as_text=True)
    search = html.split('class="list-search"', 1)[1].split("</form>", 1)[0]
    assert 'name="quality" value="todo"' in search
    assert 'name="sort" value="pinyin"' in search


def test_unknown_quality_and_sort_values_fall_back_to_defaults():
    """白名单外的取值不能改变行为（也不进 SQL）。"""
    _seed_products()
    client = create_app().test_client()
    html = client.get("/products/?quality=drop-table&sort=;DROP").get_data(as_text=True)
    assert html.count(PINYIN_SECOND) > 0
    body = _results_body(html)
    assert body.index(PINYIN_SECOND) < body.index(PINYIN_FIRST), "非法排序值应回落默认排序"
    assert "productsViewBar" in html
