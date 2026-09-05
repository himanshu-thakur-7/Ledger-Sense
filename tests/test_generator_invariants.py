"""Invariant tests for the W1 §4 generator (BOARD.md W1 card).

Covers acceptance criteria 3 (KEY4 invariant), 6 (overlay rows carry a queryable
label), and 7 (money is never float, anywhere in the generator).
"""

import ast
import dataclasses
import pathlib
import re
from decimal import Decimal

import pytest

from ledger_sense.data.counterparties import build_counterparty_universe
from ledger_sense.data.generator import (
    OVERLAY_CLUSTER_THRESHOLD,
    GeneratorConfig,
    generate,
)
from ledger_sense.data.models import (
    OVERLAY_NOTE_PREFIX,
    BankTransaction,
    LedgerEntry,
)
from ledger_sense.data.money import money_str, to_money
from ledger_sense.data.names import _VARIANTS, key4, noisy_variant

REFERENCE_SEED = 42

DATA_PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "src" / "ledger_sense" / "data"


# ---------------------------------------------------------------------------
# Acceptance 3: KEY4 invariant
# ---------------------------------------------------------------------------


def test_key4_extracts_first_four_alphanumeric_chars_uppercased():
    assert key4("Acme Logistics Group") == "ACME"
    assert key4("3M Co.") == "3MCO"
    assert key4("A.B. Freight") == "ABFR"
    assert key4("") == ""


@pytest.mark.parametrize("kind", list(_VARIANTS) + ["malformed"])
def test_every_noise_variant_preserves_key4_for_full_counterparty_universe(kind):
    """The invariant the spec calls out explicitly: every noise transform this
    module can produce preserves the canonical name's first 4 alphanumeric chars,
    for every counterparty in the (seed=42) reference universe."""
    import random

    counterparties = build_counterparty_universe(REFERENCE_SEED)
    rng = random.Random(1234)  # noise-application RNG is independent of this check
    for counterparty in counterparties:
        noisy = noisy_variant(rng, counterparty.canonical_name, kind=kind)
        assert key4(noisy) == key4(counterparty.canonical_name), (
            f"KEY4 invariant broken for {counterparty.canonical_name!r} "
            f"-> {noisy!r} (variant={kind})"
        )


def test_key4_invariant_holds_end_to_end_in_generated_bank_rows():
    """Every bank row that has a ground-truth match link must have a noisy name
    whose KEY4 matches its ledger counterpart's KEY4 -- the property Agent 1's
    by_key4 block index (spec §5.2) depends on."""
    dataset = generate(GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=5000))
    ledger_by_id = {r["ledger_id"]: r for r in dataset.ledger_rows}
    bank_by_id = {r["bank_txn_id"]: r for r in dataset.bank_rows}
    checked = 0
    for link in dataset.match_link_rows:
        ledger = ledger_by_id[link["ledger_id"]]
        bank = bank_by_id[link["bank_txn_id"]]
        assert key4(bank["counterparty_name_raw"]) == key4(ledger["counterparty_name"])
        checked += 1
    assert checked > 0


# ---------------------------------------------------------------------------
# Acceptance 6: overlay rows carry a queryable label
# ---------------------------------------------------------------------------


def test_overlay_plants_when_no_natural_cluster_qualifies():
    """At a small n_cases, no counterparty can naturally accumulate 8 siblings of
    one exception shape, so --overlay is guaranteed to actually plant -- proving the
    plant code path runs, not just the (more often not-planted) reference dataset."""
    dataset = generate(GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=500, overlay=True))
    overlay = dataset.summary.overlay
    assert overlay.natural_max_cluster < OVERLAY_CLUSTER_THRESHOLD
    assert overlay.planted is True
    assert 12 <= overlay.sibling_count <= 20

    overlay_rows = [r for r in dataset.match_link_rows if r["defect"] == overlay.defect_name]
    assert len(overlay_rows) == overlay.sibling_count

    # Queryable label: an ordinary column value, not a source comment.
    for row in overlay_rows:
        assert row["note"].startswith(OVERLAY_NOTE_PREFIX)
        assert row["note"] == f"{OVERLAY_NOTE_PREFIX}{overlay.defect_name}"

    # And nothing outside the overlay is mislabeled.
    non_overlay_rows = [r for r in dataset.match_link_rows if r["defect"] != overlay.defect_name]
    assert all(not r["note"].startswith(OVERLAY_NOTE_PREFIX) for r in non_overlay_rows)


