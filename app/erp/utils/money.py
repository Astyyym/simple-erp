from decimal import Decimal, ROUND_HALF_UP


def yuan_to_cents(value: str | int | float | Decimal) -> int:
    decimal_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(decimal_value * 100)


def cents_to_yuan(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def line_subtotal_cents(quantity: str | Decimal, unit_price_cents: int) -> int:
    subtotal = Decimal(str(quantity)) * Decimal(unit_price_cents)
    return int(subtotal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
