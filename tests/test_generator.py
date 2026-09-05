"""Acceptance tests for the W1 §4 generator (BOARD.md W1 card).

Covers acceptance criteria 1 (byte-identical reruns), 2 (defect histogram
tolerance), 4 (pass 2 disjoint references / shared counterparty universe), and 5
(printed summary contents). See test_generator_invariants.py for KEY4 (3), overlay
labeling (6), and the no-float check (7).
"""

import csv
import hashlib
import os
from decimal import Decimal

import pytest

from ledger_sense.data.cli import write_dataset
from ledger_sense.data.counterparties import build_counterparty_universe
from ledger_sense.data.defects import DEFECT_RATES, defect_counts
from ledger_sense.data.generator import GeneratorConfig, generate
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS, MATCH_LINK_COLUMNS

REFERENCE_SEED = 42
REFERENCE_N_CASES = 25_000


@pytest.fixture(scope="session")
def pass1_dataset():
    return generate(GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=REFERENCE_N_CASES))


@pytest.fixture(scope="session")
def pass2_dataset():
    return generate(GeneratorConfig(seed=REFERENCE_SEED, pass_number=2, n_cases=REFERENCE_N_CASES))


# ---------------------------------------------------------------------------
# Schema (spec §4.1 exact columns)
# ---------------------------------------------------------------------------


def test_ledger_columns_match_spec():
    assert LEDGER_COLUMNS == [
        "ledger_id",
        "booked_at",
        "amount",
        "currency",
        "entry_type",
        "counterparty_id",
        "counterparty_name",
        "reference",
        "memo",
        "account_code",
        "source_system",
    ]


def test_bank_columns_match_spec():
    assert BANK_COLUMNS == [
        "bank_txn_id",
        "value_date",
        "amount",
        "currency",
        "counterparty_name_raw",
        "reference_raw",
        "description",
        "bank_account",
        "statement_id",
        "direction",
    ]


def test_match_link_columns_match_spec():
    assert MATCH_LINK_COLUMNS == [
        "ledger_id",
        "bank_txn_id",
        "relation",
        "defect",
        "case_id",
        "note",
    ]


# ---------------------------------------------------------------------------
# Acceptance 1: two pass-1 generations, byte-identical
# ---------------------------------------------------------------------------


