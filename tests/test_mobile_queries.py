"""手机端只读查询层的测试：4 类合并口径 + 批量余额对拍 + 四类单据详情。

对拍（同批客户，`customer_balances(ids)` 与 `get_customer_account_ledger()` 逐个相等）
是决策文档 §5.1 的硬要求：批量余额是新写的，口径只能靠对拍钉住。
"""
from __future__ import annotations

from datetime import date

from erp.db import get_db, init_db
from erp.services import accounting, readonly_queries
from mobile_fixtures import seed_all


def test_today_overview_covers_four_types_and_two_nets():
    data = seed_all()
    overview = readonly_queries.today_overview(data["day"])

    counts = {entry["type"]: entry["count"] for entry in overview["types"]}
    amounts = {entry["type"]: entry["amount_cents"] for entry in overview["types"]}
    assert counts == {"sale": 1, "return": 1, "purchase": 1, "purchase_return": 1}
    assert amounts == {"sale": 20_000, "return": -3_000, "purchase": 12_000, "purchase_return": 4_000}
    # 顺序固定：销售 → 退货 → 拿货 → 退拿货
    assert [entry["type"] for entry in overview["types"]] == [
        "sale", "return", "purchase", "purchase_return"
    ]
    # 两段净额，不做四类总计
    assert overview["net_sale_cents"] == 17_000
    assert overview["net_purchase_cents"] == 8_000
    assert overview["document_count"] == 4
    assert len(overview["events"]) == 4
    assert {event["order_type"] for event in overview["events"]} == set(counts)
    assert all(event["order_no"] for event in overview["events"])


def test_today_overview_keeps_zero_rows_for_absent_types():
    init_db()
    overview = readonly_queries.today_overview("2026-01-02")
    assert [entry["count"] for entry in overview["types"]] == [0, 0, 0, 0]
    assert overview["net_sale_cents"] == 0
    assert overview["net_purchase_cents"] == 0
    assert overview["events"] == []


def test_customer_balances_match_authoritative_ledger():
    data = seed_all()
    # 再加上期初/收款/调整都各不相同的第三个客户，避免「碰巧相等」
    third = accounting.create_customer("手机端对拍客户", opening_balance_cents=-12345)
    accounting.create_order_from_typed_rows(
        third, "MD202610160009",
        [{"product_name": "对拍商品", "unit": "个", "quantity": "7", "unit_price_yuan": "3.33"}],
        status="saved", order_date="2026-10-16",
    )
    accounting.add_payment(third, 1000, payment_date="2026-10-16", method="转账")
    accounting.add_adjustment(third, -700, "对拍调整")

    ids = [data["customer_id"], data["other_customer_id"], third]
    batch = readonly_queries.customer_balances(ids)
    for customer_id in ids:
        ledger = accounting.get_customer_account_ledger(customer_id)
        assert batch[customer_id] == ledger["current_balance_cents"], customer_id
    # 空输入不查库也不报错
    assert readonly_queries.customer_balances([]) == {}


def test_customer_balances_skips_deleted_and_unknown_ids():
    data = seed_all()
    with get_db() as conn:
        conn.execute(
            "UPDATE customers SET deleted_at='2026-10-18 00:00:00' WHERE id=?",
            (data["other_customer_id"],),
        )
    batch = readonly_queries.customer_balances([data["other_customer_id"], 999999])
    assert batch == {}


def test_customer_list_orders_by_debt_desc():
    data = seed_all()
    result = readonly_queries.list_customers("")
    first = result["customers"][0]
    assert first["id"] == data["customer_id"]
    # 期初 10000 + 销售 20000 + 退货 -3000 + 调整 2000 - 收款 5000 - 拿货 12000 + 退拿货 4000
    assert first["balance_cents"] == 10000 + 20000 - 3000 + 2000 - 5000 - 12000 + 4000
    assert result["customers"][1]["balance_cents"] == 5000  # 第二客户 1 × 50（跨月那单）


def test_customer_detail_has_balance_three_parts_and_recent_entries():
    data = seed_all()
    detail = readonly_queries.customer_detail(data["customer_id"])
    assert detail is not None
    assert detail["opening_balance_cents"] == 10000
    assert detail["change_cents"] == detail["current_balance_cents"] - 10000
    assert detail["entries"], "客户详情必须有最近流水"
    # 2026-10-18：页面默认露 3 条、其余页内展开，所以后端一次取 30 条（原来是 10）。
    assert len(detail["entries"]) <= 30
    # 2026-10-22：折叠改成**显示层**的事 —— 后端一次给 20 条，手机端默认露 3 条、
    # 点「展开全部」就地铺开。原来后端硬切 3 条，前端「展开」按钮永远出不来
    # （3 条不比 3 条多），这是用户实测报的 bug。
    # 上限 20 而不是无限：防止一个老客户几千张单把响应撑大。
    assert len(detail["documents"]) <= 20
    # 要保证取的是**最新**的几张：按业务日期倒序，且首条不早于末条。
    if len(detail["documents"]) >= 2:
        dates = [document["business_date"] for document in detail["documents"]]
        assert dates == sorted(dates, reverse=True), "历史单据必须按业务日期倒序"
    assert readonly_queries.customer_detail(999999) is None


