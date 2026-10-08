"""Batch E：一致性小项（2026-10-08 第二轮修复）。

本文件覆盖：
- E1 时间模式统一（type=month，YYYY-MM 兼容旧数字参数）
- E2 状态多选筛选 + 多选翻页保参（复核 #17）
- E3 汇总提示去重
"""
from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product, mark_order_printed


def _seed_many(count, prefix="MD20261001", spec="S1"):
    """造若干销售单，覆盖多种状态。spec 要唯一，避免跨调用撞商品身份唯一索引。

    prefix 形如 MD20260901 → 业务日期取 2026-09-01，便于按月筛选断言。
    """
    digits = "".join(ch for ch in prefix if ch.isdigit())
    order_date = f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    pid = create_product("列表筛选商品", spec, "个", 1000)
    cid = create_customer(f"列表筛选客户{spec}")
    ids = []
    for index in range(count):
        order_id = create_order_from_typed_rows(
            cid, f"{prefix}{index + 1:04d}",
            [{"product_id": pid, "product_name": "列表筛选商品", "unit": "个",
              "unit_price_yuan": "10", "quantity": "1"}],
            status="saved", order_date=order_date, order_type="return" if index % 2 else "sale",
        )
        ids.append(order_id)
    return cid, ids


# --------------------------- E2：多选翻页保参 ---------------------------

def test_multi_value_order_type_survives_pagination():
    """复核 #17：/orders/?order_type=sale&order_type=return 翻页链接必须保留两个类型。"""
    init_db()
    _seed_many(60)  # > page_size(50) 才会出翻页链接
    html = create_app().test_client().get(
        "/orders/?order_type=sale&order_type=return"
    ).get_data(as_text=True)
    # 翻页链接应同时带两个 order_type。
    assert "order_type=sale" in html
    assert "order_type=return" in html
    # 找下一页链接，确认两个值都在同一个 URL 里。
    import re
    next_links = re.findall(r'href="(/orders/\?[^"]*page=\d+[^"]*)"', html)
    assert next_links, "应存在翻页链接"
    assert any(link.count("order_type=") >= 2 for link in next_links), next_links


def test_multi_value_status_survives_pagination():
    init_db()
    _seed_many(60)
    html = create_app().test_client().get(
        "/orders/?status=saved&status=void"
    ).get_data(as_text=True)
    import re
    next_links = re.findall(r'href="(/orders/\?[^"]*page=\d+[^"]*)"', html)
    assert next_links
    assert any(link.count("status=") >= 2 for link in next_links), next_links


# --------------------------- E2：状态筛选语义 ---------------------------

def test_status_filter_narrows_results():
    init_db()
    pid = create_product("状态筛选商品", "S1", "个", 1000)
    cid = create_customer("状态筛选客户")
    saved_id = create_order_from_typed_rows(
        cid, "MD202610010001",
        [{"product_id": pid, "product_name": "状态筛选商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-01",
    )
    printed_id = create_order_from_typed_rows(
        cid, "MD202610010002",
        [{"product_id": pid, "product_name": "状态筛选商品", "unit": "个", "unit_price_yuan": "10", "quantity": "1"}],
        status="saved", order_date="2026-10-01",
    )
    mark_order_printed(printed_id)
    client = create_app().test_client()

    only_saved = client.get("/orders/?status=saved").get_data(as_text=True)
    assert "MD202610010001" in only_saved
    assert "MD202610010002" not in only_saved

    only_printed = client.get("/orders/?status=printed").get_data(as_text=True)
    assert "MD202610010002" in only_printed
    assert "MD202610010001" not in only_printed

    both = client.get("/orders/?status=saved&status=printed").get_data(as_text=True)
    assert "MD202610010001" in both and "MD202610010002" in both

    # 空 = 全部（不改变现行为）
    everything = client.get("/orders/").get_data(as_text=True)
    assert "MD202610010001" in everything and "MD202610010002" in everything


def test_status_filter_block_present_in_filter_card():
    init_db()
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    assert 'name="status" id="orderStatusdraft"' in html
    assert 'name="status" id="orderStatusvoid"' in html
    assert "单据状态（可多选）" in html


# --------------------------- E1：月份输入 ---------------------------

def test_month_mode_uses_month_input_and_filters():
    init_db()
    _seed_many(3, prefix="MD20260901", spec="SEP")
    client = create_app().test_client()
    html = client.get("/orders/").get_data(as_text=True)
    assert 'type="month"' in html
    assert 'name="start_month_raw"' in html

    # YYYY-MM 参数生效：只筛 2026-09，应包含 9 月单、不包含 10 月单。
    _seed_many(3, prefix="MD20261001", spec="OCT")
    html_month = client.get("/orders/?date_mode=month&start_month_raw=2026-09&end_month_raw=2026-09").get_data(as_text=True)
    assert "MD202609010001" in html_month
    assert "MD202610010001" not in html_month


def test_legacy_numeric_month_params_still_work():
    init_db()
    _seed_many(3, prefix="MD20260901", spec="LEGACY-SEP")
    html = create_app().test_client().get(
        "/orders/?date_mode=month&start_year=2026&start_month=9&end_year=2026&end_month=9"
    ).get_data(as_text=True)
    assert "MD202609010001" in html


# --------------------------- E3：汇总提示去重 ---------------------------

def test_summary_hint_not_duplicated():
    init_db()
    _seed_many(1)
    html = create_app().test_client().get("/orders/").get_data(as_text=True)
    # 同一条 summary_hint 只出现一次；导出按钮带 title。
    assert html.count("导出汇总表将按筛选范围内有单据的客户分段打印") == 1
    assert "不受上方「订单类型」筛选影响" in html
