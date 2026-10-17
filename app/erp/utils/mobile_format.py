"""手机端显示口径：金额分档、金额格式、数量格式。

两条硬口径（2026-10-17 用户定案，见 `docs/需求/2026-10-17_安卓只读端架构决策.md` §5.2）：

- **列表只显示分档**（`无欠款 / <1千 / 1千–1万 / >1万`），精确金额只在详情页出现。
  理由不是技术限制：柜台前手机/平板会被隔壁客户看到，精确欠款数字会得罪人。
- **不显示任何成本或利润数字**（v15 已删成本链，手机是移动设备，进价泄露风险更高）。

金额格式与设计稿一致：整数不带小数、有小数才给两位；负数把负号放在 `¥` 前面。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from erp.utils.quantity import format_quantity, format_quantity_3dp

BAND_LABELS = {
    "b0": "无欠款",
    "b1": "<1千",
    "b2": "1千–1万",
    "b3": ">1万",
}


def _yuan_text(cents: int, *, force_two_decimals: bool) -> str:
    value = Decimal(int(cents or 0)) / Decimal(100)
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if not force_two_decimals and value == value.to_integral_value():
        return f"{sign}¥{int(value):,}"
    return f"{sign}¥{value:,.2f}"


def money_short(cents: int) -> str:
    """列表/汇总用：`¥3,980`、`-¥300`、`¥0`（整数不补 `.00`）。"""
    return _yuan_text(cents, force_two_decimals=False)


def money_exact(cents: int) -> str:
    """详情用：`¥3,980.00`、`-¥300.00`。"""
    return _yuan_text(cents, force_two_decimals=True)


def band(cents: int) -> dict:
    """金额分档：列表里只给这个，不给精确数字。"""
    value = int(cents or 0)
    if value <= 0:
        key = "b0"
    elif value < 100_000:
        key = "b1"
    elif value < 1_000_000:
        key = "b2"
    else:
        key = "b3"
    return {"key": key, "label": BAND_LABELS[key]}


def quantity_text(value, *, three_dp: bool = False, unit: str = "") -> str:
    if three_dp:
        return format_quantity_3dp(int(value or 0), unit)
    return format_quantity(value, unit)


def amount_band(cents: int) -> dict:
    """单据列表用的**中性**金额分档（按绝对值，没有「无欠款」这种欠款语义）。

    决策文档 §5.2 要求「列表分档、详情精确」；但客户列表的档位文案
    （无欠款 / <1千 / …）套在退货单 `-¥300` 上会显示成「无欠款」，是错的。
    所以单据列表用同一套区间、去掉欠款措辞。
    """
    value = abs(int(cents or 0))
    if value == 0:
        key = "b0"
    elif value < 100_000:
        key = "b1"
    elif value < 1_000_000:
        key = "b2"
    else:
        key = "b3"
    label = {"b0": "无金额", "b1": "<1千", "b2": "1千–1万", "b3": ">1万"}[key]
    return {"key": key, "label": label}


def short_date(iso_date: str) -> str:
    """`2026-10-17` → `10-17`（列表里省年份）。"""
    text = str(iso_date or "")
    return text[5:] if len(text) >= 10 else text


def month_day_label(iso_date: str) -> str:
    """`2026-10-17` → `10月17日`。"""
    text = str(iso_date or "")
    if len(text) < 10:
        return text
    return f"{int(text[5:7])}月{int(text[8:10])}日"


def weekday_label(iso_date: str) -> str:
    from datetime import date

    names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    try:
        day = date.fromisoformat(str(iso_date))
    except (TypeError, ValueError):
        return ""
    return names[day.weekday()]


def timestamp_label(value: str) -> str:
    """`2026-10-17T15:44:12` → `10月17日 15:44`；解析失败原样返回。"""
    text = str(value or "").strip()
    if not text:
        return "—"
    from datetime import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            moment = datetime.strptime(text[:19] if len(text) >= 19 else text, fmt)
        except ValueError:
            continue
        return f"{moment.month}月{moment.day}日 {moment.strftime('%H:%M')}"
    return text
