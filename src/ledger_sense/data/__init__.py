"""Synthetic data model and generator (spec §4).

Deterministic given ``(seed, pass_number, n_cases)`` (law L4): two RNG streams (law
L5) -- a counterparty universe seeded from ``seed`` alone, and a per-pass case stream
seeded from ``(seed, pass_number)`` -- produce ``ledger.csv``, ``bank.csv``, and the
ground-truth ``match_links.csv``. See ``BOARD.md`` (W1 card) and PRD §4 for the full
contract; ``python -m ledger_sense.data --help`` for the CLI.
"""

from .counterparties import Counterparty, build_counterparty_universe
from .generator import GeneratedDataset, GeneratorConfig, GenerationSummary, generate
from .models import BankTransaction, LedgerEntry, MatchLink

__all__ = [
    "Counterparty",
    "build_counterparty_universe",
    "GeneratedDataset",
    "GeneratorConfig",
    "GenerationSummary",
    "generate",
    "BankTransaction",
    "LedgerEntry",
    "MatchLink",
]
