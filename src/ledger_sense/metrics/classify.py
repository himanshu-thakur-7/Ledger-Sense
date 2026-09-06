"""Exception-class shape classification (task brief W6 / spec §9.1: "exceptions
eliminated by class, not a raw count").

A "class" is the same three-axis shape a learned rule's predicate is built
from (law L11 / ``ledger_sense.learning.predicate``'s own vocabulary):
normalized counterparty key, amount-delta bucket, reference-transform
pattern. Two exception rows collapse into the same class exactly when a
single learned rule's predicate could resolve both -- which is the only
definition of "class" that makes "an exception class disappeared" a real
claim instead of a raw-count coincidence.

This module never imports ``ledger_sense.learning``; it re-derives the two
small pieces of vocabulary it needs (amount bucketing, reference-transform
typing) from the same raw ``features`` cell values Agent 1 already wrote,
the same way ``ledger_sense.learning.predicate`` itself re-derives instead of
importing Agent 1's scoring internals.
"""

from decimal import Decimal

# Deterministic amount-delta buckets, in integer cents (law L3: never float).
# Boundaries are arbitrary but fixed -- what matters is that the same bucket
# function classifies both passes identically.
_AMOUNT_BUCKET_LIMITS = (
    (0, "0"),
    (100, "1-100"),
    (500, "101-500"),
    (1500, "501-1500"),
    (5000, "1501-5000"),
    (20000, "5001-20000"),
    (100000, "20001-100000"),
)
_UNBOUNDED_BUCKET = ">100000"

# Stamped when a subject carries no matcher feature vector at all -- a
# ledger-only exception (no bank_txn_id) or a bank line matching's own cheap
# tier never scored (see ``ledger_sense.learning.cli._support_count``, which
# skips exactly these rows for the same reason).
NO_FEATURES_BUCKET = "no_features"


def amount_bucket(delta_cents) -> str:
    """The bucket label for ``|delta_cents|`` -- integer cents in, a fixed
    string label out, never a float boundary."""
    magnitude = abs(int(delta_cents))
    for limit, label in _AMOUNT_BUCKET_LIMITS:
        if magnitude <= limit:
            return label
    return _UNBOUNDED_BUCKET


def reference_pattern(features: dict):
    """Classify the raw ``reference`` feature (``1`` / ``0.6`` / ``0`` /
    ``null``) into the same plain-English transform
    ``ledger_sense.learning.predicate.reference_transform_of`` uses --
    re-derived here, not imported (law L1). ``None`` when the row carries no
    reference evidence at all."""
    if "reference" not in features:
        return None
    value = features["reference"]
    if value is None:
        return "missing"
    value = Decimal(str(value))
    if value == 1:
        return "exact"
    if value == 0:
        return "wrong"
    return "fuzzy"


def exception_class(exception_row: dict, features_by_bank: dict) -> tuple:
    """The ``(counterparty_key, amount_bucket, reference_pattern)`` shape key
    for one ``exceptions.csv`` row.

    A ``ledger``-only subject (no bank_txn_id) or a bank line Agent 1 never
    attached a feature cell to carries no matcher feature vector, so it gets
    the ``NO_FEATURES_BUCKET`` placeholder on both remaining axes -- it can
    never satisfy a learned predicate either, so grouping it apart from the
    real, rule-addressable classes is correct, not a shortcut.
    """
    counterparty_key = exception_row["counterparty_key"] or "(none)"
    bank_txn_id = exception_row["bank_txn_id"]
    if exception_row["subject_kind"] not in ("bank", "pair") or not bank_txn_id:
        return (counterparty_key, NO_FEATURES_BUCKET, NO_FEATURES_BUCKET)
    features = features_by_bank.get(bank_txn_id)
    if not features:
        return (counterparty_key, NO_FEATURES_BUCKET, NO_FEATURES_BUCKET)
    delta = features.get("amount_delta_cents")
    bucket = amount_bucket(delta) if delta is not None else NO_FEATURES_BUCKET
    pattern = reference_pattern(features) or NO_FEATURES_BUCKET
    return (counterparty_key, bucket, pattern)


def class_histogram(exception_rows, features_by_bank) -> dict:
    """``class_key -> count`` for one pass's ``exceptions.csv``."""
    counts = {}
    for row in exception_rows:
        key = exception_class(row, features_by_bank)
        counts[key] = counts.get(key, 0) + 1
    return counts


def class_key_str(key: tuple) -> str:
    return "|".join(key)
