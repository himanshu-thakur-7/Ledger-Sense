"""The deterministic synthetic batch generator (spec §4).

Given ``(seed, pass_number, n_cases)`` this module produces the same three tables --
``ledger.csv`` (:class:`~ledger_sense.data.models.LedgerEntry`), ``bank.csv``
(:class:`~ledger_sense.data.models.BankTransaction`), and ``match_links.csv``
(:class:`~ledger_sense.data.models.MatchLink`, ground truth) -- byte-for-byte, every
time (law L4). See ``BOARD.md`` W1 card and PRD §4 for the full spec this implements.
"""

from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

from .counterparties import Counterparty, build_counterparty_universe
from .defects import OVERLAY_DEFECT, build_defect_sequence
from .models import OVERLAY_NOTE_PREFIX, BankTransaction, LedgerEntry, MatchLink
from .money import cents, from_cents
from .names import noisy_variant
from .rng import case_rng as make_case_rng

ENTRY_TYPES_INFLOW = ("invoice_payment", "subscription_charge")
ENTRY_TYPES_OUTFLOW = ("refund", "payout")
ENTRY_TYPES_EITHER = ("fee", "adjustment")
ALL_ENTRY_TYPES = ENTRY_TYPES_INFLOW + ENTRY_TYPES_OUTFLOW + ENTRY_TYPES_EITHER

SOURCE_SYSTEM_BY_ENTRY_TYPE = {
    "invoice_payment": "billing",
    "subscription_charge": "billing",
    "refund": "payouts",
    "payout": "payouts",
    "fee": "manual",
    "adjustment": "manual",
}

# Weighted so USD dominates but the currency feature (spec §5.3, weight 3) has
# something to occasionally disagree about.
CURRENCY_POOL = ["USD"] * 9 + ["EUR"] * 1 + ["GBP"] * 1
BANK_ACCOUNTS_BY_CURRENCY = {
    "USD": ["ACCT-USD-01", "ACCT-USD-02"],
    "EUR": ["ACCT-EUR-01"],
    "GBP": ["ACCT-GBP-01"],
}
BANK_METHODS = ["ACH", "WIRE", "CHECK"]

# Generation window: an arbitrary but fixed anchor so booked_at/value_date/statement
# week numbers are reproducible and human-readable, not tied to wall-clock time.
EPOCH = datetime(2026, 1, 5, tzinfo=timezone.utc)  # a Monday
WINDOW_DAYS = 120

LEDGER_AMOUNT_MIN_CENTS = 1_000  # $10.00
LEDGER_AMOUNT_MAX_CENTS = 500_000  # $5,000.00
ORPHAN_BANK_AMOUNT_MIN_CENTS = 1_000
ORPHAN_BANK_AMOUNT_MAX_CENTS = 250_000

# Overlay (BOARD.md W1 card, locked Q3 / PRD §15 open question A).
OVERLAY_CLUSTER_THRESHOLD = 8
OVERLAY_MIN_SIBLINGS = 12
OVERLAY_MAX_SIBLINGS = 20
OVERLAY_FEE_CENTS = 1_500  # a flat $15.00 fee offset

# Defects counted as "exception-shaped" for the overlay's natural-cluster check.
# Deliberately narrower than "everything but clean": out_of_order, duplicate, and
# missing_reference all still carry an exact amount+reference match, which a real
# matcher would generally auto-match or (for duplicate) resolve programmatically --
# they aren't the kind of judgment call a human files a resolution for. This module
# never runs Agent 1's actual scoring (that would cross the W1/W2 boundary), so this
# set is a documented heuristic proxy, not a scored prediction.
EXCEPTION_SHAPED_DEFECTS = frozenset(
    {"wrong_reference", "partial_payment", "fx_rounding", "malformed", "negative_amount", "zero_amount"}
)


@dataclass
class GeneratorConfig:
    seed: int
    pass_number: int
    n_cases: int
    overlay: bool = False
    universe_size: int = 800


