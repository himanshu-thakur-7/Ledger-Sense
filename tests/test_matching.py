"""W2: fixture-first acceptance and boundary regressions; truth stays in tests."""

import csv
import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

import pytest

from ledger_sense.data.models import BankTransaction, LedgerEntry
from ledger_sense.matching import CandidateIndex, match, run
from ledger_sense.matching.adjudication import NoneAdjudicator, StubAdjudicator, Verdict
from ledger_sense.matching.scoring import Features, ScoredCandidate, acceptance, amount_class, date_similarity, score_candidate

FIXTURE = Path(__file__).parent / "fixtures" / "mini_pass1"


def rows(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def ledger(id="LG-1", amount="100.00", reference="INV-1000001", name="Acme Logistics"):
    return LedgerEntry(id, "2026-01-10T00:00:00Z", Decimal(amount), "USD",
                       "invoice_payment", "CP-1", name, reference, "", "1200", "billing")


def bank(id="BK-1", amount="100.00", reference="INV-1000001", name="Acme Logistics"):
    return BankTransaction(id, "2026-01-10T00:00:00Z", Decimal(amount), "USD",
                           name, reference, "", "ACCT-USD-01", "STMT-1", "credit")


def test_mini_fixture_contract_precision_and_repeatability(tmp_path):
    result = run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", tmp_path / "a")
    run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", tmp_path / "b")
    outcomes = rows(tmp_path / "a" / "match_outcomes.csv")
    settlements = rows(tmp_path / "a" / "ledger_settlements.csv")
    assert list(outcomes[0]) == [
        "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin",
        "reason", "reason_detail", "matched_amount", "residual_after", "candidates",
        "features", "llm_model", "llm_confidence", "llm_is_stub",
    ]
    assert list(settlements[0]) == [
        "ledger_id", "ledger_amount", "matched_amount", "residual", "n_parts",
        "bank_txn_ids", "fully_settled", "reason",
    ]
    assert len(outcomes) == len(rows(FIXTURE / "bank.csv"))
    assert len(settlements) == len(rows(FIXTURE / "ledger.csv"))
    truth = {r["bank_txn_id"]: r for r in rows(FIXTURE / "match_links.csv")}
    for outcome in outcomes:
        link = truth.get(outcome["bank_txn_id"])
        if outcome["status"] == "matched":
            assert link and outcome["ledger_id"] == link["ledger_id"]
            assert link["defect"] not in {"negative_amount", "zero_amount"}
        assert outcome["llm_is_stub"] == "True"
        assert len(json.loads(outcome["candidates"])) <= 40
    duplicate_ledgers = {v["ledger_id"] for v in truth.values() if v["defect"] == "duplicate"}
    for id in duplicate_ledgers:
        legs = [r for r in outcomes if r["ledger_id"] == id]
        assert sum(r["status"] == "matched" for r in legs) == 1
        assert sum(r["reason"] == "duplicate_of_matched" for r in legs) == 1
    assert result.llm_calls == 0 and result.llm_is_stub
    for name in ("match_outcomes.csv", "ledger_settlements.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_block_union_window_fallback_and_no_scan():
    entries = [ledger("ref", "900", "QUOTED", "Other Party"),
               ledger("window", "104.99", "OTHER"),
               ledger("outside", "105.00", "OUTSIDE"),
               ledger("partial", "200", "PART"),
               ledger("wrong-key", "100", "X", "Different Party")]
    index = CandidateIndex(entries)
    assert {e.ledger_id for e in index.candidates(bank(reference="QUOTED"))} == {"ref", "window"}
    assert {e.ledger_id for e in index.candidates(bank(reference="", amount="50"))} == {
        "window", "outside", "partial"}
    assert index.candidates(bank(reference="", name="Unblocked Party")) == []
    # Prove queries do not iterate the full entry map, including the fallback path.
    class NoScan(dict):
        def __iter__(self):
            raise AssertionError("full scan")
        def values(self):
            raise AssertionError("full scan")
        def items(self):
            raise AssertionError("full scan")
    index.entries = NoScan(index.entries)
    assert index.candidates(bank(reference="", amount="50"))
    assert index.candidates(bank(reference="", name="Unblocked Party")) == []


def test_block_cap_closest_amounts_and_stable_ties():
    entries = [ledger(f"LG-{i:03}", str(100 + i), "SAME") for i in range(60)]
    index = CandidateIndex(reversed(entries))
    assert [e.ledger_id for e in index.candidates(bank(reference="SAME"))] == [
        f"LG-{i:03}" for i in range(40)]


@pytest.mark.parametrize("book,posted,expected", [
    (10000, 10000, "exact"), (-10000, -10000, "exact"), (0, 0, "exact"),
    (10000, 10350, "fx"), (10000, 10351, "conflict"),
    (100000, 100500, "fx"), (100000, 100501, "conflict"),
    (100, 75, "fx"), (100, 74, "partial"), (10000, 1500, "partial"),
    (10000, 8500, "partial"), (10000, 1499, "conflict"),
    (10000, 8501, "conflict"), (10000, -10000, "conflict"),
    (10000, 0, "conflict"), (0, 1, "conflict"),
])
def test_integer_amount_boundaries(book, posted, expected):
    assert amount_class(book, posted) == expected


def test_missing_wrong_and_fuzzy_reference_weights():
    entry = ledger()
    missing = score_candidate(bank(reference=""), entry)
    wrong = score_candidate(bank(reference="UNRELATED"), entry)
    fuzzy = score_candidate(bank(reference="INV-1000002"), entry)
    assert missing.features.reference is None and missing.score == 100
    assert wrong.features.reference == 0 and wrong.score == 60
    assert fuzzy.features.reference == Decimal("0.6") and fuzzy.score == 84


def test_known_wrong_reference_is_counter_evidence_even_when_similar():
    entry = ledger()
    score = score_candidate(bank(reference="INV-1000002"), entry, known_reference=True)
    assert score.features.reference == 0 and score.score == 60


@pytest.mark.parametrize("score,margin,count,expected", [
    ("88", "6", 2, "high_confidence"), ("87.999", "6", 2, ""),
    ("88", "5.999", 2, ""), ("88", "0", 1, "high_confidence"),
])
def test_acceptance_threshold_boundaries(score, margin, count, expected):
    candidate = ScoredCandidate(ledger(), Decimal(score),
                                Features(None, "exact", Decimal(1), Decimal(1), Decimal(1), Decimal(1)))
    assert acceptance(candidate, Decimal(margin), count) == expected


@pytest.mark.parametrize("score,name,reference,expected", [
    ("78", "0.70", "1", True), ("77.999", "1", "1", False),
    ("80", "0.6999", "1", False), ("80", "1", "0.6", False),
])
def test_partial_exception_boundaries(score, name, reference, expected):
    candidate = ScoredCandidate(ledger(), Decimal(score), Features(
        Decimal(reference), "partial", Decimal("0.55"), Decimal(name), Decimal(1), Decimal(1)))
    assert bool(acceptance(candidate, Decimal(0), 2)) is expected


@pytest.mark.parametrize("date,expected", [
    ("2026-01-13T00:00:00Z", "1"), ("2026-01-07T00:00:00Z", "1"),
    ("2026-02-10T12:00:00Z", "0.5"), ("2026-03-11T00:00:00Z", "0"),
])
def test_date_decay_boundaries(date, expected):
    assert date_similarity(ledger().booked_at, date) == Decimal(expected)


def test_exact_short_circuit_skips_fuzzy(monkeypatch):
    def fail(*args):
        raise AssertionError("fuzzy work on exact match")
    monkeypatch.setattr("ledger_sense.matching.scoring.name_similarity", fail)
    score = score_candidate(bank(name="Entirely Different"), ledger())
    assert score.score == 100
    assert score.features.short_circuit
    assert score.features.name is None  # skipped, never invented evidence


@pytest.mark.parametrize("amount,currency,reason", [
    ("0", "USD", "anomalous_amount"), ("-100", "USD", "anomalous_amount"),
    ("100", "EUR", "currency_conflict"),
])
def test_interlock_even_after_adjudicator_accept(amount, currency, reason):
    class RecklessStub(StubAdjudicator):
        def adjudicate(self, questions):
            return [Verdict(q.bank.bank_txn_id, q.candidates[0].ledger.ledger_id,
                            True, Decimal("1"), "test") for q in questions]
    result = match([ledger()], [replace(bank(amount=amount), currency=currency)], RecklessStub())
    assert result.outcomes[0]["status"] != "matched"
    assert result.outcomes[0]["reason"] == reason
    assert result.settlements[0]["matched_amount"] == "0.00"


def test_greedy_ties_duplicates_partials_and_signed_capacity():
    result = match([ledger(amount="-100")],
                   [bank("BK-2", "-100"), bank("BK-1", "-100")])
    a, b = result.outcomes
    assert a["bank_txn_id"] == "BK-1" and a["status"] == "matched"
    assert b["relation"] == "duplicate" and b["matched_amount"] == "0.00"
    assert result.settlements[0]["matched_amount"] == "-100.00"
    partial = match([ledger()], [bank("BK-2", "40"), bank("BK-1", "60")])
    assert all(r["reason"] == "PARTIAL_WITH_EXACT_REFERENCE" for r in partial.outcomes)
    assert partial.settlements[0]["n_parts"] == 2
    assert partial.settlements[0]["residual"] == "0.00"
    overflow = match([ledger()], [bank("BK-2", "60"), bank("BK-1", "60")])
    assert sum(r["status"] == "matched" for r in overflow.outcomes) == 1
    assert overflow.settlements[0]["residual"] == "40.00"


def test_duplicate_tolerates_independent_statement_noise():
    first = bank("BK-1")
    second = replace(bank("BK-2", name="ACME"), bank_account="ACCT-USD-02",
                     value_date="2026-01-12T00:00:00Z")
    result = match([ledger()], [first, second])
    assert result.outcomes[1]["reason"] == "duplicate_of_matched"
    assert result.outcomes[1]["matched_amount"] == "0.00"


@pytest.mark.parametrize("amount,matched,residual", [
    ("103.50", "100.00", "0.00"), ("96.50", "96.50", "3.50"),
    ("-103.50", "-100.00", "0.00"), ("-96.50", "-96.50", "-3.50"),
])
def test_fx_never_over_settles_and_underpayment_preserves_residual(amount, matched, residual):
    entry = ledger(amount="-100" if amount.startswith("-") else "100")
    result = match([entry], [bank(amount=amount)])
    assert result.outcomes[0]["status"] == "matched"
    assert result.settlements[0]["matched_amount"] == matched
    assert result.settlements[0]["residual"] == residual


def test_both_tiers_share_capacity_and_call_counter_is_measured():
    class RecordingProvider(StubAdjudicator):
        llm_is_stub = False
        model = "test-provider"

        def __init__(self):
            super().__init__()
            self.batches = []

        def adjudicate(self, questions):
            self.batches.append(questions)
            self.llm_calls += 1
            return [Verdict(q.bank.bank_txn_id, q.candidates[0].ledger.ledger_id,
                            True, Decimal("0.95"), "test") for q in questions]

    provider = RecordingProvider()
    result = match([ledger()], [bank("BK-2", reference="WRONG"), bank("BK-1")], provider)
    assert result.outcomes[0]["status"] == "matched"
    assert result.outcomes[1]["reason"] == "ledger_already_settled"
    assert result.outcomes[1]["matched_amount"] == "0.00"
    assert result.llm_calls == 1 and not result.llm_is_stub
    assert len(provider.batches) == 1
    question = provider.batches[0][0]
    assert question.reason and question.bank and len(question.candidates) <= 3
    assert question.candidates[0].features.reference == 0
    result = match([ledger()], [bank()], provider)
    assert result.llm_calls == 0  # no batch for an all-cheap run, even on a reused adapter


def test_adjudicator_cannot_switch_away_from_best_interlock():
    class SwitchStub(StubAdjudicator):
        def adjudicate(self, questions):
            return [Verdict(q.bank.bank_txn_id, "SAFE", True, Decimal(1), "test") for q in questions]
    unsafe = replace(ledger("UNSAFE"), currency="EUR")
    safe = ledger("SAFE", reference="OTHER")
    result = match([unsafe, safe], [bank()], SwitchStub())
    assert result.outcomes[0]["reason"] == "currency_conflict"
    assert result.outcomes[0]["status"] != "matched"


def test_adjudicator_rechecks_replacement_interlock():
    class SwitchStub(StubAdjudicator):
        def adjudicate(self, questions):
            return [Verdict(q.bank.bank_txn_id, "UNSAFE", True, Decimal(1), "test") for q in questions]
    safe = ledger("SAFE", reference="OTHER")
    unsafe = replace(ledger("UNSAFE", reference="OTHER"), currency="EUR")
    result = match([unsafe, safe], [bank(reference="WRONG")], SwitchStub())
    assert result.outcomes[0]["reason"] == "currency_conflict"
    assert all(r["matched_amount"] == "0.00" for r in result.settlements)


def test_reject_below_45_and_escalate_at_45(monkeypatch):
    original = score_candidate(bank(), ledger())
    for score, expected in (("44.999", "rejected"), ("45", "escalated")):
        monkeypatch.setattr("ledger_sense.matching.engine.score_candidate",
                            lambda *args, **kwargs: replace(original, score=Decimal(score)))
        result = match([ledger()], [bank()], NoneAdjudicator())
        assert result.outcomes[0]["status"] == expected


def test_empty_batch_headers_and_unique_ids(tmp_path):
    assert match([], []).outcomes == []
    assert match([], []).cheap_match_rate == 0
    ledger_path, bank_path = tmp_path / "ledger.csv", tmp_path / "bank.csv"
    ledger_path.write_text(",".join(ledger().to_row()) + "\n")
    bank_path.write_text(",".join(bank().to_row()) + "\n")
    run(ledger_path, bank_path, tmp_path / "output")
    assert (tmp_path / "output" / "match_outcomes.csv").read_text().count("\n") == 1
    assert (tmp_path / "output" / "ledger_settlements.csv").read_text().count("\n") == 1
    with pytest.raises(ValueError, match="Duplicate ledger_id"):
        match([ledger(), ledger()], [])
    with pytest.raises(ValueError, match="Duplicate bank_txn_id"):
        match([ledger()], [bank(), bank()])


@pytest.mark.parametrize("verdicts", [
    [Verdict("UNKNOWN", "LG-1", True, Decimal(1), "test")],
    [Verdict("BK-1", "UNKNOWN", True, Decimal(1), "test")],
    [Verdict("BK-1", "LG-1", True, Decimal("NaN"), "test")],
    [Verdict("BK-1", "LG-1", True, Decimal("1.01"), "test")],
    [Verdict("BK-1", "LG-1", True, Decimal(1), "test")] * 2,
])
def test_invalid_adjudicator_verdicts_fail_closed(verdicts):
    class InvalidStub(StubAdjudicator):
        def adjudicate(self, questions):
            return verdicts
    with pytest.raises(ValueError):
        match([ledger()], [bank(reference="WRONG")], InvalidStub())


def test_ambiguous_high_score_escalates_and_empty_block_rejects():
    ambiguous = match([ledger("A"), ledger("B")], [bank()], NoneAdjudicator())
    assert ambiguous.outcomes[0]["status"] == "escalated"
    assert ambiguous.outcomes[0]["margin"] == "0.00"
    empty = match([], [bank()])
    assert empty.outcomes[0]["reason"] == "no_candidate"


def test_known_near_relative_partial_decoy_limitation():
    # §5.4 intentionally pins the false positive; not a precision claim or a full-scan fix.
    decoy = ledger("DECOY", "200", "QUOTED", "Alpha Systems Group")
    true = ledger("TRUE", "100", "ACTUAL", "Alpha Systems Trading Co")
    result = match([decoy, true], [bank(reference="QUOTED", name="Alpha Systems Trading Co")])
    assert result.outcomes[0]["ledger_id"] == "DECOY"
    assert result.outcomes[0]["reason"] == "PARTIAL_WITH_EXACT_REFERENCE"


def test_cli_independent_process_determinism(tmp_path):
    for seed in ("1", "99"):
        completed = subprocess.run([
            sys.executable, "-m", "ledger_sense.matching", "--ledger", str(FIXTURE / "ledger.csv"),
            "--bank", str(FIXTURE / "bank.csv"), "--out-dir", str(tmp_path / seed),
        ], env={**os.environ, "PYTHONHASHSEED": seed}, check=True, capture_output=True, text=True)
        assert "cheap-tier match rate:" in completed.stdout
        assert "llm_calls=0" in completed.stdout
    for name in ("match_outcomes.csv", "ledger_settlements.csv"):
        assert (tmp_path / "1" / name).read_bytes() == (tmp_path / "99" / name).read_bytes()


def test_local_reference_batch_acceptance(tmp_path):
    """Opt-in full-batch check after the exact generation command in the W2 brief.

    MATCHING_BATCH_DIR can point to another local generation. Ground truth is
    opened here only, never passed to the matcher. Rate is reported, not tuned.
    """
    batch = Path(os.environ.get("MATCHING_BATCH_DIR", "data/pass1"))
    if not (batch / "ledger.csv").exists():
        pytest.skip("Generate data/pass1 to run the full-batch regression")
    result = run(batch / "ledger.csv", batch / "bank.csv", tmp_path / "first")
    run(batch / "ledger.csv", batch / "bank.csv", tmp_path / "second")
    truth = {r["bank_txn_id"]: r for r in rows(batch / "match_links.csv")}
    outcomes = {r["bank_txn_id"]: r for r in result.outcomes}
    matched = [r for r in result.outcomes if r["status"] == "matched"]
    correct = [r for r in matched if r["bank_txn_id"] in truth and
               r["ledger_id"] == truth[r["bank_txn_id"]]["ledger_id"]]
    precision = Decimal(len(correct)) / len(matched)
    print(f"\nReference cheap-tier={result.cheap_match_rate:.4f}%; "
          f"matched={len(matched)}/{len(outcomes)}; precision={precision:.6f}; llm_calls={result.llm_calls}")
    assert precision >= Decimal("0.999")
    assert all(r["bank_txn_id"] in truth for r in matched), "Orphan auto-match"
    assert all(truth[r["bank_txn_id"]]["defect"] not in {"negative_amount", "zero_amount"} for r in matched)
    duplicate_cases = {}
    for link in truth.values():
        if link["defect"] == "duplicate":
            duplicate_cases.setdefault(link["case_id"], []).append(outcomes[link["bank_txn_id"]])
    for legs in duplicate_cases.values():
        assert sum(r["status"] == "matched" for r in legs) == 1
        assert sum(r["reason"] == "duplicate_of_matched" for r in legs) == 1
        assert all(r["matched_amount"] == "0.00" for r in legs if r["relation"] == "duplicate")
    for row in result.settlements:
        assert abs(Decimal(row["matched_amount"])) <= abs(Decimal(row["ledger_amount"]))
        assert Decimal(row["ledger_amount"]) == Decimal(row["matched_amount"]) + Decimal(row["residual"])
    for filename in ("match_outcomes.csv", "ledger_settlements.csv"):
        assert (tmp_path / "first" / filename).read_bytes() == (tmp_path / "second" / filename).read_bytes()
    assert result.llm_calls == 0
