"""Agent 1 orchestration and the single signed-cent capacity ledger."""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from ledger_sense.data.models import BankTransaction, LedgerEntry
from ledger_sense.data.money import cents, from_cents, money_str

from .adjudication import Adjudicator, Question, StubAdjudicator
from .blocking import CandidateIndex
from .scoring import acceptance, interlock, normalize_currency, score_candidate, squash


def json_cell(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def cent_string(value: int) -> str:
    return money_str(from_cents(value))


def number_string(value: Decimal) -> str:
    return format(value, ".2f")


def fingerprint(bank: BankTransaction) -> tuple:
    # Statement dates, accounts, narratives and noise variants may differ between
    # duplicate postings. An explicit reference identifies the event; only fall
    # back to the name block key when that evidence is absent.
    return (cents(bank.amount), normalize_currency(bank.currency), squash(bank.reference_raw),
            "" if squash(bank.reference_raw) else squash(bank.counterparty_name_raw)[:4])


class CapacityLedger:
    """Both tiers must settle here; no accepted bank leg can overspend capacity."""

    def __init__(self, entries: Iterable[LedgerEntry]):
        self.entries = {entry.ledger_id: entry for entry in entries}
        self.remaining = {id: abs(cents(e.amount)) for id, e in self.entries.items()}
        self.parts = {id: [] for id in self.entries}
        self.signatures = {id: set() for id in self.entries}

    def residual(self, id: str) -> str:
        sign = -1 if cents(self.entries[id].amount) < 0 else 1
        return cent_string(sign * self.remaining[id])

    def settle(self, bank, candidate):
        id = candidate.ledger.ledger_id
        veto = interlock(bank, candidate.ledger)
        if veto:
            return veto, 0
        remaining, posted = self.remaining[id], abs(cents(bank.amount))
        signature = fingerprint(bank)
        # Identical half-payments can both settle while capacity suffices. An
        # identical retry that cannot fit (including an FX shortfall) is duplicate.
        if self.parts[id] and (remaining == 0 or posted > remaining):
            if signature in self.signatures[id]:
                return "duplicate_of_matched", 0
        if remaining == 0 and (posted != 0 or self.parts[id]):
            return "ledger_already_settled", 0
        # An accepted FX overage consumes no more than the book amount. Do not
        # silently clip an ordinary over-capacity partial or an exact payment.
        fx_overage = (candidate.features.amount == "fx"
                      and remaining == abs(cents(candidate.ledger.amount)))
        if posted > remaining and not fx_overage:
            return "insufficient_capacity", 0
        applied = min(posted, remaining)
        self.remaining[id] -= applied
        self.parts[id].append(bank.bank_txn_id)
        self.signatures[id].add(signature)
        return "", -applied if cents(candidate.ledger.amount) < 0 else applied

    def rows(self):
        for id, entry in sorted(self.entries.items()):
            original, remaining = cents(entry.amount), self.remaining[id]
            sign = -1 if original < 0 else 1
            parts = self.parts[id]
            full = remaining == 0 and bool(parts)
            yield {
                "ledger_id": id, "ledger_amount": cent_string(original),
                "matched_amount": cent_string(original - sign * remaining),
                "residual": cent_string(sign * remaining), "n_parts": len(parts),
                "bank_txn_ids": json_cell(parts), "fully_settled": full,
                "reason": "fully_settled" if full else "partially_settled" if parts else "never_settled",
            }


@dataclass
class MatchResult:
    outcomes: list[dict]
    settlements: list[dict]
    llm_calls: int
    llm_is_stub: bool

    @property
    def cheap_matches(self) -> int:
        return sum(row["status"] == "matched" and row["tier"] == "cheap" for row in self.outcomes)

    @property
    def cheap_match_rate(self) -> Decimal:
        return Decimal(self.cheap_matches) * 100 / len(self.outcomes) if self.outcomes else Decimal(0)


def match(ledger: Iterable[LedgerEntry], bank: Iterable[BankTransaction],
          adjudicator: Optional[Adjudicator] = None) -> MatchResult:
    index = CandidateIndex(ledger)
    capacity = CapacityLedger(index.entries.values())
    adjudicator = adjudicator if adjudicator is not None else StubAdjudicator()
    initial_calls = adjudicator.llm_calls
    states = {}
    for transaction in bank:
        id = transaction.bank_txn_id
        if id in states:
            raise ValueError(f"Duplicate bank_txn_id: {id}")
        known = squash(transaction.reference_raw) in index.by_ref
        ranked = sorted((score_candidate(transaction, e, known_reference=known)
                         for e in index.candidates(transaction)),
                        key=lambda c: (-c.score, c.ledger.ledger_id))
        best = ranked[0] if ranked else None
        margin = best.score - ranked[1].score if len(ranked) > 1 else best.score if best else Decimal(0)
        row = {
            "bank_txn_id": id, "status": "rejected", "relation": "", "ledger_id": "",
            "tier": "cheap", "score": number_string(best.score if best else Decimal(0)),
            "margin": number_string(margin), "reason": "no_candidate", "reason_detail": "Empty block",
            "matched_amount": "0.00", "residual_after": "", "candidates": json_cell([c.to_dict() for c in ranked]),
            "features": json_cell({"bank": transaction.to_row()}), "llm_model": "",
            "llm_confidence": "", "llm_is_stub": adjudicator.llm_is_stub,
        }
        if best:
            row["ledger_id"] = best.ledger.ledger_id
            row["features"] = feature_cell(transaction, best)
            row["reason"] = interlock(transaction, best.ledger) or ("below_threshold" if best.score < 45 else "ambiguous_evidence")
            row["reason_detail"] = "Cheap evidence did not satisfy acceptance and margin gates"
            row["status"] = "rejected" if best.score < 45 and not interlock(transaction, best.ledger) else "escalated"
            row["residual_after"] = capacity.residual(best.ledger.ledger_id)
        states[id] = (transaction, ranked, margin, row)

    # Score descending; the explicit bank-id tie-break is ascending. Input order
    # and Python's process-randomized hash never decide who wins capacity.
    ordered = sorted(states.values(), key=lambda s: (-s[1][0].score if s[1] else Decimal(0), s[0].bank_txn_id))
    questions = []
    for transaction, ranked, margin, row in ordered:
        if not ranked:
            continue
        best = ranked[0]
        gate = acceptance(best, margin, len(ranked))
        veto = interlock(transaction, best.ledger)
        if gate and not veto:
            apply_settlement(capacity, transaction, best, row, gate, "cheap")
        elif row["status"] == "escalated":
            questions.append(Question(transaction, tuple(ranked[:3]), row["reason"]))

    # One batch, only when there is work. Counter deltas measure actual provider
    # calls, not the number of local stub method invocations.
    verdicts = adjudicator.adjudicate(tuple(questions)) if questions else []
    offered = {q.bank.bank_txn_id: q for q in questions}
    decisions, seen = [], set()
    for verdict in verdicts:
        if verdict.bank_txn_id not in offered or verdict.bank_txn_id in seen:
            raise ValueError("Adjudicator returned an unknown or repeated bank id")
        seen.add(verdict.bank_txn_id)
        if not isinstance(verdict.confidence, Decimal) or not verdict.confidence.is_finite() or not 0 <= verdict.confidence <= 1:
            raise ValueError("Adjudicator confidence must be a finite Decimal in [0, 1]")
        question = offered[verdict.bank_txn_id]
        candidate = next((c for c in question.candidates if c.ledger.ledger_id == verdict.ledger_id), None)
        if candidate is None:
            raise ValueError("Adjudicator selected a ledger outside the offered top three")
        decisions.append((verdict, question, candidate))
    decisions.sort(key=lambda item: (-item[2].score, item[0].bank_txn_id))
    for verdict, question, candidate in decisions:
        row = states[verdict.bank_txn_id][3]
        row["llm_model"] = adjudicator.model
        row["llm_confidence"] = number_string(verdict.confidence)
        # A changed verdict cannot bypass either the original-best interlock or
        # the interlock on the proposed replacement. Capacity re-checks again.
        veto = interlock(question.bank, question.candidates[0].ledger) or interlock(question.bank, candidate.ledger)
        if veto:
            row["reason"] = veto
            row["reason_detail"] = "Agent 1 interlock veto after adjudication"
        elif verdict.accept:
            apply_settlement(capacity, question.bank, candidate, row, verdict.reason, "llm")
    calls = adjudicator.llm_calls - initial_calls
    if calls < 0:
        raise ValueError("Adjudicator provider-call counter went backwards")
    return MatchResult([states[id][3] for id in sorted(states)], list(capacity.rows()), calls, adjudicator.llm_is_stub)


def feature_cell(bank, candidate) -> str:
    # Carry input context through the file spine: routing need not import or read
    # another agent's inputs to identify amount, party, date or currency.
    return json_cell({**candidate.features.to_dict(), "bank": bank.to_row(),
                      "counterparty_key": squash(candidate.ledger.counterparty_name),
                      "amount_delta_cents": cents(bank.amount) - cents(candidate.ledger.amount),
                      "currency_normalized": normalize_currency(bank.currency)})


def apply_settlement(capacity, bank, candidate, row, reason, tier):
    failure, applied = capacity.settle(bank, candidate)
    row.update(ledger_id=candidate.ledger.ledger_id, tier=tier, score=number_string(candidate.score),
               features=feature_cell(bank, candidate), matched_amount=cent_string(applied),
               residual_after=capacity.residual(candidate.ledger.ledger_id))
    if failure:
        row.update(status="escalated", relation="duplicate" if failure == "duplicate_of_matched" else "",
                   reason=failure, reason_detail="Settlement refused by the shared capacity ledger")
    else:
        row.update(status="matched", relation="partial" if candidate.features.amount == "partial" else "exact",
                   reason=reason, reason_detail="Accepted against remaining ledger capacity")