@dataclass
class OverlayReport:
    defect_name: str
    enabled: bool
    natural_max_cluster: int
    planted: bool
    sibling_count: int
    counterparty_id: Optional[str] = None


@dataclass
class GenerationSummary:
    seed: int
    pass_number: int
    n_cases: int
    ledger_rows: int
    bank_rows: int
    match_link_rows: int
    unique_counterparties: int
    defect_histogram: "OrderedDict[str, int]"
    overlay: OverlayReport

    def format(self) -> str:
        lines = [
            "Ledger Sense synthetic generation summary",
            f"  seed={self.seed} pass_number={self.pass_number} n_cases={self.n_cases}",
            f"  row counts: ledger.csv={self.ledger_rows} bank.csv={self.bank_rows} "
            f"match_links.csv={self.match_link_rows}",
            f"  unique counterparties: {self.unique_counterparties}",
            "  defect histogram (documented mix, §4.2):",
        ]
        for name, count in self.defect_histogram.items():
            pct = (Decimal(count) * 100 / Decimal(self.n_cases)) if self.n_cases else Decimal(0)
            lines.append(f"    {name:<18} {count:>7}  ({pct.quantize(Decimal('0.01'))}%)")
        ov = self.overlay
        if ov.enabled:
            state = "PLANTED" if ov.planted else "not planted (natural cluster already qualified)"
            lines.append(
                f"  overlay: class={ov.defect_name!r} {state} -- "
                f"siblings={ov.sibling_count} "
                f"(natural max cluster observed={ov.natural_max_cluster}, "
                f"threshold={OVERLAY_CLUSTER_THRESHOLD})"
            )
        else:
            lines.append(
                f"  overlay: disabled -- class={ov.defect_name!r} would plant "
                f"12-20 siblings if enabled and no natural cluster >= "
                f"{OVERLAY_CLUSTER_THRESHOLD} exists "
                f"(natural max cluster observed={ov.natural_max_cluster})"
            )
        return "\n".join(lines)


@dataclass
class GeneratedDataset:
    ledger_rows: List[dict]
    bank_rows: List[dict]
    match_link_rows: List[dict]
    summary: GenerationSummary


@dataclass
class _Counters:
    ledger_seq: int = 0
    bank_seq: int = 0
    case_seq: int = 0


def _reference(pass_number: int, ledger_seq: int, year: int) -> str:
    # Embeds pass_number as the leading digit of the numeric suffix so pass 1 and
    # pass 2 references can never collide (law L6 / spec §4.3 "non-overlapping
    # references"), while matching the spec's own example format exactly
    # (INV-2026-1000042 == pass 1, local sequence 42).
    return f"INV-{year}-{pass_number}{ledger_seq:06d}"


def _foreign_reference(pass_number: int, rng, year: int, exclude_seq: int, upper_bound: int) -> str:
    upper_bound = max(upper_bound, 1)
    other = rng.randrange(0, upper_bound)
    if other == exclude_seq:
        other = (other + 1) % upper_bound
    return _reference(pass_number, other, year)


def _random_datetime(rng, base: Optional[datetime] = None, day_low=0, day_high=WINDOW_DAYS) -> datetime:
    if base is None:
        day_offset = rng.randint(day_low, day_high)
        second_offset = rng.randint(0, 86_399)
        return EPOCH + timedelta(days=day_offset, seconds=second_offset)
    day_offset = rng.randint(day_low, day_high)
    second_offset = rng.randint(0, 86_399)
    return base + timedelta(days=day_offset, seconds=second_offset)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _statement_id(pass_number: int, value_date: datetime) -> str:
    week = (value_date - EPOCH).days // 7
    if week < 0:
        week = 0
    return f"STMT-P{pass_number}-{week:03d}"


