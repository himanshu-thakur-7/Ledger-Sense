"""The five §5 features. Money and scoring arithmetic are Decimal/integer only."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Optional

from ledger_sense.data.models import BankTransaction, LedgerEntry
from ledger_sense.data.money import cents

D = Decimal
WEIGHTS = {"reference": 40, "amount": 30, "name": 20, "date": 7, "currency": 3}
AMOUNT_SCORES = {"exact": D(1), "fx": D("0.8"), "partial": D("0.55"), "conflict": D(0)}


def squash(text: str) -> str:
    return "".join(c for c in text.upper() if c.isalnum())


def normalize_name(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.upper()).split())


def normalize_currency(text: str) -> str:
    return text.strip().upper()


def ratio(left: str, right: str) -> Decimal:
    """Sequence-matching ratio without the stdlib's float-valued ratio() call."""
    if not left or not right:
        return D(0)
    matches = sum(block.size for block in SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks())
    return D(2 * matches) / D(len(left) + len(right))


def token_set_ratio(left: str, right: str) -> Decimal:
    a, b = set(left.split()), set(right.split())
    common = " ".join(sorted(a & b))
    combined_a = " ".join(filter(None, (common, " ".join(sorted(a - b)))))
    combined_b = " ".join(filter(None, (common, " ".join(sorted(b - a)))))
    return max(ratio(common, combined_a), ratio(common, combined_b), ratio(combined_a, combined_b))


def partial_ratio(left: str, right: str) -> Decimal:
    if not left or not right:
        return D(0)
    short, long = sorted((left, right), key=len)
    if short in long:
        return D(1)
    # Align windows on matching blocks, as in the classic partial-ratio algorithm.
    blocks = SequenceMatcher(None, short, long, autojunk=False).get_matching_blocks()
    starts = {max(0, block.b - block.a) for block in blocks}
    return max(ratio(short, long[start:start + len(short)]) for start in starts)


def name_similarity(left: str, right: str) -> Decimal:
    left, right = normalize_name(left), normalize_name(right)
    return max(D("0.40"), token_set_ratio(left, right), partial_ratio(left, right))


def plausible_partial(book: int, posted: int) -> bool:
    return book * posted > 0 and 15 * abs(book) <= 100 * abs(posted) <= 85 * abs(book)


def amount_class(book: int, posted: int) -> str:
    if book == posted:
        return "exact"
    if book * posted <= 0:
        return "conflict"
    delta = abs(posted - book)
    # Multiplication keeps the half-percent and quarter-entry boundaries exact.
    if delta * 200 <= max(350 * 200, abs(book)) and delta * 4 <= abs(book):
        return "fx"
    return "partial" if plausible_partial(book, posted) else "conflict"


def date_similarity(booked_at: str, value_date: str) -> Decimal:
    lag = abs(datetime.fromisoformat(booked_at.replace("Z", "+00:00")) -
              datetime.fromisoformat(value_date.replace("Z", "+00:00")))
    if lag <= timedelta(days=3):
        return D(1)
    if lag >= timedelta(days=60):
        return D(0)
    micros = ((lag.days * 86400 + lag.seconds) * 1000000 + lag.microseconds)
    return (D(60) - D(micros) / D(86400000000)) / D(57)


def interlock(bank: BankTransaction, entry: LedgerEntry) -> str:
    book, posted = cents(entry.amount), cents(bank.amount)
    if (book == 0) != (posted == 0) or book * posted < 0:
        return "anomalous_amount"
    if normalize_currency(bank.currency) != normalize_currency(entry.currency):
        return "currency_conflict"
    return ""


@dataclass(frozen=True)
class Features:
    reference: Optional[Decimal]
    amount: str
    amount_score: Decimal
    name: Optional[Decimal]
    date: Decimal
    currency: Decimal
    short_circuit: bool = False

    def to_dict(self) -> dict:
        return {key: str(value) if isinstance(value, Decimal) else value
                for key, value in asdict(self).items()}


@dataclass(frozen=True)
class ScoredCandidate:
    ledger: LedgerEntry
    score: Decimal
    features: Features

    def to_dict(self) -> dict:
        return {"ledger_id": self.ledger.ledger_id, "score": str(self.score),
                "features": self.features.to_dict(), "ledger": self.ledger.to_row()}


def score_candidate(bank: BankTransaction, entry: LedgerEntry, *, known_reference: bool = False) -> ScoredCandidate:
    reference, quoted = squash(entry.reference), squash(bank.reference_raw)
    ref = (None if not quoted else D(1) if quoted == reference else
           D("0.6") if not known_reference and ratio(quoted, reference) >= D("0.9") else D(0))
    amt = amount_class(cents(entry.amount), cents(bank.amount))
    ccy = D(normalize_currency(bank.currency) == normalize_currency(entry.currency))
    date = date_similarity(entry.booked_at, bank.value_date)
    if ref == 1 and amt == "exact" and ccy == 1:
        return ScoredCandidate(entry, D(100), Features(ref, amt, D(1), None, date, ccy, True))
    name = name_similarity(entry.counterparty_name, bank.counterparty_name_raw)
    weighted = AMOUNT_SCORES[amt] * 30 + name * 20 + date * 7 + ccy * 3
    score = weighted * 100 / 60 if ref is None else weighted + ref * 40
    return ScoredCandidate(entry, score, Features(ref, amt, AMOUNT_SCORES[amt], name, date, ccy))


def acceptance(candidate: ScoredCandidate, margin: Decimal, count: int) -> str:
    """Evidence-only gate. Interlock and capacity are mandatory at settlement."""
    f = candidate.features
    if candidate.score >= 88 and (count == 1 or margin >= 6):
        return "high_confidence"
    if (candidate.score >= 78 and f.reference == 1 and f.amount == "partial"
            and f.name is not None and f.name >= D("0.70")):
        return "PARTIAL_WITH_EXACT_REFERENCE"
    return ""