def test_list_documents_type_and_date_filters():
    data = seed_all()
    today_docs = readonly_queries.list_documents(
        start_date=data["day"], end_date=data["day"]
    )["documents"]
    assert len(today_docs) == 4

    sale_only = readonly_queries.list_documents(
        order_type="sale", start_date=data["day"], end_date=data["day"]
    )["documents"]
    assert [document["order_type"] for document in sale_only] == ["sale"]

    # 全部时间能跨月看到 2026-09-01 那张
    everything = readonly_queries.list_documents()["documents"]
    assert any(document["business_date"] == "2026-09-01" for document in everything)

    # 类型 + 日期叠加后计数正确
    september = readonly_queries.list_documents(
        order_type="sale", start_date="2026-09-01", end_date="2026-09-30"
    )["documents"]
    assert len(september) == 1
    assert september[0]["business_date"] == "2026-09-01"


def test_document_detail_for_all_four_types():
    data = seed_all()
    sale = readonly_queries.document_detail("sale", data["sale_id"])
    assert sale["total_cents"] == 20_000 and len(sale["items"]) == 1
    assert sale["items"][0]["quantity"] == "2"

    returned = readonly_queries.document_detail("return", data["return_id"])
    assert returned["total_cents"] == -3_000

    purchase = readonly_queries.document_detail("purchase", data["purchase_id"])
    assert purchase["total_cents"] == 12_000
    assert purchase["items"][0]["quantity_3dp"] is True
    assert purchase["items"][0]["quantity"] == "3000"

    purchase_return = readonly_queries.document_detail("purchase_return", data["purchase_return_id"])
    assert purchase_return["total_cents"] == 4_000

    # 类型不对 / id 不存在都返回 None（路由层据此给 404）
    assert readonly_queries.document_detail("unknown", data["sale_id"]) is None
    assert readonly_queries.document_detail("sale", 999999) is None


def test_product_prices_show_customer_price_and_last_deal():
    data = seed_all()
    result = readonly_queries.product_prices(data["customer_id"])
    product = result["products"][0]
    # 开单学习出专属价（最后一次手输单价 30 元那单）
    assert product["customer_price_cents"] == 3_000
    assert product["last_price_cents"] == 3_000
    assert result["customer"]["id"] == data["customer_id"]

    # 换成第二客户：只有最近成交价、没有专属价（先清掉开单学习出的那一行，
    # 否则测不到「无专属价」这条显示分支）
    with get_db() as conn:
        conn.execute("DELETE FROM customer_prices WHERE customer_id=?", (data["other_customer_id"],))
    other = readonly_queries.product_prices(data["other_customer_id"])
    other_product = other["products"][0]
    assert other_product["customer_price_cents"] is None
    assert other_product["last_price_cents"] == 5_000


def test_mobile_range_reuses_desktop_rules():
    data = seed_all()

    class Args(dict):
        pass

    today = readonly_queries.resolve_mobile_range(Args())
    assert today["mode"] == "day"
    assert today["start_date"] == today["end_date"]

    month = readonly_queries.resolve_mobile_range(Args(date_mode="month", year="2026", month="10"))
    assert (month["start_date"], month["end_date"]) == ("2026-10-01", "2026-10-31")

    year = readonly_queries.resolve_mobile_range(Args(date_mode="year", year="2026"))
    assert (year["start_date"], year["end_date"]) == ("2026-01-01", "2026-12-31")

    day_range = readonly_queries.resolve_mobile_range(
        Args(date_mode="day", start_date="2026-10-01", end_date="2026-10-17")
    )
    assert (day_range["start_date"], day_range["end_date"]) == ("2026-10-01", "2026-10-17")

    everything = readonly_queries.resolve_mobile_range(Args(date_mode="all"))
    assert everything["start_date"] == everything["end_date"] == ""
    assert everything["label"] == "全部时间"

    # 起止反着选：与桌面端一样自动换过来（两端都含）
    swapped = readonly_queries.resolve_mobile_range(
        Args(date_mode="day", start_date="2026-10-17", end_date="2026-10-01")
    )
    assert (swapped["start_date"], swapped["end_date"]) == ("2026-10-01", "2026-10-17")

    # ---- 2026-10-23 新增：当周（ISO 周一→周日）+ 更多筛选的自定义区间 ----
    week = readonly_queries.resolve_mobile_range(Args(date_mode="week"))
    assert week["mode"] == "week"
    start = date.fromisoformat(week["start_date"])
    end = date.fromisoformat(week["end_date"])
    # 必须是日历定义的整周：周一开头、周日结尾、正好 7 天
    assert start.weekday() == 0, f"当周应以周一开始，实际 {start} (weekday={start.weekday()})"
    assert end.weekday() == 6, f"当周应以周日结束，实际 {end}"
    assert (end - start).days == 6
    # 今天必须落在这一周内（不能算成「最近 7 天」那种滑动窗口）
    assert start <= date.today() <= end

    # `custom` 与 `day` 的 range 同义：三个参数原样透传到起止
    custom = readonly_queries.resolve_mobile_range(
        Args(date_mode="custom", start_date="2026-09-01", end_date="2026-09-30")
    )
    assert (custom["start_date"], custom["end_date"]) == ("2026-09-01", "2026-09-30")
    assert data["day"]