def test_overlay_disabled_by_default_plants_nothing():
    dataset = generate(GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=500))
    overlay = dataset.summary.overlay
    assert overlay.enabled is False
    assert overlay.planted is False
    assert overlay.sibling_count == 0
    assert not any(r["defect"] == overlay.defect_name for r in dataset.match_link_rows)
    assert not any(r["note"].startswith(OVERLAY_NOTE_PREFIX) for r in dataset.match_link_rows)


def test_overlay_check_is_computed_not_assumed():
    """L15: the plant/no-plant decision must be a real per-run computation. Confirm
    the two branches are mutually exclusive and internally consistent regardless of
    which one this particular seed/n_cases combination lands in."""
    dataset = generate(
        GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=25_000, overlay=True)
    )
    overlay = dataset.summary.overlay
    if overlay.natural_max_cluster >= OVERLAY_CLUSTER_THRESHOLD:
        assert overlay.planted is False
        assert overlay.sibling_count == 0
    else:
        assert overlay.planted is True
        assert 12 <= overlay.sibling_count <= 20


def test_overlay_siblings_share_the_documented_shape():
    """All planted siblings are pinned to one counterparty with a consistent fixed
    fee delta and exact reference (spec: "12-20 siblings of one fee_offset-shaped
    defect")."""
    dataset = generate(GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=500, overlay=True))
    overlay = dataset.summary.overlay
    assert overlay.planted
    ledger_by_id = {r["ledger_id"]: r for r in dataset.ledger_rows}
    bank_by_id = {r["bank_txn_id"]: r for r in dataset.bank_rows}
    overlay_links = [r for r in dataset.match_link_rows if r["defect"] == overlay.defect_name]
    counterparty_ids = set()
    deltas = set()
    for link in overlay_links:
        ledger = ledger_by_id[link["ledger_id"]]
        bank = bank_by_id[link["bank_txn_id"]]
        counterparty_ids.add(ledger["counterparty_id"])
        assert bank["reference_raw"] == ledger["reference"]
        deltas.add(abs(Decimal(ledger["amount"]) - Decimal(bank["amount"])))
    assert counterparty_ids == {overlay.counterparty_id}
    assert deltas == {Decimal("15.00")}


# ---------------------------------------------------------------------------
# Acceptance 7: money is never float, anywhere in the generator
# ---------------------------------------------------------------------------


def test_no_float_builtin_calls_anywhere_in_data_package():
    for path in DATA_PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                pytest.fail(f"float() called in {path}:{node.lineno} -- money must stay Decimal (law L3)")


def test_amount_fields_are_typed_decimal():
    ledger_fields = {f.name: f.type for f in dataclasses.fields(LedgerEntry)}
    bank_fields = {f.name: f.type for f in dataclasses.fields(BankTransaction)}
    assert ledger_fields["amount"] is Decimal
    assert bank_fields["amount"] is Decimal


def test_to_money_rejects_float_input():
    with pytest.raises(TypeError):
        to_money(1.23)


def test_generated_amounts_are_exact_2dp_strings_no_float_artifacts(tmp_path=None):
    dataset = generate(GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=2000))
    pattern = re.compile(r"-?\d+\.\d{2}$")
    for row in dataset.ledger_rows:
        assert pattern.match(row["amount"]), row["amount"]
        assert money_str(Decimal(row["amount"])) == row["amount"]
    for row in dataset.bank_rows:
        assert pattern.match(row["amount"]), row["amount"]
        assert money_str(Decimal(row["amount"])) == row["amount"]