def _bank_account(rng, currency: str) -> str:
    pool = BANK_ACCOUNTS_BY_CURRENCY.get(currency.strip().upper(), BANK_ACCOUNTS_BY_CURRENCY["USD"])
    return rng.choice(pool)


def _apply_name_noise(rng, canonical_name: str, force_malformed: bool) -> str:
    if force_malformed:
        return noisy_variant(rng, canonical_name, kind="malformed")
    if rng.random() < 0.85:
        noisy = noisy_variant(rng, canonical_name)
        # Some variants are occasionally a no-op for a given name (e.g. drop_suffix
        # on a name with no suffix to drop) -- when the 85% draw says "noise this
        # record", it should actually look different, or the baseline-noise rate
        # this module reports would silently undercount. Uppercase is always
        # different for the word lists in names.py (none are pre-uppercased).
        if noisy == canonical_name:
            noisy = noisy_variant(rng, canonical_name, kind="uppercase")
        return noisy
    return canonical_name


def _amount_delta_bucket(ledger_c: int, bank_c: int) -> int:
    return abs(ledger_c - bank_c) // 50


def _reference_pattern(ledger_reference: str, reference_raw: str) -> str:
    """Coarse, feature-observable reference shape for the overlay cluster gate.

    Deliberately mirrors what a matcher could actually observe (the raw string
    shape), not the generator's internal defect label -- so e.g. ``wrong_reference``
    (genuinely different digits) and ``malformed`` (same digits, junk separators)
    only end up in the same cluster if they'd actually look alike to a matcher.
    """
    if reference_raw == "":
        return "empty"
    if reference_raw == ledger_reference:
        return "exact"
    normalized = reference_raw.upper()
    for junk in ("_", "/", ".", "  "):
        normalized = normalized.replace(junk, "-")
    normalized = " ".join(normalized.split())
    if normalized == ledger_reference:
        return "malformed"
    return "foreign"


def _description(method: str, direction: str, noisy_name: str, reference_raw: str) -> str:
    verb = "CREDIT" if direction == "credit" else "DEBIT"
    ref_part = f"PYMT REF {reference_raw}" if reference_raw else "PYMT REF (none)"
    return f"{method} {verb} {noisy_name.upper()} {ref_part}"


def _direction_for(amount_cents: int, fallback_positive: bool) -> str:
    if amount_cents > 0:
        return "credit"
    if amount_cents < 0:
        return "debit"
    return "credit" if fallback_positive else "debit"


