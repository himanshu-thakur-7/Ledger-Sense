"""The messy-case taxonomy (spec §4.2), exact rates.

Rates are kept as ``Decimal`` (not ``float``) throughout -- the same "never float"
discipline the money module follows (law L3) applies to every number this package
computes, not only currency amounts. Rates must sum to exactly 100 -- checked at
import time so a typo here fails loudly instead of silently drifting the histogram.
"""

import random
from collections import OrderedDict
from decimal import ROUND_FLOOR, Decimal
from typing import List

DEFECT_RATES = OrderedDict(
    [
        ("clean", Decimal("57.0")),
        ("wrong_reference", Decimal("7.0")),
        ("partial_payment", Decimal("6.0")),
        ("out_of_order", Decimal("6.0")),
        ("duplicate", Decimal("5.0")),
        ("missing_reference", Decimal("5.0")),
        ("fx_rounding", Decimal("4.0")),
        ("malformed", Decimal("2.5")),
        ("negative_amount", Decimal("2.0")),
        ("orphan_bank", Decimal("2.0")),
        ("orphan_ledger", Decimal("2.0")),
        ("zero_amount", Decimal("1.5")),
    ]
)

_RATE_SUM = sum(DEFECT_RATES.values(), Decimal("0"))
if _RATE_SUM != Decimal("100.0"):
    raise AssertionError(f"DEFECT_RATES must sum to 100.0, got {_RATE_SUM}")

# Defects that plant an actual guardrail bait row (spec §4.2 notes).
GUARDRAIL_BAIT_DEFECTS = frozenset({"negative_amount", "zero_amount"})

# Overlay-only defect shape, not part of the documented §4.2 mix (locked BOARD.md Q3).
OVERLAY_DEFECT = "fee_offset"


def defect_counts(n_cases: int) -> "OrderedDict[str, int]":
    """Exact per-defect case counts for ``n_cases``, via largest-remainder rounding.

    Using exact stratified counts (rather than sampling each case independently from
    the rate distribution) means the defect histogram matches §4.2 as closely as
    integer division allows -- no multinomial sampling noise to tolerate. At the
    reference n_cases=25000 every rate divides evenly, so the histogram is exact.
    """
    n = Decimal(n_cases)
    raw = {name: (rate * n) / Decimal(100) for name, rate in DEFECT_RATES.items()}
    floors = {
        name: int(value.to_integral_value(rounding=ROUND_FLOOR))
        for name, value in raw.items()
    }
    remainder = n_cases - sum(floors.values())
    # Largest fractional remainder gets the leftover units, ties broken by the fixed
    # taxonomy order above -- deterministic, no RNG involved.
    fractions = sorted(
        DEFECT_RATES.keys(), key=lambda name: (raw[name] - floors[name]), reverse=True
    )
    counts = OrderedDict((name, floors[name]) for name in DEFECT_RATES)
    for name in fractions[:remainder]:
        counts[name] += 1
    assert sum(counts.values()) == n_cases
    return counts


def build_defect_sequence(rng: random.Random, n_cases: int) -> List[str]:
    """Exact-count defect labels for ``n_cases``, shuffled by the case stream.

    Shuffling (rather than the counts' fixed order) is what makes defect assignment
    depend on ``(seed, pass_number)`` while keeping the histogram exact.
    """
    counts = defect_counts(n_cases)
    sequence = []
    for name, count in counts.items():
        sequence.extend([name] * count)
    rng.shuffle(sequence)
    return sequence
