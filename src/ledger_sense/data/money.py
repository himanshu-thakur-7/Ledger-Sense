"""Money helpers (law L3): Decimal in memory, 2-decimal string in CSV, never float.

Every amount that enters or leaves this package goes through :func:`to_money` or
:func:`money_str`. No function in ``ledger_sense.data`` may hold a monetary value in a
``float`` at any point -- not as an intermediate, not as a return value.
"""

from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")


def to_money(value) -> Decimal:
    """Coerce ``value`` (int, str, or Decimal) to a 2-decimal-place Decimal.

    Never accepts ``float`` -- that would already have introduced binary rounding
    error before we could quantize it away.
    """
    if isinstance(value, float):
        raise TypeError(
            "to_money() refuses float input (law L3) -- pass an int, str, or Decimal"
        )
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def cents(value: Decimal) -> int:
    """Convert a 2dp Decimal to integer cents (exact, no float roundtrip)."""
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def from_cents(value_cents: int) -> Decimal:
    """Convert integer cents back to a 2dp Decimal."""
    return to_money(Decimal(value_cents) / Decimal(100))


def money_str(value: Decimal) -> str:
    """Render a Decimal as the fixed 2-decimal string the CSVs store."""
    return str(to_money(value))
