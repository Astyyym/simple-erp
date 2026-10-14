"""P6 每联打印行数（6/8/10/12）的行几何契约。

**这是排版层证据，不是打印机实打证据**：这里用真实 WeasyPrint 渲染真实打印模板，
量每页内容底边与「可打印底边」（逻辑页高 − @page 上下外边距）的余量，钉住三件事：

1. 每个档位 N 行必须落在**一联**里（含页脚），N+1 行才换第二联；
2. 每个档位都要容得下**至少一行品名折行**（门店商品名经常折行，折一行约 +4.44mm）；
3. 12 行档位必须比 10 行收得更紧——沿用 0.9mm 内边距时 12 行直接溢出成两联。

打印件最终是否落在纸面正确位置，仍须目标打印机实打验收（本文件不能替代）。
"""
import re
from pathlib import Path

import jinja2
import pytest
from weasyprint import HTML

from erp.config import (
    SLIP_ROW_PADDING_MM,
    SLIP_SIZE,
    SLIP_SIZE_CHOICES,
    _merge_defaults,
)

PROJECT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = PROJECT / "app" / "erp" / "templates" / "orders"
PRINT_TEMPLATE = (TEMPLATE_DIR / "print_template.html").read_text(encoding="utf-8")
MM_PER_PX = 25.4 / 96
LONG_NAME = "沟槽式机械三通DN100×65内外丝带法兰连接件"  # 明显折行的长品名
WRAPPED_LINE_MM = 4.44  # 实测：一行品名折行多占的高度

_margin = re.search(r"margin:\s*([\d.]+)mm\s+([\d.]+)mm;", PRINT_TEMPLATE)
assert _margin, "打印模板的 @page 外边距必须显式可读，几何契约要靠它算可打印区"
# 盒树坐标以**纸面左上角**为原点（第一个子盒 position_y 就是上外边距），
# 所以「可打印底边」= 逻辑页高 − 下外边距，而不是减上下两个外边距。
PAGE_BOTTOM_MARGIN_MM = float(_margin.group(1))
PAGE_HEIGHT_MM = float(_merge_defaults({})["order_pdf_page_height_mm"])
PRINTABLE_BOTTOM_MM = PAGE_HEIGHT_MM - PAGE_BOTTOM_MARGIN_MM


def _env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)))
    env.globals["cents_to_yuan"] = lambda cents: f"{cents / 100:.2f}"
    return env


def _max_bottom_mm(box, offset_y=0.0, include_self=False):
    """页面盒树里最靠下的内容底边（mm）。页面盒自身的高度就是内容框高，不能算进去。"""
    bottom = offset_y
    height = getattr(box, "height", None)
    if include_self and isinstance(height, (int, float)):
        bottom = max(bottom, offset_y + height)
    for child in (getattr(box, "children", None) or []):
        position_y = getattr(child, "position_y", None)
        margin_top = getattr(child, "margin_top", 0) or 0
        child_offset = offset_y if not isinstance(position_y, (int, float)) else position_y + margin_top
        bottom = max(bottom, _max_bottom_mm(child, child_offset, include_self=True))
    return bottom


def _page_text(page):
    texts = []

    def walk(box):
        text = getattr(box, "text", "")
        if text and text.strip():
            texts.append(text.strip())
        for child in (getattr(box, "children", None) or []):
            walk(child)

    walk(page._page_box)
    return " ".join(texts)


def render(row_count, slip_size, *, long_names=0, padding_mm=None):
    names = [
        LONG_NAME if index <= long_names else f"几何闸阀{index:03d}"
        for index in range(1, row_count + 1)
    ]
    order = {
        "order_no": "MD202610140001", "order_date": "2026-10-14", "order_type": "sale",
        "customer_name": "几何契约客户", "notes": "", "total_amount_cents": row_count * 10000,
        "status": "saved",
    }
    items = [
        {"product_name": name, "unit": "只", "quantity": "2",
         "unit_price_cents": 5000, "subtotal_cents": 10000}
        for name in names
    ]
    html = _env().get_template("print_template.html").render(
        order=order, items=items, config=_merge_defaults({}), document_title="销售清单",
        slip_size=slip_size,
        slip_row_padding_mm=SLIP_ROW_PADDING_MM[slip_size] if padding_mm is None else padding_mm,
    )
    document = HTML(string=html, base_url=str(PROJECT)).render()
    return document, names, html