def _hash_csv(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_two_pass1_generations_are_byte_identical(tmp_path):
    config = GeneratorConfig(seed=REFERENCE_SEED, pass_number=1, n_cases=REFERENCE_N_CASES)
    ds_a = generate(config)
    ds_b = generate(config)

    assert ds_a.ledger_rows == ds_b.ledger_rows
    assert ds_a.bank_rows == ds_b.bank_rows
    assert ds_a.match_link_rows == ds_b.match_link_rows

    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    write_dataset(ds_a, str(dir_a))
    write_dataset(ds_b, str(dir_b))
    for name in ("ledger.csv", "bank.csv", "match_links.csv"):
        assert _hash_csv(dir_a / name) == _hash_csv(dir_b / name), f"{name} differs between runs"


def test_generation_is_deterministic_regardless_of_process(tmp_path):
    """A second, independent call (fresh RNGs from scratch) still matches."""
    config = GeneratorConfig(seed=7, pass_number=1, n_cases=500)
    a = generate(config)
    b = generate(config)
    assert a.ledger_rows == b.ledger_rows
    assert a.bank_rows == b.bank_rows
    assert a.match_link_rows == b.match_link_rows


# ---------------------------------------------------------------------------
# Acceptance 2: defect histogram within tolerance of §4.2
# ---------------------------------------------------------------------------


def test_defect_rates_sum_to_100():
    assert sum(DEFECT_RATES.values(), Decimal("0")) == Decimal("100.0")


def test_defect_histogram_matches_spec_exactly(pass1_dataset):
    expected = defect_counts(REFERENCE_N_CASES)
    assert dict(pass1_dataset.summary.defect_histogram) == dict(expected)
    # Every documented class present, none renamed/dropped.
    assert set(pass1_dataset.summary.defect_histogram) == set(DEFECT_RATES)


def test_defect_histogram_within_tolerance_for_uneven_n():
    """Non-multiple-of-25000 n_cases: rounding may drift by at most one case per
    class (largest-remainder method), and the total must still equal n_cases."""
    n = 3333
    counts = defect_counts(n)
    assert sum(counts.values()) == n
    for name, rate in DEFECT_RATES.items():
        expected = float(rate) / 100.0 * n
        assert abs(counts[name] - expected) < 1.0


# ---------------------------------------------------------------------------
# Cardinality (spec §4.1: mostly 1:1; duplicates/partials 1:2; orphans 1:0 / 0:1)
# ---------------------------------------------------------------------------


def test_cardinalities(pass1_dataset):
    links_by_case = {}
    for row in pass1_dataset.match_link_rows:
        links_by_case.setdefault(row["case_id"], []).append(row)

    ledger_ids = {r["ledger_id"] for r in pass1_dataset.ledger_rows}
    bank_ids = {r["bank_txn_id"] for r in pass1_dataset.bank_rows}
    linked_ledger_ids = {r["ledger_id"] for r in pass1_dataset.match_link_rows}
    linked_bank_ids = {r["bank_txn_id"] for r in pass1_dataset.match_link_rows}

    for case_id, links in links_by_case.items():
        defect = links[0]["defect"]
        if defect in ("partial_payment", "duplicate"):
            assert len(links) == 2, f"{defect} case {case_id} should have 2 links, got {len(links)}"
        else:
            assert len(links) == 1, f"{defect} case {case_id} should have 1 link, got {len(links)}"

    orphan_ledger_count = sum(1 for r in pass1_dataset.ledger_rows if r["ledger_id"] not in linked_ledger_ids)
    orphan_bank_count = sum(1 for r in pass1_dataset.bank_rows if r["bank_txn_id"] not in linked_bank_ids)
    expected = defect_counts(REFERENCE_N_CASES)
    assert orphan_ledger_count == expected["orphan_ledger"]
    assert orphan_bank_count == expected["orphan_bank"]

    # Every ledger/bank id referenced by a link actually exists.
    assert linked_ledger_ids <= ledger_ids
    assert linked_bank_ids <= bank_ids


def test_partial_payment_legs_sum_to_ledger_amount(pass1_dataset):
    ledger_by_id = {r["ledger_id"]: r for r in pass1_dataset.ledger_rows}
    bank_by_id = {r["bank_txn_id"]: r for r in pass1_dataset.bank_rows}
    by_case = {}
    for row in pass1_dataset.match_link_rows:
        if row["defect"] == "partial_payment":
            by_case.setdefault(row["case_id"], []).append(row)
    assert by_case, "expected at least one partial_payment case in the reference batch"
    for case_id, links in by_case.items():
        ledger_amount = Decimal(ledger_by_id[links[0]["ledger_id"]]["amount"])
        total = sum(Decimal(bank_by_id[l["bank_txn_id"]]["amount"]) for l in links)
        assert total == ledger_amount


def test_duplicate_legs_share_amount_and_reference(pass1_dataset):
    ledger_by_id = {r["ledger_id"]: r for r in pass1_dataset.ledger_rows}
    bank_by_id = {r["bank_txn_id"]: r for r in pass1_dataset.bank_rows}
    by_case = {}
    for row in pass1_dataset.match_link_rows:
        if row["defect"] == "duplicate":
            by_case.setdefault(row["case_id"], []).append(row)
    assert by_case
    for case_id, links in by_case.items():
        relations = sorted(l["relation"] for l in links)
        assert relations == ["duplicate", "exact"]
        amounts = {bank_by_id[l["bank_txn_id"]]["amount"] for l in links}
        assert len(amounts) == 1
        ledger_amount = ledger_by_id[links[0]["ledger_id"]]["amount"]
        assert amounts == {ledger_amount}


def test_baseline_name_noise_rate_is_approximately_85_percent(pass1_dataset):
    """Spec §4.3: ~85% of bank records get a perturbed counterparty name. Excludes
    `malformed`, which always forces its own (different) name transform."""
    ledger_by_id = {r["ledger_id"]: r for r in pass1_dataset.ledger_rows}
    bank_by_id = {r["bank_txn_id"]: r for r in pass1_dataset.bank_rows}
    noisy = total = 0
    for link in pass1_dataset.match_link_rows:
        if link["defect"] == "malformed":
            continue
        bank_row = bank_by_id[link["bank_txn_id"]]
        ledger_row = ledger_by_id[link["ledger_id"]]
        total += 1
        if bank_row["counterparty_name_raw"] != ledger_row["counterparty_name"]:
            noisy += 1
    rate = noisy / total
    assert 0.80 <= rate <= 0.90, f"baseline name noise rate {rate:.3f} drifted far from ~85%"


def test_guardrail_bait_defects_shaped_correctly(pass1_dataset):
    ledger_by_id = {r["ledger_id"]: r for r in pass1_dataset.ledger_rows}
    bank_by_id = {r["bank_txn_id"]: r for r in pass1_dataset.bank_rows}
    for row in pass1_dataset.match_link_rows:
        if row["defect"] == "zero_amount":
            assert Decimal(bank_by_id[row["bank_txn_id"]]["amount"]) == Decimal("0.00")
        elif row["defect"] == "negative_amount":
            ledger_amount = Decimal(ledger_by_id[row["ledger_id"]]["amount"])
            bank_amount = Decimal(bank_by_id[row["bank_txn_id"]]["amount"])
            assert bank_amount == -ledger_amount


# ---------------------------------------------------------------------------
# Acceptance 4: pass 2 references disjoint from pass 1; counterparty universe equal
# ---------------------------------------------------------------------------


def test_pass2_references_disjoint_from_pass1(pass1_dataset, pass2_dataset):
    refs1 = {r["reference"] for r in pass1_dataset.ledger_rows}
    refs2 = {r["reference"] for r in pass2_dataset.ledger_rows}
    assert refs1.isdisjoint(refs2)
    assert len(refs1) == len(pass1_dataset.ledger_rows)  # references unique within a pass
    assert len(refs2) == len(pass2_dataset.ledger_rows)


def test_pass2_has_different_transactions_than_pass1(pass1_dataset, pass2_dataset):
    ledger_ids_1 = {r["ledger_id"] for r in pass1_dataset.ledger_rows}
    ledger_ids_2 = {r["ledger_id"] for r in pass2_dataset.ledger_rows}
    assert ledger_ids_1.isdisjoint(ledger_ids_2)


def test_counterparty_universe_identical_across_passes():
    universe_seed_only = build_counterparty_universe(REFERENCE_SEED)
    # Independent of pass_number and n_cases entirely -- it never touches the case
    # stream (law L5).
    again = build_counterparty_universe(REFERENCE_SEED)
    assert universe_seed_only == again
    assert len(universe_seed_only) == 800


def test_pass2_defect_shape_matches_pass1(pass1_dataset, pass2_dataset):
    """Law L6: pass 2 must not be easier -- same documented mix, exactly."""
    assert dict(pass1_dataset.summary.defect_histogram) == dict(pass2_dataset.summary.defect_histogram)


def test_pass2_uses_same_counterparty_ids_as_pass1(pass1_dataset, pass2_dataset):
    ids_1 = {r["counterparty_id"] for r in pass1_dataset.ledger_rows}
    ids_2 = {r["counterparty_id"] for r in pass2_dataset.ledger_rows}
    # Not required to be the exact same subset (different random draws), but both
    # must be subsets of the same shared universe.
    universe_ids = {c.counterparty_id for c in build_counterparty_universe(REFERENCE_SEED)}
    assert ids_1 <= universe_ids
    assert ids_2 <= universe_ids


# ---------------------------------------------------------------------------
# Acceptance 5: printed summary contents
# ---------------------------------------------------------------------------


def test_summary_format_contains_required_fields(pass1_dataset):
    text = pass1_dataset.summary.format()
    assert "ledger.csv=" in text
    assert "bank.csv=" in text
    assert "match_links.csv=" in text
    assert "unique counterparties:" in text
    for name in DEFECT_RATES:
        assert name in text
    assert "overlay:" in text
    assert "fee_offset" in text  # overlay class name called out explicitly


def test_summary_row_counts_are_accurate(pass1_dataset):
    s = pass1_dataset.summary
    assert s.ledger_rows == len(pass1_dataset.ledger_rows)
    assert s.bank_rows == len(pass1_dataset.bank_rows)
    assert s.match_link_rows == len(pass1_dataset.match_link_rows)


def test_unique_counterparty_count_includes_orphan_bank_participants(pass1_dataset):
    # orphan_bank cases draw a counterparty but never emit a ledger row, so the
    # reported unique count must be >= the ledger-only distinct count.
    ledger_only = len({r["counterparty_id"] for r in pass1_dataset.ledger_rows})
    assert pass1_dataset.summary.unique_counterparties >= ledger_only
    assert pass1_dataset.summary.unique_counterparties <= 800


# ---------------------------------------------------------------------------
# Committed tiny fixture stays in sync with the documented exact command
# ---------------------------------------------------------------------------

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "mini_pass1")


def test_committed_fixture_matches_documented_command():
    """Regenerating with the exact documented command reproduces the committed
    fixture byte-for-byte -- guards against silent generator drift."""
    dataset = generate(GeneratorConfig(seed=42, pass_number=1, n_cases=50))
    for name, columns, rows in (
        ("ledger.csv", LEDGER_COLUMNS, dataset.ledger_rows),
        ("bank.csv", BANK_COLUMNS, dataset.bank_rows),
        ("match_links.csv", MATCH_LINK_COLUMNS, dataset.match_link_rows),
    ):
        committed_path = os.path.join(FIXTURE_DIR, name)
        with open(committed_path, newline="", encoding="utf-8") as fh:
            committed_rows = list(csv.DictReader(fh))
        assert committed_rows == rows, f"{name} fixture is stale -- regenerate tests/fixtures/mini_pass1"
