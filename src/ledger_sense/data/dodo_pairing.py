"""Ledger-side pairing for Dodo-sourced bank transactions (W11 locked decision).

**Pairing decision (PRD "Locked decisions" #1, pull-then-synthesize):**
``dodo_source.py`` pulls real, fixed transactions from Dodo's sandbox first;
this module synthesizes the *ledger* counterpart around each one, mirroring
``data/generator.py``'s defect-injection logic (reused directly:
``data.defects.DEFECT_RATES``) -- but strictly inverted from how the
synthetic generator works. The generator synthesizes a ``LedgerEntry`` first
and perturbs a *synthesized* ``BankTransaction`` around it; here the bank row
is a real, already-fixed external observation we don't own and must
reproduce byte-for-byte, so only the **ledger** side is ever perturbed.

That asymmetry shrinks the applicable defect taxonomy versus the full
synthetic generator's twelve §4.2 classes. Supported here
(``PAIRING_DEFECTS``), weighted by the same §4.2 rates (renormalized over
just this subset via :func:`pairing_rates`, exact-count stratified via
:func:`pairing_defect_counts` -- the same largest-remainder method
``data/defects.py::defect_counts`` uses, deliberately mirrored rather than
imported since the denominator differs):

  - ``clean``            ledger mirrors the bank row exactly (amount + reference)
  - ``wrong_reference``  ledger reference differs from what the bank shows
  - ``fx_rounding``      ledger amount differs from the bank amount by a small delta
  - ``negative_amount``  guardrail bait: ledger amount sign is flipped vs. the bank
  - ``zero_amount``      guardrail bait: ledger amount is zeroed

``missing_reference`` is not sampled -- it is *observed*: whenever a pulled
bank row already has a blank ``reference_raw`` (a real, common Dodo sandbox
shape), the case is labeled ``missing_reference`` regardless of the sampled
class, because that reflects what actually happened on the bank side, not a
choice this module gets to make.

Excluded entirely (documented, not silently dropped):

  - ``duplicate`` / ``partial_payment`` -- need *two* bank rows per ledger
    row; each pulled Dodo transaction is one independent row, so there is no
    natural place to plant a second leg without inventing bank data.
  - ``out_of_order`` -- perturbs the value-date lag, which is a bank-side
    timing property; the bank row's ``value_date`` here is a real Dodo
    timestamp, not ours to skew.
  - ``malformed`` -- perturbs bank-side name/currency string formatting,
    which is real observed data here, not ours to corrupt.
  - ``orphan_bank`` / ``orphan_ledger`` -- both classes are exactly "no
    pairing", which contradicts this module's one-bank-row-in,
    one-ledger-row-out contract: every pulled Dodo transaction gets a paired
    ledger row, by design (PRD: "synthesize matching ledger.csv rows around
    them").

Determinism: :func:`pair_dataset` draws everything (the defect sequence and
every per-row synthesis choice) from one ``random.Random`` seeded via
``data.rng.derive_seed`` -- never wall-clock/OS entropy -- so the same
``(bank_transactions, seed)`` always produces byte-identical output.
"""

from __future__ import annotations

import random
from collections import OrderedDict
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import List, Tuple

from .defects import DEFECT_RATES
from .models import BankTransaction, LedgerEntry, MatchLink
from .money import cents, from_cents
from .rng import derive_seed

# Mirrors data/generator.py's ENTRY_TYPES_INFLOW/ENTRY_TYPES_OUTFLOW (a small,
# documented reimplementation -- generator.py stays read-only/unmodified).
ENTRY_TYPES_INFLOW = ("invoice_payment", "subscription_charge")
ENTRY_TYPES_OUTFLOW = ("refund", "payout")

PAIRING_DEFECTS = ("clean", "wrong_reference", "fx_rounding", "negative_amount", "zero_amount")

# A bank row with no reference is relabeled `missing_reference` regardless of
# the sampled class (see module docstring) -- never itself sampled.
MISSING_REFERENCE_DEFECT = "missing_reference"

FX_DELTA_MAX_CENTS = 350


def pairing_rates() -> "OrderedDict[str, Decimal]":
    """§4.2 rates for just ``PAIRING_DEFECTS``, renormalized to sum to 100.

    Decimal throughout (law L3's "never float" discipline extends to every
    number this package computes, per ``data/defects.py``'s own docstring).
    """
    subset = OrderedDict((name, DEFECT_RATES[name]) for name in PAIRING_DEFECTS)
    total = sum(subset.values(), Decimal("0"))
    return OrderedDict((name, rate * Decimal(100) / total) for name, rate in subset.items())


def pairing_defect_counts(n: int) -> "OrderedDict[str, int]":
    """Exact per-defect counts for ``n`` pulled transactions.

    Largest-remainder rounding, mirroring ``data/defects.py::defect_counts``
    exactly (same algorithm, different -- renormalized -- rate table).
    """
    rates = pairing_rates()
    raw = {name: (rate * Decimal(n)) / Decimal(100) for name, rate in rates.items()}
    floors = {name: int(value.to_integral_value(rounding=ROUND_FLOOR)) for name, value in raw.items()}
    remainder = n - sum(floors.values())
    fractions = sorted(rates.keys(), key=lambda name: (raw[name] - floors[name]), reverse=True)
    counts = OrderedDict((name, floors[name]) for name in rates)
    for name in fractions[:remainder]:
        counts[name] += 1
    assert sum(counts.values()) == n
    return counts


