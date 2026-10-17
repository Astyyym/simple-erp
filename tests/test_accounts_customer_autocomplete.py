"""账款管理 / 往来对账的客户字段改「输入即模糊匹配 + 候选点选」（2026-10-17 用户要求）。

用户原话：「账款管理，请输入客户，这里就只能匹配客户，他不能输入，然后来进行模糊匹配」；
决策：输入时就实时模糊匹配、下拉列出候选供选择；只输入名称、没点候选时直接提示去候选里选。

契约（本文件钉住）：
1. 客户 / 往来对象字段 = combobox 输入框 + 候选浮层 + hidden `customer_id`，不再是全量 `<select>`；
2. 候选来源复用 `/orders/api/customers`（名称子串 / 拼音首字母 / 全拼），前端必须把 id 写回 hidden；
3. 提交值仍是 `customer_id`：收款 / 调整 / 作废 / 对账入口与对账快照都按 id 工作；
4. 只输入名称、没点候选 → 明确提示，不猜客户、不静默出空结果。
"""
from erp import create_app
from erp.db import init_db
from erp.services.accounting import create_customer, create_order_from_typed_rows, create_product


def _seed():
    pid = create_product("联想客户商品", "个")
    cid = create_customer("联想测试客户", opening_balance_cents=0)
    create_order_from_typed_rows(
        cid,
        "MD202610170901",
        [{"product_id": pid, "product_name": "联想客户商品", "unit": "个",
          "unit_price_yuan": "50", "quantity": "2"}],
        status="saved",
        order_date="2026-10-17",
    )
    return cid


def test_accounts_customer_field_is_combobox_not_full_select():
    init_db()
    _seed()
    html = create_app().test_client().get("/accounts/").get_data(as_text=True)
    # 输入框 + 候选浮层 + hidden id
    assert 'id="ledgerCustomerInput"' in html and 'name="customer"' in html
    assert 'id="ledger_customer_suggestions"' in html and 'role="listbox"' in html
    assert 'id="ledgerCustomer" name="customer_id"' in html
    # 候选来源：复用单据管理页的客户接口（返回 id，供写回 hidden）
    assert "/orders/api/customers?q=" in html
    assert "ErpOrderEntry.bindAutocomplete" in html
    # 旧的全量下拉必须消失（否则用户仍被逼着在长列表里滚）
    assert 'id="ledgerCustomer" name="customer_id" required' not in html
    assert "请选择客户" not in html


def test_accounts_unpicked_customer_name_shows_hint_without_guessing():
    init_db()
    _seed()
    html = create_app().test_client().get("/accounts/?customer=联想").get_data(as_text=True)
    assert "请从下拉候选里选择客户后再查询流水。" in html
    # 没选中客户 → 不渲染流水表，只显示提示 + 客户余额表
    assert 'id="ledgerRows"' not in html
    assert "客户当前余额" in html
    # 空态与结果区共用同一个 fragment id（否则首次查询的就地刷新会静默丢结果）
    assert 'id="accountsResults" data-refresh-fragment' in html
    # 输入内容回显，用户不必重打
    assert 'value="联想"' in html


def test_accounts_selected_customer_echoes_name_and_renders_ledger():
    init_db()
    cid = _seed()
    html = create_app().test_client().get(f"/accounts/?customer_id={cid}").get_data(as_text=True)
    assert 'id="accountsResults"' in html
    assert f'id="ledgerCustomer" name="customer_id" value="{cid}"' in html
    assert 'value="联想测试客户"' in html
    assert "请从下拉候选里选择客户后再查询流水。" not in html


def test_accounts_unknown_customer_id_warns_instead_of_blank():
    init_db()
    _seed()
    html = create_app().test_client().get("/accounts/?customer_id=999999").get_data(as_text=True)
    assert "该客户不存在或已删除，请重新从候选里选择。" in html
    assert 'id="ledgerRows"' not in html


def test_reconciliation_object_field_is_combobox_not_full_select():
    init_db()
    _seed()
    html = create_app().test_client().get("/accounts/reconciliation").get_data(as_text=True)
    assert 'id="reconCustomerInput"' in html and 'name="customer"' in html
    assert 'id="recon_customer_suggestions"' in html
    assert 'id="reconCustomer" name="customer_id"' in html
    assert "/orders/api/customers?q=" in html
    assert "请选择往来对象" not in html


def test_reconciliation_unpicked_object_name_shows_hint():
    init_db()
    _seed()
    response = create_app().test_client().get("/accounts/reconciliation?customer=联想")
    assert "请从下拉候选里选择往来对象后再查询。" in response.get_data(as_text=True)
    assert response.status_code == 400


def test_reconciliation_selected_object_echoes_name():
    init_db()
    cid = _seed()
    response = create_app().test_client().get(f"/accounts/reconciliation?customer_id={cid}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'id="reconCustomer" name="customer_id" value="{cid}"' in html
    assert 'value="联想测试客户"' in html


def test_reconciliation_empty_state_is_refresh_fragment():
    """未选中对象时的空态必须与结果面板同 id，否则首次查询的就地刷新会静默丢结果。"""
    init_db()
    _seed()
    html = create_app().test_client().get("/accounts/reconciliation").get_data(as_text=True)
    assert 'id="reconPanel" data-refresh-fragment' in html
