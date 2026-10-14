"""客户管理「全部 / 待补全 / 按拼音排序」（2026-10-14 第二轮：与商品页同步）。

背景：客户页此前把 chip 放在搜索行下面的 `.list-facet` 里，而商品页的同类 chip 在
批量动作条（`.list-batch-bar`）右侧 —— 两页不一致（用户 2026-10-14 第二轮截图）。
本轮客户页搬进批量动作条，并补上「按拼音排序」（此前只有商品页有）。

待补全口径（与商品侧同构，见 `services/master_data_quality.py`）：
来源 = 开单自动建档或批量导入（`origin IN ('order','import')`），且电话与地址都为空。
"""
from __future__ import annotations

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer

# 名字特意选成「按名称排」与「按拼音排」结果相反的一对：
#   名称（Unicode 码位）：水(U+6C34) < 灭(U+706D) → 水枪在前
#   拼音：miehuoqi… < shuiqiang… → 灭火器在前
PINYIN_FIRST = "灭火器客户排序测试"
PINYIN_SECOND = "水枪客户排序测试"


def _seed_customers():
    init_db()
    create_customer(PINYIN_FIRST, phone="13900000001")
    create_customer(PINYIN_SECOND, phone="13900000002")


def _seed_todo():
    """一个开单攒出（无电话地址，算待补全）+ 一个手工建档（同样空，不算）。"""
    init_db()
    create_customer("手工建档无联系方式测试客户")
    client = create_app().test_client()
    response = client.post(
        "/orders/create",
        data={
            "customer_name": "开单攒出测试客户",
            "product_name": "开单攒出测试商品",
            "unit": "个",
            "unit_price": "10",
            "quantity": "1",
            "status": "saved",
        },
    )
    assert response.status_code in (200, 302), response.status_code


def _results_body(html: str) -> str:
    """只取结果表体：页面顶部还有建议浮层等其它内容，不能整页搜名字。"""
    section = html.split('id="customersResults"', 1)[1]
    return section.split("<tbody>", 1)[1].split("</tbody>", 1)[0]


# ---------------------------------------------------------------- 按钮就位（与商品页同构）

def test_view_options_live_in_the_batch_bar_next_to_bulk_delete():
    """三个按钮放在「删除选中」右侧那块原本空着的区域，并带就地刷新钩子。"""
    init_db()
    html = create_app().test_client().get("/customers/").get_data(as_text=True)
    assert 'id="customersViewBar"' in html, "整条批量栏要能就地替换（否则筛选状态不会变）"
    assert "data-refresh-fragment" in html.split('class="list-batch-bar"', 1)[1][:160]
    bar = html.split('class="list-batch-bar"', 1)[1].split('<p class="list-legend"', 1)[0]
    assert "删除选中" in bar
    for label in ("档案筛选", "全部", "待补全", "按拼音排序"):
        assert label in bar, label
    assert bar.count("data-inplace") == 3, "全部 / 待补全 / 按拼音排序 都要走就地刷新"
    assert 'class="list-view-options"' in bar


def test_old_facet_row_is_gone_from_the_customer_page():
    """旧结构（搜索行下面的 .list-facet chip 行 + 表格上方孤零零的删除选中）必须消失。"""
    init_db()
    html = create_app().test_client().get("/customers/").get_data(as_text=True)
    assert 'class="list-facet"' not in html
    assert 'id="customersFacet"' not in html
    assert '<button class="btn btn-danger mb-3" type="button"' not in html


# ---------------------------------------------------------------- 待补全口径

def test_todo_filter_only_lists_order_archived_customers_without_contact():
    _seed_todo()
    client = create_app().test_client()
    body = _results_body(client.get("/customers/?quality=todo").get_data(as_text=True))
    assert "开单攒出测试客户" in body
    assert "手工建档无联系方式测试客户" not in body


def test_todo_chip_count_matches_the_same_definition():
    _seed_todo()
    client = create_app().test_client()
    with get_db() as conn:
        expected = conn.execute(
            "SELECT COUNT(*) AS c FROM customers WHERE deleted_at IS NULL"
            " AND origin IN ('order', 'import')"
            " AND TRIM(COALESCE(phone, ''))='' AND TRIM(COALESCE(address, ''))=''"
        ).fetchone()["c"]
    assert expected >= 1, "夹具应至少造出一个待补全客户"
    html = client.get("/customers/").get_data(as_text=True)
    assert f"待补全（{expected}）" in html


