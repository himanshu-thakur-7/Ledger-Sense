"""One batched adjudication seam; bundled adjudicators never call a provider.

The stub is deliberately a transparent conservative heuristic, not simulated AI:
unique exact/fx amount, name >= .90, date >= .50, and non-reference margin >= 6.
It may rescue a wrong reference but never sees evidence outside the top three.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from ledger_sense.data.models import BankTransaction

from .scoring import ScoredCandidate, interlock


@dataclass(frozen=True)
class Question:
    bank: BankTransaction
    candidates: tuple[ScoredCandidate, ...]
    reason: str


@dataclass(frozen=True)
class Verdict:
    bank_txn_id: str
    ledger_id: str
    accept: bool
    confidence: Decimal
    reason: str


class Adjudicator(Protocol):
    llm_is_stub: bool
    llm_calls: int  # cumulative provider calls, incremented by the provider adapter
    model: str

    def adjudicate(self, questions: Sequence[Question]) -> Sequence[Verdict]: ...


class NoneAdjudicator:
    llm_is_stub = True
    model = "none"

    def __init__(self):
        self.llm_calls = 0

    def adjudicate(self, questions: Sequence[Question]) -> Sequence[Verdict]:
        return []


class StubAdjudicator(NoneAdjudicator):
    model = "deterministic-stub-v1"

    def adjudicate(self, questions: Sequence[Question]) -> Sequence[Verdict]:
        verdicts = []
        for question in questions:
            ranked = []
            for candidate in question.candidates:
                f = candidate.features
                if f.name is None or interlock(question.bank, candidate.ledger):
                    continue
                evidence = (f.amount_score * 30 + f.name * 20 + f.date * 7 + f.currency * 3) * 100 / 60
                ranked.append((evidence, candidate))
            ranked.sort(key=lambda pair: (-pair[0], pair[1].ledger.ledger_id))
            if not ranked:
                continue
            evidence, best = ranked[0]
            f = best.features
            if (f.amount in {"exact", "fx"} and f.name >= Decimal("0.90")
                    and f.date >= Decimal("0.50") and evidence >= 88
                    and (len(ranked) == 1 or evidence - ranked[1][0] >= 6)):
                verdicts.append(Verdict(question.bank.bank_txn_id, best.ledger.ledger_id,
                                        True, evidence / 100, "stub_amount_name_agreement"))
        return verdicts