def facts(document, names):
    result = []
    for page in document.pages:
        text = _page_text(page)
        result.append({
            "rows": [name for name in names if name in text],
            "bottom_mm": _max_bottom_mm(page._page_box) * MM_PER_PX,
            "slip_total": "本张合计" in text,
            "order_total": "合计" in text and "本张合计" not in text,
            "notice": "续打在第" in text,
            "footer": "订货电话" in text,
        })
    return result


@pytest.mark.parametrize("slip_size", SLIP_SIZE_CHOICES)
def test_each_tier_fits_exactly_its_row_count_on_one_slip(slip_size):
    """N 行（含页脚）落在一联内；N+1 行才换第二联，且第一联恰好 N 行。"""
    pages = facts(*render(slip_size, slip_size)[:2])
    assert len(pages) == 1, f"{slip_size} 行档位：{slip_size} 行应打一联，实际 {len(pages)} 联"
    assert pages[0]["rows"] == [f"几何闸阀{index:03d}" for index in range(1, slip_size + 1)]
    assert pages[0]["order_total"] is True and pages[0]["footer"] is True
    assert pages[0]["notice"] is False
    slack = PRINTABLE_BOTTOM_MM - pages[0]["bottom_mm"]
    assert slack >= 0.5, f"{slip_size} 行档位：一联只剩 {slack:.2f}mm，太贴边"

    pages = facts(*render(slip_size + 1, slip_size)[:2])
    assert len(pages) == 2, f"{slip_size} 行档位：{slip_size + 1} 行应换第二联"
    assert len(pages[0]["rows"]) == slip_size
    assert pages[0]["slip_total"] is True and pages[0]["notice"] is True and pages[0]["footer"] is False
    assert pages[1]["order_total"] is True and pages[1]["footer"] is True
    assert pages[1]["notice"] is False


@pytest.mark.parametrize("slip_size", SLIP_SIZE_CHOICES)
def test_each_tier_absorbs_at_least_one_wrapped_product_name(slip_size):
    """满档位时，只要有一行品名折行就必须仍然是一联——门店商品名经常折行。"""
    pages = facts(*render(slip_size, slip_size, long_names=1)[:2])
    assert len(pages) == 1, (
        f"{slip_size} 行档位：满档位 + 一行品名折行（+{WRAPPED_LINE_MM}mm）就溢出了第二联，"
        f"说明该档内边距收得不够"
    )
    slack = PRINTABLE_BOTTOM_MM - pages[0]["bottom_mm"]
    assert slack >= 0, f"{slip_size} 行档位：折行后余量 {slack:.2f}mm"


def test_the_12_row_tier_must_pad_tighter_than_the_10_row_tier():
    """12 行沿用 10 行的 0.9mm 内边距会直接溢出成两联——这是 0.4mm 档位存在的理由。"""
    pages = facts(*render(12, 12, padding_mm=0.9)[:2])
    assert len(pages) == 2, "12 行 @0.9mm 本应溢出；若这条失败说明行高变了，需重新标定档位"
    assert SLIP_ROW_PADDING_MM[12] < SLIP_ROW_PADDING_MM[SLIP_SIZE]


def test_tier_padding_map_covers_every_choice():
    assert set(SLIP_ROW_PADDING_MM) == set(SLIP_SIZE_CHOICES)
    assert all(0 < padding <= 0.9 for padding in SLIP_ROW_PADDING_MM.values())
    assert SLIP_SIZE == 10
