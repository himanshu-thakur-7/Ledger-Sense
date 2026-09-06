"""§6.1-6.3 -- the five categories, the ordered bank-side classifier, the
book-side rule, and pair-and-suppress.

Nothing here reads the ground-truth MatchLink table or imports anything from
``ledger_sense.matching`` -- every input is a plain value already sitting in
a ``match_outcomes.csv`` / ``ledger_settlements.csv`` row (see
``ledger_sense.routing.io``).
"""

from decimal import Decimal
from typing import Optional

CATEGORIES = (
    "duplicate",
    "amount_mismatch",
    "timing",
    "unidentified_counterpart",
    "suspect_posting",
)

# §6.2 -- "Use the same 0.70 name floor as §5.4 -- a routing rule claiming 'we
# know the counterparty' must never be more generous than the matching rule
# making the same claim."
NAME_FLOOR = Decimal("0.70")

# §4.2 / §6.2's book-side note: "reuse Agent 1's 15-85% partial band."
PARTIAL_BAND_FLOOR_PCT = Decimal("15")


def _decimal_or_none(value) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def classify_bank(reason: str, relation: str, features: dict) -> tuple[str, str]:
    """§6.2 -- ordered, first-hit. ``features`` is the parsed ``features`` JSON
    cell from a ``match_outcomes.csv`` row (absent keys, e.g. for a
    ``no_candidate`` row, simply fail every feature-based condition and fall
    through to rule 7)."""
    if reason in ("anomalous_amount", "currency_conflict"):
        return "suspect_posting", f"bank-rule-1: reason={reason}"
    if relation == "duplicate":
        return "duplicate", "bank-rule-2: relation=duplicate"
    if reason == "ledger_already_settled":
        return "amount_mismatch", "bank-rule-3: reason=ledger_already_settled"

    amount_class = features.get("amount")
    name = _decimal_or_none(features.get("name"))
    date = _decimal_or_none(features.get("date"))

    if amount_class == "conflict" and name is not None and name >= NAME_FLOOR:
        return "amount_mismatch", f"bank-rule-4: amount=conflict,name={name}>=0.70"
    if (
        date is not None
        and date < Decimal("0.50")
        and name is not None
        and name >= NAME_FLOOR
        and amount_class in ("exact", "fx")
    ):
        return "timing", f"bank-rule-5: date={date}<0.50,name={name}>=0.70,amount={amount_class}"
    if amount_class == "partial" and name is not None and name >= NAME_FLOOR:
        return "timing", f"bank-rule-6: amount=partial,name={name}>=0.70"
    return "unidentified_counterpart", "bank-rule-7: no earlier condition matched"


def classify_book(settlement_reason: str, ledger_amount: Decimal, residual: Decimal) -> tuple[str, str]:
    """Book side (§6.2's closing note): ``never_settled`` -> timing;
    ``partially_settled`` residual >=15% of the ledger amount -> timing,
    <15% -> amount_mismatch."""
    if settlement_reason == "never_settled":
        return "timing", "book-rule: never_settled"
    denominator = abs(ledger_amount)
    ratio_pct = (abs(residual) / denominator * 100) if denominator != 0 else Decimal(100)
    if ratio_pct >= PARTIAL_BAND_FLOOR_PCT:
        return "timing", f"book-rule: partially_settled residual_ratio={ratio_pct:.2f}%>=15%"
    return "amount_mismatch", f"book-rule: partially_settled residual_ratio={ratio_pct:.2f}%<15%"


def select_pairs(bank_top_candidate: dict, unclaimed_ledger_ids: set) -> dict:
    """§6.3 pair-and-suppress.

    ``bank_top_candidate`` maps every *unresolved* bank_txn_id to its top
    candidate ledger_id (``""`` when Agent 1 found no candidate at all).
    ``unclaimed_ledger_ids`` is the set of ledger ids Agent 1 never settled
    against any bank line (``ledger_settlements.csv`` reason ==
    ``never_settled``).

    A bank subject whose top candidate is itself unclaimed pairs with it --
    but a ledger id can only pair with one bank subject, so where several bank
    subjects share the same top candidate, ties break by bank_txn_id order
    (ascending) and the rest stay ordinary bank subjects.

    Returns ``{bank_txn_id: ledger_id}`` for exactly the winning pairs.
    """
    groups: dict = {}
    for bank_txn_id, ledger_id in bank_top_candidate.items():
        if ledger_id and ledger_id in unclaimed_ledger_ids:
            groups.setdefault(ledger_id, []).append(bank_txn_id)
    pairs = {}
    for ledger_id, bank_ids in groups.items():
        winner = min(bank_ids)
        pairs[winner] = ledger_id
    return pairs
