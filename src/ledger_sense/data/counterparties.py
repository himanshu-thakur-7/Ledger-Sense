"""Counterparty universe (spec §4.3): seeded from ``seed`` only, shared across passes.

~800 counterparties, generated once per ``seed`` regardless of ``pass_number`` or
``n_cases``, so a rule learned against pass 1's counterparties still applies to pass
2 (law L5).
"""

from dataclasses import dataclass
from typing import List

from .names import generate_canonical_name
from .rng import counterparty_rng

DEFAULT_UNIVERSE_SIZE = 800


@dataclass(frozen=True)
class Counterparty:
    counterparty_id: str
    canonical_name: str


def build_counterparty_universe(
    seed: int, size: int = DEFAULT_UNIVERSE_SIZE
) -> List[Counterparty]:
    """Deterministic list of counterparties, seeded from ``seed`` alone.

    Draws only from :func:`ledger_sense.data.rng.counterparty_rng` -- never touches
    the per-pass case stream -- so the result is identical for pass 1 and pass 2 at a
    given seed, independent of ``n_cases``.
    """
    rng = counterparty_rng(seed)
    counterparties = []
    seen_names = set()
    for i in range(size):
        name = generate_canonical_name(rng, i)
        # Extremely unlikely collision given the word pools; if it happens, keep
        # drawing from the same stream (still fully deterministic) until unique.
        while name in seen_names:
            name = generate_canonical_name(rng, i)
        seen_names.add(name)
        counterparties.append(
            Counterparty(counterparty_id=f"CP-{i:04d}", canonical_name=name)
        )
    return counterparties
