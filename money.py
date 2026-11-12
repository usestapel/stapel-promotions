"""Money rules of stapel-promotions: Decimal inside, strings on the wire.

The one rounding rule of the package lives in :func:`percent_of` and
:func:`allocate` (spec §4.5); nothing else rounds.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

#: Decimal places of every money column (currencies with exponent 2 only).
DECIMAL_PLACES = 2
QUANTUM = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)

#: Promotion values / thresholds (a price-sized column, listings' precision).
VALUE_MAX_DIGITS = 12
#: Redemption amounts and anything summed over a cart (orders' total width).
AMOUNT_MAX_DIGITS = 18

#: Strict upper bound of a value column.
VALUE_LIMIT = Decimal(10) ** (VALUE_MAX_DIGITS - DECIMAL_PLACES)
#: Strict upper bound of any wire amount this package accepts.
AMOUNT_LIMIT = Decimal(10) ** (AMOUNT_MAX_DIGITS - DECIMAL_PLACES)


class MalformedAmount(ValueError):
    """A wire amount that is not a finite decimal string with at most 2 dp."""


def parse(raw, *, signed: bool = False) -> Decimal:
    """A wire amount as a Decimal. Floats are refused: they are already wrong."""
    if isinstance(raw, (float, bool)) or raw is None:
        raise MalformedAmount(f"amount must be a decimal string, got {raw!r}")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise MalformedAmount(f"amount is not a decimal: {raw!r}") from None
    if not value.is_finite():
        raise MalformedAmount(f"amount is not finite: {raw!r}")
    if not signed and value < 0:
        raise MalformedAmount(f"amount must be non-negative: {raw!r}")
    if value != value.quantize(QUANTUM):
        raise MalformedAmount(f"amount has more than {DECIMAL_PLACES} decimal places: {raw!r}")
    if abs(value) >= AMOUNT_LIMIT:
        raise MalformedAmount(f"amount {raw!r} is out of range")
    return value.quantize(QUANTUM)


def to_wire(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(QUANTUM))


def percent_of(base: Decimal, percent: Decimal) -> Decimal:
    """``base × percent / 100`` rounded half-up to the cent, never above *base*."""
    if base <= 0 or percent <= 0:
        return ZERO
    return min(base, (base * percent / HUNDRED).quantize(QUANTUM, rounding=ROUND_HALF_UP))


def allocate(amount: Decimal, bases: list[Decimal]) -> list[Decimal]:
    """Split *amount* across *bases* proportionally, in whole cents.

    Each share is rounded down; the leftover cents go one by one to the
    largest remainders (ties: lower index). No share exceeds its base and
    the shares sum to ``min(amount, sum(bases))`` exactly.
    """
    total = sum(bases, ZERO)
    amount = min(amount, total)
    if amount <= 0 or total <= 0:
        return [ZERO for _ in bases]
    shares: list[Decimal] = []
    remainders: list[tuple[Decimal, int]] = []
    for index, base in enumerate(bases):
        exact = amount * base / total
        share = min(base, exact.quantize(QUANTUM, rounding=ROUND_DOWN))
        shares.append(share)
        remainders.append((exact - share, index))
    leftover = int(((amount - sum(shares, ZERO)) / QUANTUM).to_integral_value())
    for _, index in sorted(remainders, key=lambda r: (-r[0], r[1])):
        if leftover <= 0:
            break
        if shares[index] + QUANTUM <= bases[index]:
            shares[index] += QUANTUM
            leftover -= 1
    # A second pass only matters when a line was full; any line with room takes the rest.
    index = 0
    while leftover > 0 and index < len(bases):
        room = int(((bases[index] - shares[index]) / QUANTUM).to_integral_value())
        take = min(room, leftover)
        shares[index] += QUANTUM * take
        leftover -= take
        index += 1
    return shares


__all__ = [
    "AMOUNT_LIMIT",
    "AMOUNT_MAX_DIGITS",
    "DECIMAL_PLACES",
    "MalformedAmount",
    "QUANTUM",
    "VALUE_LIMIT",
    "VALUE_MAX_DIGITS",
    "ZERO",
    "allocate",
    "parse",
    "percent_of",
    "to_wire",
]
