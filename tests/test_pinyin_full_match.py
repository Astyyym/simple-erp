"""P3（2026-10-14）：拼音匹配从「只有首字母」扩到「首字母 + 整串全拼」。

背景：`pinyin_initials` 只存首字母，`消防水枪QZ3.5/7.5` → `xfsqqz3.5/7.5`，
于是 `xf` / `XFS` 命中，而门店更常手输的完整拼音 `xiaofang` / `shuiqiang` 一个都命中不了。
本轮新增派生列 `pinyin_full`（整串小写全拼，幂等加列 + 只补空值回填，不升 schema 版本），
所有匹配点改走 `utils/pinyin.py` 的 `name_match_clause()` / `name_match_params()`。

本文件按「真实入口 → 真的能搜到」逐个断言，不只看 SQL 文本；另锁老库回填、幂等与
「没有匹配点退回只剩首字母的旧写法」。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import (
    create_customer,
    create_order_from_typed_rows,
    create_product,
    update_product_record,
)
from erp.services.purchases import create_purchase_order
from erp.utils.pinyin import normalize_name_query, pinyin_full


def _initials_and_full(table: str, name: str) -> tuple[str, str]:
    with get_db() as conn:
        row = conn.execute(
            f"SELECT pinyin_initials, pinyin_full FROM {table} WHERE name=?", (name,)
        ).fetchone()
    return row["pinyin_initials"], row["pinyin_full"]


# ---------------------------------------------------------------- 派生值本身

def test_pinyin_full_uses_whole_syllables_not_initials():
    assert pinyin_full("消防水枪QZ3.5/7.5") == "xiaofangshuiqiangqz3.5/7.5"
    assert pinyin_full("星辰宾馆") == "xingchenbinguan"
    assert pinyin_full("ABC水管") == "abcshuiguan"  # 英文原样保留、中文全拼
    assert pinyin_full("") == ""


def test_product_writes_both_pinyin_columns_and_refreshes_on_rename():
    init_db()
    product_id = create_product("消防水枪", "个")
    assert _initials_and_full("products", "消防水枪") == ("xfsq", "xiaofangshuiqiang")
    update_product_record(product_id, "消防水带", "个")
    assert _initials_and_full("products", "消防水带") == ("xfsd", "xiaofangshuidai")


def test_customer_writes_both_pinyin_columns():
    init_db()
    create_customer("星辰宾馆")
    assert _initials_and_full("customers", "星辰宾馆") == ("xcbg", "xingchenbinguan")


# ---------------------------------------------------------------- 商品入口

def _seed_products():
    init_db()
    create_product("消防水枪QZ3.5/7.5", "个")
    create_product("Y型过滤器DN50", "个")


def test_product_list_search_matches_full_pinyin_and_still_initials():
    _seed_products()
    client = create_app().test_client()
    for query in ("xiaofang", "shuiqiang", "xiaofangshuiqiang", "xf", "消防"):
        html = client.get(f"/products/?q={query}").get_data(as_text=True)
        assert "消防水枪QZ3.5/7.5" in html, query


def test_product_suggestions_apis_match_full_pinyin():
    _seed_products()
    client = create_app().test_client()
    for path in ("/products/api/suggestions", "/orders/api/products", "/purchases/api/products"):
        response = client.get(f"{path}?q=xiaofang")
        assert response.is_json, path
        payload = response.get_json()
        names = [row["name"] if isinstance(row, dict) else row for row in payload]
        assert "消防水枪QZ3.5/7.5" in names, path


# ---------------------------------------------------------------- 客户入口

def _seed_customers():
    init_db()
    create_customer("星辰宾馆")
    create_customer("消防器材张老板")


def test_customer_list_and_suggestions_match_full_pinyin():
    _seed_customers()
    client = create_app().test_client()
    html = client.get("/customers/?q=xingchen").get_data(as_text=True)
    assert "星辰宾馆" in html and "消防器材张老板" not in html
    assert "星辰宾馆" in client.get("/customers/api/suggestions?q=xingchen").get_json()


def test_entry_page_customer_autocomplete_matches_full_pinyin():
    _seed_customers()
    client = create_app().test_client()
    for path in ("/orders/api/customers", "/purchases/api/objects"):
        names = [row["name"] for row in client.get(f"{path}?q=xingchenbinguan").get_json()]
        assert "星辰宾馆" in names, path


def test_analytics_and_summary_customer_scope_match_full_pinyin():
    """分析页客户筛选改联想输入后（2026-10-17）：全拼匹配落在候选接口上，页面引用该接口。"""
    _seed_customers()
    client = create_app().test_client()
    html = client.get("/analytics/").get_data(as_text=True)
    assert "/orders/api/customers?q=" in html
    names = [row["name"] for row in client.get("/orders/api/customers?q=xingchen").get_json()]
    assert "星辰宾馆" in names

    from erp.utils.pdf import resolve_summary_customer_ids

    with get_db() as conn:
        expected = conn.execute("SELECT id FROM customers WHERE name='星辰宾馆'").fetchone()["id"]
    assert resolve_summary_customer_ids("xingchen", "", "") == [int(expected)]


# ---------------------------------------------------------------- 筛选框（用户 2026-10-14 明确要求一并支持）

def _seed_order_for_customer():
    init_db()
    customer_id = create_customer("星辰宾馆")
    product_id = create_product("星辰商品", "个")
    create_order_from_typed_rows(
        customer_id,
        "MD202610140001",
        [{"product_id": product_id, "product_name": "星辰商品", "unit": "个",
          "quantity": "1", "unit_price_yuan": "10"}],
        status="saved",
        order_date="2026-10-14",
    )
    return customer_id


def test_order_list_customer_filter_matches_full_pinyin():
    """单据管理筛选框输完整拼音也要能筛出该客户的单据（此前只按名称/首字母）。"""
    _seed_order_for_customer()
    html = create_app().test_client().get("/orders/?customer=xingchen").get_data(as_text=True)
    assert "MD202610140001" in html


def test_order_list_filter_conditions_include_full_pinyin_column():
    """筛选条件本身（未解析成单一客户时的回落分支）也必须是三列匹配。"""
    source = (Path(__file__).resolve().parents[1] / "app" / "erp" / "routes" / "orders.py").read_text(encoding="utf-8")
    order_side = source.split("def order_side_conditions")[1].split("def purchase_side_conditions")[0]
    purchase_side = source.split("def purchase_side_conditions")[1].split("order_where, order_params")[0]
    assert "name_match_clause" in order_side and "name_match_params" in order_side
    assert "name_match_clause" in purchase_side and "name_match_params" in purchase_side


# ---------------------------------------------------------------- 原单候选（两张关联框的候选接口）

def test_purchase_source_candidates_match_customer_full_pinyin():
    init_db()
    customer_id = create_customer("星辰宾馆")
    product_id = create_product("拿货候选商品", "个")
    # 正式拿货单要求商品已启用库存。

    create_purchase_order(
        "NH202610140001", "2026-10-14",
        [{"product_id": product_id, "quantity": "1", "unit_cost_yuan": "10"}],
        customer_id=customer_id, request_key="pinyin-source",
    )
    client = create_app().test_client()
    rows = client.get("/purchases/api/purchase_sources?q=xingchen").get_json()
    assert [row["order_no"] for row in rows] == ["NH202610140001"]


def test_return_source_candidates_match_customer_full_pinyin():
    init_db()
    customer_id = create_customer("星辰宾馆")
    product_id = create_product("退货候选商品", "个")

    create_order_from_typed_rows(
        customer_id,
        "MD202610140002",
        [{"product_id": product_id, "product_name": "退货候选商品", "unit": "个",
          "quantity": "1", "unit_price_yuan": "30"}],
        status="saved",
        order_date="2026-10-14",
    )
    rows = create_app().test_client().get("/orders/api/return_sources?q=xingchen").get_json()["orders"]
    assert [row["order_no"] for row in rows] == ["MD202610140002"]


# ---------------------------------------------------------------- 老库回填与幂等

def test_init_db_backfills_full_pinyin_only_for_empty_values():
    init_db()
    create_customer("星辰宾馆")
    with get_db() as conn:
        conn.execute("UPDATE customers SET pinyin_full='' WHERE name='星辰宾馆'")
        conn.execute("INSERT INTO customers(name, pinyin_initials, pinyin_full) VALUES ('手工客户', 'sgkh', 'keep-me')")
    init_db()
    assert _initials_and_full("customers", "星辰宾馆")[1] == "xingchenbinguan"
    assert _initials_and_full("customers", "手工客户")[1] == "keep-me"


def test_no_match_site_falls_back_to_initials_only():
    """防止以后新增搜索点只写首字母：全仓不应再有「只比 pinyin_initials」的旧写法。

    `utils/pinyin.py` 本身是构造这个片段的模块（docstring 里会同时提到两列），跳过它。
    """
    root = Path(__file__).resolve().parents[1] / "app" / "erp"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "pinyin.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "pinyin_initials LIKE" in line and "pinyin_full" not in line:
                offenders.append(f"{path.name}:{number}")
    assert offenders == []


# ---------------------------------------------------------------- 输入法分隔符（2026-10-14 用户实测）
#
# 用户截图现场：销售单商品名里输入 `xiao'fang`，候选只有输入法自己的窗口，应用侧一个都不出。
# 根因不是没做全拼，而是**检索串没归一**：输入法在音节之间插了隔音符号（直/弯撇号），
# 而派生列是无分隔符形态（`xiaofangshuidai…`），`%xiao'fang%` 永远命中不了。
# 同类变体还有空格与全角字母。

def test_normalize_name_query_strips_ime_separators_and_fullwidth():
    assert normalize_name_query("xiao'fang") == "xiaofang"
    assert normalize_name_query("xiao’fang") == "xiaofang"      # 弯撇号
    assert normalize_name_query("xiao‘fang") == "xiaofang"      # 左弯撇号
    assert normalize_name_query("xiao`fang") == "xiaofang"      # 反引号
    assert normalize_name_query("xiao fang") == "xiaofang"      # 空格
    assert normalize_name_query("xiao　fang") == "xiaofang"     # 全角空格
    assert normalize_name_query("ｘｉａｏｆａｎｇ") == "xiaofang"  # 全角字母
    assert normalize_name_query(" 消防 ") == "消防"
    assert normalize_name_query("") == ""
    assert normalize_name_query(None) == ""


def test_separator_only_query_matches_nothing_instead_of_everything():
    """只输一个 `'` 时不能归一成空串——`LIKE '%%'` 会把全库列出来。"""
    _seed_products()
    client = create_app().test_client()
    payload = client.get("/orders/api/products?q=" + quote("'")).get_json()
    assert payload == []
    html = client.get("/products/?q=" + quote("'")).get_data(as_text=True)
    assert "消防水枪QZ3.5/7.5" not in html


def test_entry_product_search_matches_full_pinyin_written_with_ime_separator():
    """开单页商品名输入框（`/orders/api/products`）必须认 `xiao'fang` 这种输入法写法。"""
    _seed_products()
    client = create_app().test_client()
    for query in ("xiao'fang", "xiao’fang", "xiao fang", "ｘｉａｏｆａｎｇ", "xiao'fangshuiqiang"):
        payload = client.get("/orders/api/products?q=" + quote(query)).get_json()
        names = [row["name"] for row in payload]
        assert "消防水枪QZ3.5/7.5" in names, query


def test_customer_entry_search_matches_full_pinyin_with_ime_separator():
    _seed_customers()
    client = create_app().test_client()
    for path in ("/orders/api/customers", "/purchases/api/objects", "/customers/api/suggestions"):
        for query in ("xing'chen", "xing chen", "xingchenbinguan"):
            payload = client.get(f"{path}?q=" + quote(query)).get_json()
            names = [row["name"] if isinstance(row, dict) else row for row in payload]
            assert "星辰宾馆" in names, (path, query)


def test_list_page_and_order_filter_accept_ime_separator():
    """列表页搜索框与单据管理筛选框走的是同一条归一（快照名/电话地址的 LIKE 也一起归一）。"""
    _seed_order_for_customer()
    client = create_app().test_client()
    assert "星辰宾馆" in client.get("/customers/?q=" + quote("xing'chen")).get_data(as_text=True)
    assert "MD202610140001" in client.get("/orders/?customer=" + quote("xing'chen")).get_data(as_text=True)
    assert "MD202610140001" in client.get("/orders/?q=" + quote("xing'chen")).get_data(as_text=True)
