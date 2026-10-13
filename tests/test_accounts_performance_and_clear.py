import uuid

from erp import create_app
from erp.db import get_db, init_db
from erp.services.accounting import add_adjustment, add_payment, create_customer, create_order_from_typed_rows, customer_balance_cents


def test_accounts_bulk_balance_query_matches_individual_balance_calculation():
    init_db()
    suffix = uuid.uuid4().hex[:8]
    customer_id = create_customer(f"批量余额客户{suffix}", opening_balance_cents=10000)
    create_order_from_typed_rows(
        customer_id,
        f"BAL-{suffix}",
        [{"product_name": f"余额商品{suffix}", "unit": "个", "unit_price_yuan": "30", "quantity": "2"}],
        status="saved",
        order_date="2026-07-11",
    )
    add_payment(customer_id, 1500)
    add_adjustment(customer_id, -500, "测试核减")

    expected = customer_balance_cents(customer_id)
    html = create_app().test_client().get("/accounts/").get_data(as_text=True)

    assert f"批量余额客户{suffix}" in html
    assert f"{expected / 100:.2f}" in html


def test_order_entry_row_tools_live_below_the_table_and_keep_clear_all():
    """行工具条（2026-10-11 改版）：新增/删除/一键清空放在表格正下方、随表横向滚动。

    改版前两按钮在卡片右下角、离表格远；用户要求挪到表格下方（产品名称单元格下方）。
    这里断言按钮顺序与位置都在 `.order-table-scroll` 内，且「一键清空」仍是同一份实现。
    """
    client = create_app().test_client()

    for path, tbody in (("/orders/new", "orderBody"), ("/orders/return/new", "orderBody"),
                        ("/purchases/new", "purchaseRows"), ("/purchases/return/new", "returnRows")):
        html = client.get(path).get_data(as_text=True)
        # 工具条在明细表之后、滚动容器结束之前
        scroll_open = html.index('class="order-table-scroll"')
        table_close = html.index("</table>", scroll_open)
        tools_pos = html.index('class="row-tools"', table_close)
        assert table_close < tools_pos, path
        assert 'id="rowTools"' in html, path
        # 三个按钮同排，顺序：新增一行 / 删除一行 / 一键清空
        add_pos = html.index("新增一行", tools_pos)
        remove_pos = html.index("删除一行", tools_pos)
        clear_pos = html.index("一键清空", tools_pos)
        assert add_pos < remove_pos < clear_pos, path
        assert 'onclick="addRow()"' in html, path
        assert 'onclick="removeRow()"' in html, path
        assert 'onclick="clearAllRows()"' in html, path
        # 删除按钮初始 disabled（只剩 1 行时不可删）
        assert 'id="removeRowButton" onclick="removeRow()" disabled' in html, path

        assert "function removeRow()" in html, path
        assert "function syncRowToolState()" in html, path
        # 行为改变（2026-10-13 换页提示行）：判定改为「只数数据行」，
        # 换页提示行混在 #orderBody 里不能算录入行，否则剩 1 行时删除按钮还是可点。
        assert f"DataRows(document.getElementById('{tbody}')).length <= 1" in html, path
        assert "function clearAllRows()" in html, path
        assert "while (tbody.rows.length > 1)" in html, path
        # 右侧动作区只留保存按钮，不再重复行按钮
        for action_id in ("orderActions", "purchaseActions", "returnActions"):
            if f'id="{action_id}"' in html:
                block = html[html.index(f'id="{action_id}"'):]
                block = block[:block.index("</div>")]
                assert "一键清空" not in block and "新增一行" not in block and "删除一行" not in block, (path, action_id)
