"""Deterministic RNG stream derivation (law L5).

Two independent streams, both built on the stdlib ``random.Random`` (deterministic
given a seed, no wall-clock or OS entropy involved):

- **Counterparty universe** -- seeded from ``seed`` alone. The ~800-name universe is
  therefore identical across pass 1 and pass 2 (and across any n_cases), which is what
  lets a rule learned on pass 1 still apply to pass 2's counterparties.
- **Case stream** -- seeded from ``(seed, pass_number)``. Everything about *which*
  cases occur, in what order, with which defects and noise, comes from this stream so
  that pass 2 is a genuinely different draw, not a copy.

Seed derivation uses plain integer arithmetic only -- never Python's built-in
``hash()``, which is salted per-process (``PYTHONHASHSEED``) and would break
byte-identical reruns (law L4).
"""

import random

# Large odd multiplier for a simple, dependency-free integer mixing function.
_MULTIPLIER = 1_000_003


def derive_seed(*parts: int) -> int:
    """Deterministically fold integers into a single RNG seed.

    Pure arithmetic (no hash(), no os.urandom) so it reproduces identically on every
    Python process, platform, and run.
    """
    acc = 0
    for part in parts:
        acc = (acc * _MULTIPLIER + int(part)) & 0xFFFFFFFFFFFFFFFF
    return acc


def counterparty_rng(seed: int) -> random.Random:
    """The counterparty-universe stream: seeded from ``seed`` only."""
    return random.Random(derive_seed(seed))


def case_rng(seed: int, pass_number: int) -> random.Random:
    """The case stream: seeded from ``(seed, pass_number)``."""
    return random.Random(derive_seed(seed, pass_number))
