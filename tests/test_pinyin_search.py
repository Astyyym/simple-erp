"""E7：商品拼音首字母（纯派生字段，用于商品搜索）。

背景（复核 #19）：`products.pinyin_initials` 与 `api_products` 的 LIKE 查询早已存在，
但全仓库从未写入过该字段，拼音搜索实际不可用。本轮收敛到 `utils/pinyin.py` 并接入
四处：商品新增、商品编辑、商品导入、开单自动建档；init_db 再幂等回填旧空值。

本文件锁「派生值真的被写进库」，与 test_legacy_startup 的回填断言互补。
"""
from erp import create_app
from erp.db import get_db, init_db
from erp.utils.pinyin import pinyin_initials
from erp.services.accounting import create_customer, create_product, update_product_record


def _initials_of(product_id: int) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT pinyin_initials FROM products WHERE id=?", (product_id,)
        ).fetchone()
    return row["pinyin_initials"]


def test_pinyin_initials_generates_first_letters():
    assert pinyin_initials("消防泵") == "xfb"
    assert pinyin_initials("ABC水管") == "abcsg"  # 英文保留、中文取首字母（水→s，管→g）
    assert pinyin_initials("") == ""


def test_create_product_writes_pinyin_initials():
    init_db()
    from erp.utils.pinyin import pinyin_initials as build

    product_id = create_product("灭火器", "MFZ", "个", 5000, build("灭火器"))
    assert _initials_of(product_id) == "mhq"


def test_update_product_refreshes_pinyin_initials_on_rename():
    init_db()
    from erp.utils.pinyin import pinyin_initials as build

    product_id = create_product("应急灯", "", "个", 3000, build("应急灯"))
    assert _initials_of(product_id) == "yjd"
    update_product_record(product_id, "安全帽", "", "个", 3000)
    assert _initials_of(product_id) == "aqm"


def test_search_matches_pinyin_initials():
    init_db()
    from erp.utils.pinyin import pinyin_initials as build

    create_product("水带", "65mm", "米", 800, build("水带"))
    client = create_app().test_client()
    html = client.get("/products/?q=sd").get_data(as_text=True)
    assert "水带" in html


def test_auto_created_product_from_order_gets_pinyin():
    """开单时自动建档的商品也应写拼音（services/accounting.py 自动建档路径）。"""
    init_db()
    customer_id = create_customer("拼音建档客户")
    from erp.services.accounting import create_order_from_typed_rows

    create_order_from_typed_rows(
        customer_id, "MD202610010001",
        [{"product_name": "消火栓", "unit": "个", "unit_price_yuan": "100", "quantity": "1"}],
        status="saved", order_date="2026-10-01",
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT pinyin_initials FROM products WHERE name='消火栓'"
        ).fetchone()
    assert row is not None and row["pinyin_initials"] == "xhs"
