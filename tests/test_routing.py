"""W3: fixture-first acceptance and boundary regressions for Agent 2 routing.

Ground truth (``match_links.csv``) is opened here, in tests, only -- never by
``ledger_sense.routing`` itself (see ``tests/test_routing_isolation.py``).
"""

import csv
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_sense.matching.io import run as matching_run
from ledger_sense.routing import roster
from ledger_sense.routing.classify import CATEGORIES, classify_bank, classify_book, select_pairs
from ledger_sense.routing.clock import compute as clock_compute
from ledger_sense.routing.clock import parse_iso, severity_for, sla_hours_for
from ledger_sense.routing.io import run as routing_run

FIXTURE = Path(__file__).parent / "fixtures" / "mini_pass1"


def rows(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


# ---------------------------------------------------------------------------
# §6.2 -- the ordered bank-side classifier, one hand-built case per rule.
# ---------------------------------------------------------------------------

def test_bank_rule_1_anomalous_amount_beats_everything_else():
    # Even if relation also happens to be duplicate, rule 1 fires first.
    category, detail = classify_bank("anomalous_amount", "duplicate", {"amount": "conflict", "name": "1.0"})
    assert category == "suspect_posting" and "bank-rule-1" in detail


def test_bank_rule_1_currency_conflict():
    category, _ = classify_bank("currency_conflict", "", {})
    assert category == "suspect_posting"


def test_bank_rule_2_duplicate_relation():
    category, detail = classify_bank("ambiguous_evidence", "duplicate", {"amount": "exact", "name": "1.0"})
    assert category == "duplicate" and "bank-rule-2" in detail


def test_bank_rule_3_ledger_already_settled():
    category, detail = classify_bank("ledger_already_settled", "", {"amount": "exact", "name": "1.0"})
    assert category == "amount_mismatch" and "bank-rule-3" in detail


def test_bank_rule_4_conflict_amount_with_name_floor():
    category, detail = classify_bank("ambiguous_evidence", "", {"amount": "conflict", "name": "0.70"})
    assert category == "amount_mismatch" and "bank-rule-4" in detail


def test_bank_rule_4_requires_name_floor_else_falls_through():
    # name just under the 0.70 floor must NOT satisfy rule 4 (or 5/6) -- falls to rule 7.
    category, detail = classify_bank("ambiguous_evidence", "", {"amount": "conflict", "name": "0.69"})
    assert category == "unidentified_counterpart" and "bank-rule-7" in detail


def test_bank_rule_5_stale_date_exact_or_fx_amount():
    for amount_class in ("exact", "fx"):
        category, detail = classify_bank(
            "ambiguous_evidence", "", {"amount": amount_class, "name": "0.70", "date": "0.49"})
        assert category == "timing" and "bank-rule-5" in detail


def test_bank_rule_5_requires_date_below_half_not_at_half():
    # date == 0.50 exactly must NOT satisfy rule 5 (strict '<').
    category, detail = classify_bank(
        "ambiguous_evidence", "", {"amount": "exact", "name": "0.90", "date": "0.50"})
    assert category != "timing" or "bank-rule-5" not in detail


def test_bank_rule_6_partial_amount_with_name_floor():
    category, detail = classify_bank("ambiguous_evidence", "", {"amount": "partial", "name": "0.70", "date": "1.0"})
    assert category == "timing" and "bank-rule-6" in detail


def test_bank_rule_7_catch_all_no_candidate():
    category, detail = classify_bank("no_candidate", "", {})
    assert category == "unidentified_counterpart" and "bank-rule-7" in detail


def test_bank_rule_order_conflict_amount_wins_over_partial_ambiguity():
    # amount=conflict with name>=0.70 must hit rule 4, never fall through to 6/7.
    category, detail = classify_bank("ambiguous_evidence", "", {"amount": "conflict", "name": "1.0", "date": "1.0"})
    assert category == "amount_mismatch" and "bank-rule-4" in detail


@pytest.mark.parametrize("category", CATEGORIES)
def test_five_categories_only(category):
    assert category in {"duplicate", "amount_mismatch", "timing", "unidentified_counterpart", "suspect_posting"}
assert len(CATEGORIES) == 5


# ---------------------------------------------------------------------------
# Book side (§6.2's closing note) -- never_settled / partial band boundaries.
# ---------------------------------------------------------------------------

def test_book_never_settled_is_always_timing():
    category, detail = classify_book("never_settled", Decimal("100.00"), Decimal("100.00"))
    assert category == "timing" and "never_settled" in detail


def test_book_partial_residual_at_or_above_15pct_is_timing():
    category, _ = classify_book("partially_settled", Decimal("100.00"), Decimal("15.00"))
    assert category == "timing"


def test_book_partial_residual_below_15pct_is_amount_mismatch():
    category, _ = classify_book("partially_settled", Decimal("100.00"), Decimal("14.99"))
    assert category == "amount_mismatch"


def test_book_partial_residual_sign_and_ledger_sign_do_not_matter():
    category, _ = classify_book("partially_settled", Decimal("-100.00"), Decimal("-20.00"))
    assert category == "timing"


# ---------------------------------------------------------------------------
# §6.3 -- pair-and-suppress, including the bank_txn_id tie-break.
# ---------------------------------------------------------------------------

def test_pair_selects_bank_subject_whose_top_candidate_is_unclaimed():
    pairs = select_pairs({"BK-1": "LG-1", "BK-2": ""}, {"LG-1"})
    assert pairs == {"BK-1": "LG-1"}


def test_pair_ignores_claimed_ledger_top_candidates():
    pairs = select_pairs({"BK-1": "LG-1"}, {"LG-2"})
    assert pairs == {}


def test_pair_ties_break_by_bank_txn_id_ascending():
    pairs = select_pairs({"BK-9": "LG-1", "BK-2": "LG-1", "BK-5": "LG-1"}, {"LG-1"})
    assert pairs == {"BK-2": "LG-1"}


def test_pair_losers_are_not_silently_dropped_by_the_selector():
    # select_pairs only returns winners; the caller keeps everyone else as an
    # ordinary bank subject -- verified end-to-end below, not here.
    pairs = select_pairs({"BK-9": "LG-1", "BK-2": "LG-1"}, {"LG-1"})
    assert set(pairs) == {"BK-2"}


# ---------------------------------------------------------------------------
# §6.4 -- roster, desk assignment, blake2b individual assignment.
# ---------------------------------------------------------------------------

def test_roster_is_exactly_eleven_people_in_four_desks():
    assert len(roster.ROSTER) == 11
    assert len(roster.DESKS["AR"]) == 3
    assert len(roster.DESKS["AP"]) == 3
    assert len(roster.DESKS["recon_ops"]) == 3
    assert len(roster.DESKS["controller"]) == 2


def test_desk_suspect_posting_always_controller():
    assert roster.desk_for("suspect_posting", inbound=True) == "controller"
    assert roster.desk_for("suspect_posting", inbound=False) == "controller"


def test_desk_duplicate_always_recon_ops():
    assert roster.desk_for("duplicate", inbound=True) == "recon_ops"
    assert roster.desk_for("duplicate", inbound=False) == "recon_ops"


def test_desk_else_ar_if_inbound_else_ap():
    assert roster.desk_for("timing", inbound=True) == "AR"
    assert roster.desk_for("timing", inbound=False) == "AP"
    assert roster.desk_for("amount_mismatch", inbound=True) == "AR"
    assert roster.desk_for("unidentified_counterpart", inbound=False) == "AP"


def test_individual_assignment_is_stable_for_the_same_counterparty():
    first = roster.individual_for("AR", "ACMELOGISTICS")
    second = roster.individual_for("AR", "ACMELOGISTICS")
    assert first == second


def test_individual_assignment_does_not_use_python_hash(monkeypatch):
    # Python's built-in hash() is per-process salted (PYTHONHASHSEED); using
    # it would make individual_for() non-deterministic across processes. Make
    # the built-in explode to prove routing never calls it.
    def exploding_hash(*_args, **_kwargs):
        raise AssertionError("roster.individual_for must never call hash()")
    monkeypatch.setattr("builtins.hash", exploding_hash)
    owner = roster.individual_for("recon_ops", "SOME COUNTERPARTY")
    assert owner in roster.DESKS["recon_ops"]


def test_individual_assignment_uses_counterparty_not_a_transaction_id():
    # Two different "transaction ids" for the same counterparty must land on
    # the same person; assign() never even sees a transaction id.
    owner_a, _ = roster.assign("timing", True, "ACME CORP")
    owner_b, _ = roster.assign("timing", True, "ACME CORP")
    assert owner_a == owner_b


def test_assign_basis_names_the_desk_and_the_individual():
    owner, basis = roster.assign("suspect_posting", True, "ACME CORP")
    assert "controller" in basis and owner.owner_id in basis


# ---------------------------------------------------------------------------
# §6.5 -- the SLA clock: pure function of (opened_at, sla_hours, now).
# ---------------------------------------------------------------------------

def test_suspect_posting_is_always_p1_base_4h_regardless_of_amount():
    for amount in (Decimal("0.01"), Decimal("50"), Decimal("999999")):
        assert severity_for("suspect_posting", amount) == "P1"
    assert sla_hours_for("suspect_posting", "P1") == Decimal(4) * Decimal("0.5")


@pytest.mark.parametrize("amount,expected", [
    (Decimal("9999.99"), "P2"),
    (Decimal("1000.00"), "P2"),
    (Decimal("999.99"), "P3"),
    (Decimal("10000.00"), "P1"),
])
def test_severity_amount_buckets(amount, expected):
    assert severity_for("timing", amount) == expected


@pytest.mark.parametrize("category,severity,expected", [
    ("duplicate", "P1", Decimal(24) * Decimal("0.5")),
    ("amount_mismatch", "P2", Decimal(48) * Decimal("1.0")),
    ("timing", "P3", Decimal(120) * Decimal("1.5")),
    ("unidentified_counterpart", "P2", Decimal(72) * Decimal("1.0")),
])
def test_sla_hours_formula(category, severity, expected):
    assert sla_hours_for(category, severity) == expected


def test_clock_due_at_is_opened_at_plus_sla_hours():
    opened = parse_iso("2026-01-01T00:00:00Z")
    now = parse_iso("2026-01-01T00:00:00Z")
    result = clock_compute(opened, Decimal(10), now)
    assert result.due_at == parse_iso("2026-01-01T10:00:00Z")


def test_clock_on_track_well_inside_the_window():
    opened = parse_iso("2026-01-01T00:00:00Z")
    now = parse_iso("2026-01-01T01:00:00Z")  # 1h elapsed of a 10h window -> 90% left
    result = clock_compute(opened, Decimal(10), now)
    assert result.sla_state == "on_track"
    assert not result.at_risk and not result.breached


def test_clock_at_risk_just_under_25pct_remaining():
    opened = parse_iso("2026-01-01T00:00:00Z")
    now = parse_iso("2026-01-01T07:30:01Z")  # < 2.5h (25% of 10h) remaining
    result = clock_compute(opened, Decimal(10), now)
    assert result.at_risk and not result.breached
    assert result.sla_state == "at_risk"


def test_clock_not_yet_at_risk_at_exactly_25pct_remaining():
    opened = parse_iso("2026-01-01T00:00:00Z")
    now = parse_iso("2026-01-01T07:30:00Z")  # exactly 2.5h (25%) remaining
    result = clock_compute(opened, Decimal(10), now)
    assert not result.at_risk
    assert result.sla_state == "on_track"


def test_clock_breached_exactly_at_due_at():
    opened = parse_iso("2026-01-01T00:00:00Z")
    now = parse_iso("2026-01-01T10:00:00Z")
    result = clock_compute(opened, Decimal(10), now)
    assert result.breached and result.sla_state == "breached"


def test_clock_breached_hours_remaining_is_negative():
    opened = parse_iso("2026-01-01T00:00:00Z")
    now = parse_iso("2026-01-01T12:00:00Z")
    result = clock_compute(opened, Decimal(10), now)
    assert result.breached
    assert result.hours_remaining == Decimal("-2")


def test_clock_never_calls_datetime_now(monkeypatch):
    import datetime as datetime_module

    class ExplodingDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("routing.clock must never call datetime.now()")
    monkeypatch.setattr(datetime_module, "datetime", ExplodingDatetime)
    opened = ExplodingDatetime.fromisoformat("2026-01-01T00:00:00+00:00")
    now = ExplodingDatetime.fromisoformat("2026-01-01T01:00:00+00:00")
    clock_compute(opened, Decimal(10), now)


# ---------------------------------------------------------------------------
# End-to-end against the committed mini fixture (real Agent 1 output, no mocks).
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {"duplicate", "amount_mismatch", "timing", "unidentified_counterpart", "suspect_posting"}


def _route_fixture(tmp_path, as_of="2026-06-01T00:00:00Z"):
    matching_out = tmp_path / "matching_out"
    matching_run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", matching_out)
    routed = tmp_path / "routed"
    exception_rows, queue_rows = routing_run(
        matching_out / "match_outcomes.csv", matching_out / "ledger_settlements.csv",
        FIXTURE / "ledger.csv", FIXTURE / "bank.csv", as_of, routed)
    return routed, exception_rows, queue_rows


def test_mini_fixture_output_contract_columns(tmp_path):
    routed, _, _ = _route_fixture(tmp_path)
    exceptions = rows(routed / "exceptions.csv")
    queues = rows(routed / "owner_queues.csv")
    assert list(exceptions[0]) == [
        "exception_id", "pass_id", "subject_kind", "bank_txn_id", "ledger_id",
        "category", "classification_detail", "match_status", "match_reason",
        "settlement_reason", "counterparty_key", "counterparty_label",
        "amount", "currency", "severity", "owner_id", "owner_name", "owner_team",
        "assignment_basis", "opened_at", "sla_hours", "due_at",
        "hours_remaining", "sla_state", "sla_display", "queue_position",
        "age_days", "evidence",
    ]
    assert list(queues[0]) == [
        "owner_id", "owner_name", "owner_team", "open_exceptions",
        "n_p1", "n_p2", "n_p3", "earliest_due_at", "n_breached",
    ]
    assert len(queues) == 11


def test_mini_fixture_every_routed_row_has_category_owner_and_clock(tmp_path):
    _, exception_rows, _ = _route_fixture(tmp_path)
    assert exception_rows, "fixture should produce at least one exception"
    for row in exception_rows:
        assert row["category"] in VALID_CATEGORIES
        assert row["owner_id"] and row["owner_name"] and row["owner_team"]
        assert row["due_at"] and row["opened_at"] and row["sla_hours"]
        assert row["subject_kind"] in {"bank", "ledger", "pair"}


def test_mini_fixture_ground_truth_duplicates_route_as_duplicate(tmp_path):
    routed, exception_rows, _ = _route_fixture(tmp_path)
    truth = rows(FIXTURE / "match_links.csv")
    by_bank = {row["bank_txn_id"]: row for row in exception_rows if row["bank_txn_id"]}
    duplicate_legs = [link["bank_txn_id"] for link in truth if link["relation"] == "duplicate"]
    assert duplicate_legs, "fixture should contain at least one duplicate case"
    for bank_txn_id in duplicate_legs:
        assert by_bank[bank_txn_id]["category"] == "duplicate"


def test_mini_fixture_guardrail_bait_routes_as_suspect_posting(tmp_path):
    routed, exception_rows, _ = _route_fixture(tmp_path)
    truth = rows(FIXTURE / "match_links.csv")
    by_bank = {row["bank_txn_id"]: row for row in exception_rows if row["bank_txn_id"]}
    bait_ids = [link["bank_txn_id"] for link in truth if link["defect"] in ("negative_amount", "zero_amount")]
    assert bait_ids, "fixture should contain at least one guardrail-bait case"
    hit = sum(1 for bank_txn_id in bait_ids
              if bank_txn_id in by_bank and by_bank[bank_txn_id]["category"] == "suspect_posting")
    assert hit / len(bait_ids) >= 0.95


def test_mini_fixture_pair_and_suppress_never_double_reports_a_ledger(tmp_path):
    _, exception_rows, _ = _route_fixture(tmp_path)
    ledger_ids = [row["ledger_id"] for row in exception_rows if row["ledger_id"]]
    assert len(ledger_ids) == len(set(ledger_ids)), "a ledger id must appear on at most one exception row"
    bank_ids = [row["bank_txn_id"] for row in exception_rows if row["bank_txn_id"]]
    assert len(bank_ids) == len(set(bank_ids)), "a bank txn id must appear on at most one exception row"


def test_mini_fixture_exception_ids_are_unique(tmp_path):
    _, exception_rows, _ = _route_fixture(tmp_path)
    ids = [row["exception_id"] for row in exception_rows]
    assert len(ids) == len(set(ids))


def test_mini_fixture_owner_queue_counts_reconcile_with_exceptions(tmp_path):
    _, exception_rows, queue_rows = _route_fixture(tmp_path)
    assert sum(row["open_exceptions"] for row in queue_rows) == len(exception_rows)
    for queue in queue_rows:
        owned = [row for row in exception_rows if row["owner_id"] == queue["owner_id"]]
        assert queue["open_exceptions"] == len(owned)
        assert queue["n_p1"] == sum(row["severity"] == "P1" for row in owned)
        assert queue["n_p2"] == sum(row["severity"] == "P2" for row in owned)
        assert queue["n_p3"] == sum(row["severity"] == "P3" for row in owned)
        assert queue["n_breached"] == sum(row["sla_state"] == "breached" for row in owned)


def test_mini_fixture_queue_positions_are_contiguous_sorted_by_due_at_then_id(tmp_path):
    _, exception_rows, _ = _route_fixture(tmp_path)
    by_owner = {}
    for row in exception_rows:
        by_owner.setdefault(row["owner_id"], []).append(row)
    for owned in by_owner.values():
        expected_order = sorted(owned, key=lambda r: (r["due_at"], r["exception_id"]))
        expected_order.sort(key=lambda r: r["queue_position"])
        positions = sorted(row["queue_position"] for row in owned)
        assert positions == list(range(1, len(owned) + 1))
        by_position = {row["queue_position"]: row["exception_id"] for row in owned}
        due_sorted_ids = [r["exception_id"] for r in sorted(owned, key=lambda r: (r["due_at"], r["exception_id"]))]
        assert [by_position[i] for i in range(1, len(owned) + 1)] == due_sorted_ids


def test_mini_fixture_two_full_reruns_are_byte_identical(tmp_path):
    matching_out = tmp_path / "matching_out"
    matching_run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", matching_out)
    routing_run(matching_out / "match_outcomes.csv", matching_out / "ledger_settlements.csv",
                FIXTURE / "ledger.csv", FIXTURE / "bank.csv", "2026-06-01T00:00:00Z", tmp_path / "a")
    routing_run(matching_out / "match_outcomes.csv", matching_out / "ledger_settlements.csv",
                FIXTURE / "ledger.csv", FIXTURE / "bank.csv", "2026-06-01T00:00:00Z", tmp_path / "b")
    for name in ("exceptions.csv", "owner_queues.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_exception_id_collision_is_a_hard_error(monkeypatch):
    from ledger_sense.routing.engine import Subject, route

    subject = Subject(
        subject_kind="ledger", bank_txn_id="", ledger_id="LG-1", category="timing",
        classification_detail="test", match_status="", match_reason="", settlement_reason="never_settled",
        counterparty_key="X", counterparty_label="X", amount=Decimal("10.00"), currency="USD",
        inbound=True, opened_at=parse_iso("2026-01-01T00:00:00Z"),
    )
    with pytest.raises(ValueError, match="Duplicate exception_id"):
        route([subject, subject], parse_iso("2026-06-01T00:00:00Z"))


def test_cli_independent_process_determinism(tmp_path):
    matching_out = tmp_path / "matching_out"
    matching_run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", matching_out)
    for seed in ("1", "99"):
        completed = subprocess.run([
            sys.executable, "-m", "ledger_sense.routing",
            "--outcomes", str(matching_out / "match_outcomes.csv"),
            "--settlements", str(matching_out / "ledger_settlements.csv"),
            "--ledger", str(FIXTURE / "ledger.csv"), "--bank", str(FIXTURE / "bank.csv"),
            "--as-of", "2026-06-01T00:00:00Z", "--out-dir", str(tmp_path / seed),
        ], env={**os.environ, "PYTHONHASHSEED": seed}, check=True, capture_output=True, text=True)
        assert "exceptions=" in completed.stdout
    for name in ("exceptions.csv", "owner_queues.csv"):
        assert (tmp_path / "1" / name).read_bytes() == (tmp_path / "99" / name).read_bytes()


# ---------------------------------------------------------------------------
# Opt-in full-batch acceptance check against §4's reference dataset (seed=42).
# ---------------------------------------------------------------------------

def test_local_reference_batch_acceptance(tmp_path):
    """Measured against the real seed=42 pass-1 batch. Ground truth is opened
    here, in the test, only -- never inside ledger_sense.routing. Numbers are
    reported, not tuned to hit the targets (spec §6.8)."""
    batch = Path(os.environ.get("ROUTING_BATCH_DIR", "data/pass1"))
    if not (batch / "match_outcomes.csv").exists() or not (batch / "ledger_settlements.csv").exists():
        pytest.skip("Generate data/pass1 (ledger.csv/bank.csv + Agent 1 outputs) to run the full-batch regression")

    as_of = "2026-06-01T00:00:00Z"
    routing_run(batch / "match_outcomes.csv", batch / "ledger_settlements.csv",
                batch / "ledger.csv", batch / "bank.csv", as_of, tmp_path / "first")
    routing_run(batch / "match_outcomes.csv", batch / "ledger_settlements.csv",
                batch / "ledger.csv", batch / "bank.csv", as_of, tmp_path / "second")
    for name in ("exceptions.csv", "owner_queues.csv"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()

    exception_rows = rows(tmp_path / "first" / "exceptions.csv")
    queue_rows = rows(tmp_path / "first" / "owner_queues.csv")
    outcomes = rows(batch / "match_outcomes.csv")
    settlements = {row["ledger_id"]: row for row in rows(batch / "ledger_settlements.csv")}

    # STR: computed purely from Agent 1's own outputs (never ground truth) --
    # "no exception row = straight-through" (§6.7), and a bank line that
    # matched but whose ledger residual is still open is NOT straight-through.
    total_bank = len(outcomes)
    straight_through = sum(
        1 for row in outcomes
        if row["status"] == "matched" and settlements.get(row["ledger_id"], {}).get("reason") == "fully_settled"
    )
    str_pct = straight_through / total_bank * 100
    print(f"\nStraight-through rate = {straight_through}/{total_bank} = {str_pct:.2f}%")

    assert all(
        row["category"] in VALID_CATEGORIES and row["owner_id"] and row["owner_name"] and row["due_at"]
        for row in exception_rows
    )
    assert sum(int(row["open_exceptions"]) for row in queue_rows) == len(exception_rows)
    ids = [row["exception_id"] for row in exception_rows]
    assert len(ids) == len(set(ids))

    truth = {row["bank_txn_id"]: row for row in rows(batch / "match_links.csv")}
    by_bank = {row["bank_txn_id"]: row for row in exception_rows if row["bank_txn_id"]}
    duplicate_legs = [bid for bid, link in truth.items() if link["relation"] == "duplicate"]
    duplicate_hit = sum(1 for bid in duplicate_legs if by_bank.get(bid, {}).get("category") == "duplicate")
    print(f"Ground-truth duplicates -> duplicate: {duplicate_hit}/{len(duplicate_legs)}")

    bait_ids = [bid for bid, link in truth.items() if link["defect"] in ("negative_amount", "zero_amount")]
    bait_hit = sum(1 for bid in bait_ids if by_bank.get(bid, {}).get("category") == "suspect_posting")
    bait_pct = bait_hit / len(bait_ids) * 100 if bait_ids else 100.0
    print(f"Guardrail bait -> suspect_posting: {bait_hit}/{len(bait_ids)} = {bait_pct:.2f}%")

    # Targets, measured honestly (report a miss rather than tune the data).
    assert 85.0 <= str_pct <= 90.0, f"STR {str_pct:.2f}% is far outside the ~87-88% target band"
    assert duplicate_hit == len(duplicate_legs)
    assert bait_pct >= 95.0
