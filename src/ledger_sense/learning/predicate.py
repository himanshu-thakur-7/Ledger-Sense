"""Feature-space predicate schema and evaluator (spec §7.2, law L11).

A learned rule is a predicate over Agent 1's own feature vocabulary --
normalized counterparty key, amount-delta bucket, reference-transform type,
currency -- read straight off a ``match_outcomes.csv`` row's ``features``
JSON cell (see ``ledger_sense.matching.engine.feature_cell``'s output
shape, which this module only *reads*, never imports). Never a bare
transaction id, never a new embedding space -- just the same handful of
values Agent 1 already scored with.

This module never imports ``ledger_sense.matching``. It re-derives the two
small pieces of vocabulary it needs (name squashing, reference-transform
typing) from the plain feature values already sitting in the CSV cell, the
same way ``ledger_sense.guardrail.normalize`` keeps its own token
normalizer instead of importing one.
"""

import re
from decimal import Decimal
from typing import Optional

from ledger_sense.data.money import cents as _cents, to_money as _to_money

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

REFERENCE_TRANSFORMS = ("exact", "fuzzy", "wrong", "missing")
AMOUNT_CLASSES = ("exact", "fx", "partial", "conflict")
PREDICATE_FIELDS = (
    "counterparty_key",
    "currency",
    "amount_delta_cents_min",
    "amount_delta_cents_max",
    "reference_transform",
    "amount_class",
)


def squash(text: str) -> str:
    """Uppercase, alphanumeric-only key.

    Deliberately the same transform as Agent 1's own ``squash()``
    (``matching/scoring.py``), reimplemented here instead of imported (law
    L1) -- it is applied to counterparty names on both sides of the file
    spine, so a human typing "Acme Logistics" at the ``resolve`` CLI has to
    land on the same key Agent 1 already stamped into ``features``.
    """
    return _NON_ALNUM.sub("", text.upper())


def reference_transform_of(features: dict) -> Optional[str]:
    """Classify Agent 1's ``reference`` feature (``1`` / ``0.6`` / ``0.0`` /
    ``null``) into the plain-English transform a human resolution talks
    about. Returns ``None`` when the row carries no reference evidence at
    all (e.g. a ``no_candidate`` row's bare ``{"bank": ...}`` feature cell)."""
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


def build_predicate(
    *,
    counterparty_key=None,
    currency=None,
    amount_delta_min=None,
    amount_delta_max=None,
    reference_transform=None,
    amount_class=None,
) -> dict:
    """Assemble a predicate dict from human-supplied fields.

    ``amount_delta_min``/``amount_delta_max`` are given in **dollars** (a
    string or ``Decimal``, e.g. ``"15.00"``) -- the same units the spec's
    own example uses ("0<amount_delta<=3") -- and stored as integer cents
    (law L3: never float), matching ``amount_delta_cents`` in Agent 1's own
    ``features`` cell.
    """
    predicate = {}
    if counterparty_key is not None and counterparty_key != "":
        predicate["counterparty_key"] = squash(counterparty_key)
    if currency is not None and currency != "":
        predicate["currency"] = currency.strip().upper()
    if amount_delta_min is not None:
        predicate["amount_delta_cents_min"] = _cents(_to_money(amount_delta_min))
    if amount_delta_max is not None:
        predicate["amount_delta_cents_max"] = _cents(_to_money(amount_delta_max))
    if reference_transform is not None and reference_transform != "":
        if reference_transform not in REFERENCE_TRANSFORMS:
            raise ValueError(f"reference_transform must be one of {REFERENCE_TRANSFORMS}")
        predicate["reference_transform"] = reference_transform
    if amount_class is not None and amount_class != "":
        if amount_class not in AMOUNT_CLASSES:
            raise ValueError(f"amount_class must be one of {AMOUNT_CLASSES}")
        predicate["amount_class"] = amount_class
    if "amount_delta_cents_min" in predicate and "amount_delta_cents_max" in predicate:
        if predicate["amount_delta_cents_min"] > predicate["amount_delta_cents_max"]:
            raise ValueError("amount_delta_cents_min must be <= amount_delta_cents_max")
    return predicate


def evaluate_predicate(predicate: dict, features: dict) -> bool:
    """True if ``features`` (a parsed ``match_outcomes.csv`` ``features``
    cell) satisfies every field ``predicate`` specifies (logical AND).

    An empty predicate matches nothing -- a rule must say *something* about
    the feature space (law L11: never "transaction #48213 is fine").
    """
    if not predicate:
        return False
    if "counterparty_key" in predicate and features.get("counterparty_key") != predicate["counterparty_key"]:
        return False
    if "currency" in predicate and features.get("currency_normalized") != predicate["currency"]:
        return False
    if "amount_class" in predicate and features.get("amount") != predicate["amount_class"]:
        return False
    if "amount_delta_cents_min" in predicate or "amount_delta_cents_max" in predicate:
        delta = features.get("amount_delta_cents")
        if delta is None:
            return False
        magnitude = abs(int(delta))
        if "amount_delta_cents_min" in predicate and magnitude < predicate["amount_delta_cents_min"]:
            return False
        if "amount_delta_cents_max" in predicate and magnitude > predicate["amount_delta_cents_max"]:
            return False
    if "reference_transform" in predicate and reference_transform_of(features) != predicate["reference_transform"]:
        return False
    return True


def _dollars(cents_value: int) -> str:
    return str((Decimal(cents_value) / Decimal(100)).quantize(Decimal("0.01")))


def render_english(predicate: dict) -> str:
    """Render ``predicate`` as the plain-English string the ``resolve`` CLI
    prints (spec §7.2/§11: "the candidate predicate in plain English")."""
    parts = []
    if "counterparty_key" in predicate:
        parts.append(f"counterparty={predicate['counterparty_key']}")
    if "amount_delta_cents_min" in predicate or "amount_delta_cents_max" in predicate:
        lo = predicate.get("amount_delta_cents_min")
        hi = predicate.get("amount_delta_cents_max")
        lo_s = _dollars(lo) if lo is not None else "0.00"
        if hi is not None:
            parts.append(f"{lo_s} < |amount_delta| <= {_dollars(hi)}")
        else:
            parts.append(f"|amount_delta| >= {lo_s}")
    if "amount_class" in predicate:
        parts.append(f"amount_class={predicate['amount_class']}")
    if "reference_transform" in predicate:
        parts.append(f"reference={predicate['reference_transform']}")
    if "currency" in predicate:
        parts.append(f"currency={predicate['currency']}")
    return " AND ".join(parts) if parts else "(empty predicate -- never matches)"