def build_pairing_defect_sequence(rng: random.Random, n: int) -> List[str]:
    """Exact-count defect labels for ``n`` pulled rows, shuffled by ``rng``."""
    counts = pairing_defect_counts(n)
    sequence: List[str] = []
    for name, count in counts.items():
        sequence.extend([name] * count)
    rng.shuffle(sequence)
    return sequence


def _reference(seq: int, year: int, *, suffix: str = "") -> str:
    return f"INV-DODO-{year}-{seq:06d}{suffix}"


def _entry_type_for(direction: str, rng: random.Random) -> str:
    pool = ENTRY_TYPES_INFLOW if direction == "credit" else ENTRY_TYPES_OUTFLOW
    return rng.choice(pool)


def synthesize_ledger_entry(
    bank: BankTransaction, defect: str, rng: random.Random, seq: int
) -> Tuple[LedgerEntry, MatchLink, str]:
    """Build the ``LedgerEntry`` + ``MatchLink`` counterpart for one pulled ``bank`` row.

    Returns ``(ledger_entry, match_link, effective_defect)`` -- ``effective_defect``
    is ``defect`` unless the bank row's own shape forces ``missing_reference``
    (see module docstring).
    """
    value_date = datetime.strptime(bank.value_date, "%Y-%m-%dT%H:%M:%SZ")
    booked_at = value_date - timedelta(days=rng.randint(-2, 2), seconds=rng.randint(0, 86_399))
    year = booked_at.year

    ledger_id = f"LG-DODO-{seq:06d}"
    case_id = f"C-DODO-{seq:06d}"
    counterparty_id = f"CP-DODO-{seq:06d}"
    counterparty_name = bank.counterparty_name_raw.strip() or f"Dodo Counterparty {seq}"

    effective_defect = MISSING_REFERENCE_DEFECT if bank.reference_raw == "" else defect
    bank_cents = cents(bank.amount)
    ledger_cents = bank_cents
    reference = bank.reference_raw or _reference(seq, year)
    note = "clean settlement"

    if effective_defect == "wrong_reference":
        reference = _reference(seq, year, suffix="X")
        note = "ledger books a different reference than Dodo shows"
    elif effective_defect == "fx_rounding":
        delta = rng.randint(1, FX_DELTA_MAX_CENTS)
        sign = rng.choice((1, -1))
        ledger_cents = bank_cents + sign * delta
        note = "small fx/rounding delta vs. the Dodo-observed amount"
    elif effective_defect == "negative_amount":
        ledger_cents = -bank_cents
        note = "guardrail bait: sign flipped vs. Dodo"
    elif effective_defect == "zero_amount":
        ledger_cents = 0
        note = "guardrail bait: zero posting vs. Dodo"
    elif effective_defect == MISSING_REFERENCE_DEFECT:
        note = "reference blank on the Dodo side"
    # else: "clean" -- reference/ledger_cents already mirror the bank row.

    entry = LedgerEntry(
        ledger_id=ledger_id,
        booked_at=booked_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        amount=from_cents(ledger_cents),
        currency=bank.currency.strip(),
        entry_type=_entry_type_for(bank.direction, rng),
        counterparty_id=counterparty_id,
        counterparty_name=counterparty_name,
        reference=reference,
        memo=f"dodo_pairing:{effective_defect} for {counterparty_name}",
        account_code="1200" if ledger_cents >= 0 else "2100",
        source_system="dodo",
    )
    link = MatchLink(
        ledger_id=ledger_id,
        bank_txn_id=bank.bank_txn_id,
        relation="exact",
        defect=effective_defect,
        case_id=case_id,
        note=note,
    )
    return entry, link, effective_defect


def pair_dataset(
    bank_transactions: List[BankTransaction], *, seed: int = 0
) -> Tuple[List[LedgerEntry], List[MatchLink], "OrderedDict[str, int]"]:
    """Synthesize one ledger row + match link per ``bank_transactions`` row.

    Deterministic given ``(bank_transactions, seed)`` -- see module docstring.
    """
    rng = random.Random(derive_seed(seed))
    defect_sequence = build_pairing_defect_sequence(rng, len(bank_transactions))
    ledger_entries: List[LedgerEntry] = []
    match_links: List[MatchLink] = []
    histogram: "OrderedDict[str, int]" = OrderedDict()
    for seq, (bank, defect) in enumerate(zip(bank_transactions, defect_sequence)):
        entry, link, effective_defect = synthesize_ledger_entry(bank, defect, rng, seq)
        ledger_entries.append(entry)
        match_links.append(link)
        histogram[effective_defect] = histogram.get(effective_defect, 0) + 1
    return ledger_entries, match_links, histogram