class _CaseBuilder:
    """Builds one case's rows. Holds only the shared, read-only generation config."""

    def __init__(self, pass_number: int, counterparties: List[Counterparty]):
        self.pass_number = pass_number
        self.counterparties = counterparties

    def _pick_base(self, rng, forced_counterparty: Optional[Counterparty] = None) -> Tuple[Counterparty, str, int, datetime]:
        counterparty = forced_counterparty if forced_counterparty is not None else rng.choice(self.counterparties)
        entry_type = rng.choice(ALL_ENTRY_TYPES)
        magnitude = rng.randint(LEDGER_AMOUNT_MIN_CENTS, LEDGER_AMOUNT_MAX_CENTS)
        if entry_type in ENTRY_TYPES_INFLOW:
            sign = 1
        elif entry_type in ENTRY_TYPES_OUTFLOW:
            sign = -1
        else:
            sign = rng.choice((1, -1))
        ledger_cents = sign * magnitude
        booked_at = _random_datetime(rng)
        return counterparty, entry_type, ledger_cents, booked_at

    def _make_ledger(self, counters, counterparty, entry_type, ledger_cents, booked_at, currency, reference) -> Tuple[LedgerEntry, str]:
        ledger_seq = counters.ledger_seq
        counters.ledger_seq += 1
        ledger_id = f"LG-P{self.pass_number}-{ledger_seq:06d}"
        amount = from_cents(ledger_cents)
        memo = f"{entry_type} for {counterparty.canonical_name}"
        account_code = "1200" if ledger_cents >= 0 else "2100"
        entry = LedgerEntry(
            ledger_id=ledger_id,
            booked_at=_iso(booked_at),
            amount=amount,
            currency=currency,
            entry_type=entry_type,
            counterparty_id=counterparty.counterparty_id,
            counterparty_name=counterparty.canonical_name,
            reference=reference,
            memo=memo,
            account_code=account_code,
            source_system=SOURCE_SYSTEM_BY_ENTRY_TYPE[entry_type],
        )
        return entry, ledger_id

    def _make_bank(
        self, counters, rng, counterparty, bank_cents, value_date, currency, reference_raw, force_malformed
    ) -> BankTransaction:
        bank_seq = counters.bank_seq
        counters.bank_seq += 1
        bank_txn_id = f"BK-P{self.pass_number}-{bank_seq:06d}"
        noisy_name = _apply_name_noise(rng, counterparty.canonical_name, force_malformed)
        direction = _direction_for(bank_cents, fallback_positive=True)
        method = rng.choice(BANK_METHODS)
        bank_currency = f" {currency.lower()} " if force_malformed else currency
        description = _description(method, direction, noisy_name, reference_raw)
        return BankTransaction(
            bank_txn_id=bank_txn_id,
            value_date=_iso(value_date),
            amount=from_cents(bank_cents),
            currency=bank_currency,
            counterparty_name_raw=noisy_name,
            reference_raw=reference_raw,
            description=description,
            bank_account=_bank_account(rng, currency),
            statement_id=_statement_id(self.pass_number, value_date),
            direction=direction,
        )

    def build(
        self,
        rng,
        counters: _Counters,
        case_id: str,
        defect: str,
        forced_counterparty: Optional[Counterparty] = None,
    ) -> dict:
        """Returns {"ledger": LedgerEntry|None, "banks": [...], "links": [...],
        "cluster_key": tuple|None} for one case."""
        counterparty, entry_type, ledger_cents, booked_at = self._pick_base(rng, forced_counterparty)
        currency = rng.choice(CURRENCY_POOL)
        year = booked_at.year
        force_malformed = defect == "malformed"

        if defect == "orphan_ledger":
            reference = _reference(self.pass_number, counters.ledger_seq, year)
            ledger, ledger_id = self._make_ledger(
                counters, counterparty, entry_type, ledger_cents, booked_at, currency, reference
            )
            return {
                "ledger": ledger,
                "banks": [],
                "links": [],
                "cluster_key": None,
                "counterparty_id": counterparty.counterparty_id,
            }

        if defect == "orphan_bank":
            magnitude = rng.randint(ORPHAN_BANK_AMOUNT_MIN_CENTS, ORPHAN_BANK_AMOUNT_MAX_CENTS)
            bank_cents = rng.choice((1, -1)) * magnitude
            value_date = _random_datetime(rng)
            has_reference = rng.random() < 0.5
            reference_raw = (
                _foreign_reference(self.pass_number, rng, year, exclude_seq=-1, upper_bound=max(counters.ledger_seq, 1))
                if has_reference
                else ""
            )
            bank = self._make_bank(
                counters, rng, counterparty, bank_cents, value_date, currency, reference_raw, force_malformed=False
            )
            return {
                "ledger": None,
                "banks": [bank],
                "links": [],
                "cluster_key": None,
                "counterparty_id": counterparty.counterparty_id,
            }

        # Every remaining defect has exactly one ledger row.
        reference = _reference(self.pass_number, counters.ledger_seq, year)
        ledger_seq_for_ref = counters.ledger_seq  # captured before _make_ledger increments it
        ledger, ledger_id = self._make_ledger(
            counters, counterparty, entry_type, ledger_cents, booked_at, currency, reference
        )

        banks: List[BankTransaction] = []
        links: List[MatchLink] = []
        cluster_key = None

        def emit(bank_cents, value_date, reference_raw, relation, note):
            bank = self._make_bank(
                counters, rng, counterparty, bank_cents, value_date, currency, reference_raw, force_malformed
            )
            banks.append(bank)
            links.append(
                MatchLink(
                    ledger_id=ledger_id,
                    bank_txn_id=bank.bank_txn_id,
                    relation=relation,
                    defect=defect,
                    case_id=case_id,
                    note=note,
                )
            )
            return bank

        if defect == "clean":
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            emit(ledger_cents, value_date, reference, "exact", "clean settlement")

        elif defect == "wrong_reference":
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            foreign_ref = _foreign_reference(
                self.pass_number, rng, year, exclude_seq=ledger_seq_for_ref, upper_bound=max(ledger_seq_for_ref, 1) + 1
            )
            emit(ledger_cents, value_date, foreign_ref, "exact", "bank quoted a different reference")

        elif defect == "partial_payment":
            value_date_a = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            value_date_b = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            low = int(abs(ledger_cents) * 3 // 10) or 1
            high = int(abs(ledger_cents) * 7 // 10) or 1
            if high <= low:
                high = low + 1
            split = rng.randint(low, high)
            sign = 1 if ledger_cents >= 0 else -1
            part_a = sign * split
            part_b = ledger_cents - part_a
            emit(part_a, value_date_a, reference, "partial", "first partial installment")
            emit(part_b, value_date_b, reference, "partial", "second partial installment")

        elif defect == "out_of_order":
            if rng.random() < 0.5:
                value_date = booked_at - timedelta(days=rng.randint(1, 6))
            else:
                value_date = booked_at + timedelta(days=rng.randint(21, 48))
            emit(ledger_cents, value_date, reference, "exact", "value date badly skewed from booking")

        elif defect == "duplicate":
            value_date_a = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            value_date_b = _random_datetime(rng, base=booked_at, day_low=0, day_high=3)
            emit(ledger_cents, value_date_a, reference, "exact", "first leg, settles the ledger entry")
            emit(ledger_cents, value_date_b, reference, "duplicate", "second posting of the same event")

        elif defect == "missing_reference":
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            emit(ledger_cents, value_date, "", "exact", "reference blank on the bank side")

        elif defect == "fx_rounding":
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            delta = rng.randint(1, 350)
            sign = rng.choice((1, -1))
            bank_cents = ledger_cents - sign * delta if ledger_cents >= 0 else ledger_cents + sign * delta
            emit(bank_cents, value_date, reference, "exact", "small fx/rounding delta")

        elif defect == "malformed":
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            sep = rng.choice(("_", "/", "  ", "."))
            junk_reference = reference.replace("-", sep).lower()
            emit(ledger_cents, value_date, junk_reference, "exact", "padded/lowercased currency and name, junk separators")

        elif defect == "negative_amount":
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            emit(-ledger_cents, value_date, reference, "exact", "guardrail bait: sign flipped")

        elif defect == "zero_amount":
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            emit(0, value_date, reference, "exact", "guardrail bait: zero posting")

        elif defect == OVERLAY_DEFECT:
            value_date = _random_datetime(rng, base=booked_at, day_low=-2, day_high=2)
            sign = 1 if ledger_cents >= 0 else -1
            bank_cents = ledger_cents - sign * OVERLAY_FEE_CENTS
            emit(bank_cents, value_date, reference, "exact", f"{OVERLAY_NOTE_PREFIX}{OVERLAY_DEFECT}")

        else:
            raise ValueError(f"unknown defect: {defect!r}")

        if defect in EXCEPTION_SHAPED_DEFECTS and links:
            first_bank_cents = cents(banks[0].amount)
            cluster_key = (
                counterparty.counterparty_id,
                _amount_delta_bucket(ledger_cents, first_bank_cents),
                _reference_pattern(reference, banks[0].reference_raw),
            )

        return {
            "ledger": ledger,
            "banks": banks,
            "links": links,
            "cluster_key": cluster_key,
            "counterparty_id": counterparty.counterparty_id,
        }


def generate(config: GeneratorConfig) -> GeneratedDataset:
    """Generate one full (ledger, bank, match_links) batch per ``config``.

    Deterministic: identical ``config`` values always produce byte-identical CSV
    output (law L4). See module docstring and PRD §4 for the contract.
    """
    counterparties = build_counterparty_universe(config.seed, config.universe_size)
    rng = make_case_rng(config.seed, config.pass_number)
    builder = _CaseBuilder(config.pass_number, counterparties)
    counters = _Counters()

    defect_sequence = build_defect_sequence(rng, config.n_cases)

    ledger_entries: List[LedgerEntry] = []
    bank_txns: List[BankTransaction] = []
    match_links: List[MatchLink] = []
    seen_counterparties = set()
    cluster_counter: Counter = Counter()
    histogram: "OrderedDict[str, int]" = OrderedDict()

    for defect in defect_sequence:
        case_id = f"C-P{config.pass_number}-{counters.case_seq:06d}"
        counters.case_seq += 1
        result = builder.build(rng, counters, case_id, defect)
        seen_counterparties.add(result["counterparty_id"])
        if result["ledger"] is not None:
            ledger_entries.append(result["ledger"])
        for b in result["banks"]:
            bank_txns.append(b)
        for l in result["links"]:
            match_links.append(l)
        if result["cluster_key"] is not None:
            cluster_counter[result["cluster_key"]] += 1
        histogram[defect] = histogram.get(defect, 0) + 1

    natural_max_cluster = max(cluster_counter.values()) if cluster_counter else 0
    overlay_planted = False
    overlay_sibling_count = 0
    overlay_counterparty_id = None

    if config.overlay and natural_max_cluster < OVERLAY_CLUSTER_THRESHOLD:
        overlay_planted = True
        overlay_sibling_count = rng.randint(OVERLAY_MIN_SIBLINGS, OVERLAY_MAX_SIBLINGS)
        overlay_counterparty = rng.choice(counterparties)
        overlay_counterparty_id = overlay_counterparty.counterparty_id
        for _ in range(overlay_sibling_count):
            case_id = f"C-P{config.pass_number}-{counters.case_seq:06d}"
            counters.case_seq += 1
            # Every overlay case is pinned to the single overlay counterparty so the
            # siblings actually cluster (spec: "one fee_offset-shaped defect").
            result = builder.build(
                rng, counters, case_id, OVERLAY_DEFECT, forced_counterparty=overlay_counterparty
            )
            ledger_entries.append(result["ledger"])
            seen_counterparties.add(result["counterparty_id"])
            bank_txns.extend(result["banks"])
            match_links.extend(result["links"])
            # Deliberately NOT added to `histogram` -- that dict is the §4.2
            # documented mix (acceptance #2); overlay siblings are additive and
            # tracked only in `overlay.sibling_count` so they can never be mistaken
            # for a naturally-occurring class (law L15).

    overlay = OverlayReport(
        defect_name=OVERLAY_DEFECT,
        enabled=config.overlay,
        natural_max_cluster=natural_max_cluster,
        planted=overlay_planted,
        sibling_count=overlay_sibling_count,
        counterparty_id=overlay_counterparty_id,
    )

    summary = GenerationSummary(
        seed=config.seed,
        pass_number=config.pass_number,
        n_cases=config.n_cases,
        ledger_rows=len(ledger_entries),
        bank_rows=len(bank_txns),
        match_link_rows=len(match_links),
        unique_counterparties=len(seen_counterparties),
        defect_histogram=histogram,
        overlay=overlay,
    )

    return GeneratedDataset(
        ledger_rows=[e.to_row() for e in ledger_entries],
        bank_rows=[b.to_row() for b in bank_txns],
        match_link_rows=[m.to_row() for m in match_links],
        summary=summary,
    )
