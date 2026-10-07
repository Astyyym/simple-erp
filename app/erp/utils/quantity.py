"""Display quantities without changing stored values or input validation."""

from decimal import Decimal


_COUNT_UNITS = frozenset({"个", "件", "套", "只", "台", "瓶", "箱", "包", "组", "卷", "根", "支", "把", "张", "枚", "辆", "双", "盒", "桶", "袋"})


def format_quantity(value: str | int | Decimal | None, unit: str = "") -> str:
    """Hide count-unit padding, but never round historical fractional counts.

    Measures and unknown units retain every stored nonzero decimal digit.
    """
    if value is None:
        return "—"
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("数量必须为有限数值")
    if number == 0:
        return "0"
    if (unit or "").strip() in _COUNT_UNITS and number == number.to_integral_value():
        return format(number.to_integral_value(), "f")
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_quantity_3dp(value: int | None, unit: str = "") -> str:
    """Format integer thousandths exactly, without floating-point division."""
    if value is None:
        return "—"
    number = Decimal(value)
    sign, digits, exponent = number.as_tuple()
    return format_quantity(Decimal((sign, digits, exponent - 3)), unit)
