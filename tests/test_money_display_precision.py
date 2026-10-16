"""Display micro-yuan money values at two visible decimal places without rounding the payload."""
import pytest

from erp.utils.money import micro_to_yuan


@pytest.mark.parametrize("micro, expected", [
    (None, "—"), (0, "0.00"), (1, "0.00"), (5_000, "0.01"),
    (1_234_567, "1.23"), (1_995_000, "2.00"), (-1_005_000, "-1.01"),
])
def test_internal_micro_cost_formats_as_two_visible_decimal_places(micro, expected):
    assert micro_to_yuan(micro) == expected