def test_todo_badge_marks_the_same_customers_the_filter_lists():
    _seed_todo()
    client = create_app().test_client()
    body = _results_body(client.get("/customers/").get_data(as_text=True))
    row = [line for line in body.split("<tr>") if "开单攒出测试客户" in line]
    assert row, "待补全客户应在全部列表里"
    assert "待补全</span>" in row[0], "列表里的待补全标记要与筛选同口径"


# ---------------------------------------------------------------- 按拼音排序

def test_default_order_is_by_name_and_pinyin_sort_changes_it():
    _seed_customers()
    client = create_app().test_client()
    default_body = _results_body(client.get("/customers/").get_data(as_text=True))
    assert default_body.index(PINYIN_SECOND) < default_body.index(PINYIN_FIRST), "默认按名称（码位）排"

    sorted_body = _results_body(client.get("/customers/?sort=pinyin").get_data(as_text=True))
    assert sorted_body.index(PINYIN_FIRST) < sorted_body.index(PINYIN_SECOND), "按拼音排：miehuoqi < shuiqiang"
    assert "按拼音排序" in client.get("/customers/?sort=pinyin").get_data(as_text=True)


def test_pinyin_sort_survives_search_and_todo_filter():
    _seed_todo()
    client = create_app().test_client()
    html = client.get("/customers/?quality=todo&sort=pinyin").get_data(as_text=True)
    body = _results_body(html)
    assert "开单攒出测试客户" in body
    # 筛选 + 排序要能一起用，且链接里互相带上对方的参数（否则点一下就丢条件）。
    assert "quality=todo&sort=pinyin" in html


def test_view_links_keep_query_and_the_other_option():
    _seed_customers()
    html = create_app().test_client().get("/customers/?q=排序测试&sort=pinyin").get_data(as_text=True)
    bar = html.split('class="list-batch-bar"', 1)[1].split('<p class="list-legend"', 1)[0]
    assert "q=%E6%8E%92%E5%BA%8F%E6%B5%8B%E8%AF%95" in bar, "筛选/排序链接必须带上搜索词"
    assert bar.count("sort=pinyin") >= 2, "待补全与全部链接要保持当前排序"


def test_search_keeps_the_active_view_options():
    """在「待补全 + 按拼音排序」下搜索，不能把视图选项丢掉（与商品页同款做法）。"""
    _seed_customers()
    html = create_app().test_client().get("/customers/?quality=todo&sort=pinyin").get_data(as_text=True)
    search = html.split('class="list-search"', 1)[1].split("</form>", 1)[0]
    assert 'name="quality" value="todo"' in search
    assert 'name="sort" value="pinyin"' in search


def test_unknown_quality_and_sort_values_fall_back_to_defaults():
    """白名单外的取值不能改变行为（也不进 SQL）。"""
    _seed_customers()
    client = create_app().test_client()
    html = client.get("/customers/?quality=drop-table&sort=;DROP").get_data(as_text=True)
    body = _results_body(html)
    assert body.index(PINYIN_SECOND) < body.index(PINYIN_FIRST), "非法排序值应回落默认排序"
    assert "customersViewBar" in html


# ---------------------------------------------------------------- 编辑页提示（与商品页对称）

def test_customer_edit_page_shows_the_completion_chip_and_field_flags():
    """2026-10-14「主次与分组」重构：客户编辑页与商品页对称 —— 页头 chip + 字段上的 ⚠。

    断言带 `class="` 前缀：共享样式的注释里也有 `.completion-chip` 字面量，
    只查裸类名会被注释满足，变成假通过。
    """
    _seed_todo()
    client = create_app().test_client()
    with get_db() as conn:
        todo_id = conn.execute("SELECT id FROM customers WHERE name='开单攒出测试客户'").fetchone()["id"]
        manual_id = conn.execute(
            "SELECT id FROM customers WHERE name='手工建档无联系方式测试客户'"
        ).fetchone()["id"]

    html = client.get(f"/customers/{todo_id}/edit").get_data(as_text=True)
    chip_parts = html.split('class="completion-chip"', 1)
    assert len(chip_parts) == 2, "待补全客户要在页头渲染 chip"
    assert "缺联系方式" in chip_parts[1].split("</span>", 1)[0]
    # 电话与地址是一对，两个都要标出来。
    assert html.count('class="field-flag"') == 2
    assert html.count('class="field-warn"') == 2

    # 手工建档即使同样没填联系方式也不算待补全 → 编辑页不提示。
    manual_html = client.get(f"/customers/{manual_id}/edit").get_data(as_text=True)
    assert 'class="completion-chip"' not in manual_html
    assert 'class="field-flag"' not in manual_html
