"""W6: fixture-first acceptance for the Agent 5 Metrics Orchestrator (spec §9).

  * classify.py            -- amount bucketing, reference-transform typing,
                               exception-class shape keys.
  * scoreboard.py           -- pure STR/precision/class-diff/trace math,
                               refusal on internally-inconsistent inputs.
  * io.py                   -- strict read-only CSV/JSON boundary.
  * CLI end-to-end           -- ``ledger_sense-scoreboard scoreboard`` via
                                 subprocess (refusal contract + success path +
                                 byte-identical reruns, acceptance #4/#5).
  * Full pipeline (slow)     -- real matching/routing/guardrail/learning CLIs
                                 feed a real scoreboard run (acceptance #1-#3).
"""

import ast
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

import ledger_sense.metrics

from ledger_sense.data.io_csv import write_csv
from ledger_sense.data.money import cents, from_cents, to_money
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS, MATCH_LINK_COLUMNS, BankTransaction, LedgerEntry, MatchLink
from ledger_sense.metrics import io as metrics_io
from ledger_sense.metrics.classify import amount_bucket, class_histogram, exception_class, reference_pattern
from ledger_sense.metrics.scoreboard import (
    ScoreboardError,
    build_scoreboard,
    ground_truth_map,
    guardrail_split,
    real_straight_through,
    straight_through,
)

# ---------------------------------------------------------------------------
# Isolation (law L1): metrics never imports another agent's internals.
# match_links.csv itself IS allowed here (law L2 names Agent 5 as the one
# exception), so unlike test_routing_isolation.py this sweep does not forbid
# the string "match_links" -- only the forbidden *imports*.
# ---------------------------------------------------------------------------


def assert_no_agent_internals_imported(source):
    tree = ast.parse(source)
    forbidden_modules = {"matching", "routing", "guardrail", "learning"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "ledger_sense":
                assert not {a.name for a in node.names} & forbidden_modules
            assert not any(f".{word}" in f".{module}" for word in forbidden_modules), module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f".{word}" in f".{alias.name}" for word in forbidden_modules), alias.name


def test_metrics_package_never_imports_another_agents_internals():
    package = Path(ledger_sense.metrics.__file__).parent
    for file in package.rglob("*.py"):
        if "__pycache__" not in file.parts:
            assert_no_agent_internals_imported(file.read_text())


@pytest.mark.parametrize("source", [
    "from ledger_sense.matching import engine as innocent",
    "from ledger_sense.routing.io import run as innocent",
    "from ledger_sense.guardrail import load_policy as innocent",
    "from ledger_sense.learning.rules import matching_rule as innocent",
    "import ledger_sense.matching as innocent",
    "from ledger_sense import learning as innocent",
])
def test_isolation_check_catches_agent_internal_imports(source):
    with pytest.raises(AssertionError):
        assert_no_agent_internals_imported(source)


# ---------------------------------------------------------------------------
# classify.py
# ---------------------------------------------------------------------------


def test_amount_bucket_boundaries_are_inclusive_on_the_low_side():
    assert amount_bucket(0) == "0"
    assert amount_bucket(100) == "1-100"
    assert amount_bucket(101) == "101-500"
    assert amount_bucket(1500) == "501-1500"
    assert amount_bucket(-1500) == "501-1500"  # magnitude only, sign-agnostic
    assert amount_bucket(100001) == ">100000"


def test_reference_pattern_matches_matcher_vocabulary():
    assert reference_pattern({"reference": "1"}) == "exact"
    assert reference_pattern({"reference": "0"}) == "wrong"
    assert reference_pattern({"reference": "0.6"}) == "fuzzy"
    assert reference_pattern({"reference": None}) == "missing"
    assert reference_pattern({}) is None


def test_exception_class_is_no_features_for_ledger_only_subject():
    row = {"subject_kind": "ledger", "bank_txn_id": "", "counterparty_key": "ACME"}
    key = exception_class(row, {})
    assert key == ("ACME", "no_features", "no_features")


def test_exception_class_reads_amount_and_reference_off_features():
    row = {"subject_kind": "bank", "bank_txn_id": "BK-1", "counterparty_key": "ACME"}
    features = {"BK-1": {"amount_delta_cents": 1500, "reference": "1"}}
    key = exception_class(row, features)
    assert key == ("ACME", "501-1500", "exact")


def test_class_histogram_groups_identically_shaped_rows():
    rows = [
        {"subject_kind": "bank", "bank_txn_id": "BK-1", "counterparty_key": "ACME"},
        {"subject_kind": "bank", "bank_txn_id": "BK-2", "counterparty_key": "ACME"},
    ]
    features = {
        "BK-1": {"amount_delta_cents": 1500, "reference": "1"},
        "BK-2": {"amount_delta_cents": 1499, "reference": "1"},
    }
    hist = class_histogram(rows, features)
    assert hist == {("ACME", "501-1500", "exact"): 2}


# ---------------------------------------------------------------------------
# scoreboard.py -- pure math
# ---------------------------------------------------------------------------


def _outcome(bank_txn_id, ledger_id, status="matched", reason="high_confidence"):
    return {"bank_txn_id": bank_txn_id, "status": status, "ledger_id": ledger_id, "reason": reason}


def _settlement(ledger_id, reason="fully_settled"):
    return {"ledger_id": ledger_id, "reason": reason}


def test_straight_through_counts_matched_and_fully_settled_only():
    outcomes = [_outcome("BK-1", "LG-1"), _outcome("BK-2", "LG-2", status="escalated")]
    settlements = {"LG-1": _settlement("LG-1"), "LG-2": _settlement("LG-2", reason="open")}
    result = straight_through(outcomes, settlements)
    assert result == {"straight": 1, "total": 2, "pct": "50.00"}


def test_ground_truth_map_is_bank_txn_id_to_ledger_id():
    links = [{"bank_txn_id": "BK-1", "ledger_id": "LG-1"}, {"bank_txn_id": "BK-2", "ledger_id": "LG-2"}]
    assert ground_truth_map(links) == {"BK-1": "LG-1", "BK-2": "LG-2"}


def test_real_straight_through_rejects_a_confidently_wrong_match():
    # Agent 1 claims BK-1 -> LG-WRONG, but the ground truth says LG-1.
    outcomes = [_outcome("BK-1", "LG-WRONG")]
    settlements = {"LG-WRONG": _settlement("LG-WRONG")}
    truth = {"BK-1": "LG-1"}
    result = real_straight_through(outcomes, settlements, truth)
    assert result["claimed_matches"] == 1
    assert result["correct_matches"] == 0
    assert result["straight_through_correct"] == 0
    assert result["precision_pct"] == "0.00"
    assert result["real_str_pct"] == "0.00"


def test_real_straight_through_credits_a_correct_and_settled_match():
    outcomes = [_outcome("BK-1", "LG-1")]
    settlements = {"LG-1": _settlement("LG-1")}
    truth = {"BK-1": "LG-1"}
    result = real_straight_through(outcomes, settlements, truth)
    assert result["correct_matches"] == 1
    assert result["straight_through_correct"] == 1
    assert result["precision_pct"] == "100.00"
    assert result["real_str_pct"] == "100.00"


def test_guardrail_split_sums_to_total():
    rows = [{"verdict": "allow"}, {"verdict": "allow"}, {"verdict": "block"}, {"verdict": "hold"}]
    result = guardrail_split(rows)
    assert result["total"] == 4
    assert result["allow_count"] == 2 and result["allow_pct"] == "50.00"
    assert result["block_count"] == 1 and result["hold_count"] == 1


def _minimal_pass(outcomes=(), settlements=(), exceptions=(), release_decisions=(), match_links=(),
                   queues=(), guardrail_audit=()):
    return {
        "outcomes": list(outcomes), "settlements": list(settlements), "exceptions": list(exceptions),
        "features": {}, "release_decisions": list(release_decisions), "match_links": list(match_links),
        "queues": list(queues), "guardrail_audit": list(guardrail_audit),
    }


def test_build_scoreboard_refuses_rule_hit_with_unknown_rule_id():
    pass1 = _minimal_pass()
    pass2 = _minimal_pass(outcomes=[_outcome("BK-1", "LG-1", reason="resolved_by_rule")])
    rule_hits = [{"bank_txn_id": "BK-1", "ledger_id": "LG-1", "rule_id": "RULE-ghost",
                  "resolution_id": "RES-1", "resolution_type": "fee_offset",
                  "applied_cents": "0", "guardrail_verdict": "allow", "predicate": "{}"}]
    with pytest.raises(ScoreboardError, match="not present in rules.json"):
        build_scoreboard(pass1=pass1, pass2=pass2, rules=[], rule_hits=rule_hits,
                          pass1_dir="p1", pass2_dir="p2", rules_path="rules.json")


def test_build_scoreboard_refuses_when_trace_coverage_is_incomplete():
    # A resolved_by_rule row with NO matching rule_hits.csv entry -- exactly
    # the "never fabricate a number" failure mode acceptance #3 guards against.
    pass1 = _minimal_pass()
    pass2 = _minimal_pass(outcomes=[
        _outcome("BK-1", "LG-1", reason="resolved_by_rule"),
        _outcome("BK-2", "LG-2", reason="resolved_by_rule"),
    ])
    rules = [{"rule_id": "RULE-1", "plain_english": "x"}]
    rule_hits = [{"bank_txn_id": "BK-1", "ledger_id": "LG-1", "rule_id": "RULE-1",
                  "resolution_id": "RES-1", "resolution_type": "fee_offset",
                  "applied_cents": "0", "guardrail_verdict": "allow", "predicate": "{}"}]
    with pytest.raises(ScoreboardError, match="trace-table coverage mismatch"):
        build_scoreboard(pass1=pass1, pass2=pass2, rules=rules, rule_hits=rule_hits,
                          pass1_dir="p1", pass2_dir="p2", rules_path="rules.json")


def test_build_scoreboard_happy_path_reports_class_elimination_and_full_trace():
    pass1_exceptions = [
        {"subject_kind": "bank", "bank_txn_id": "BK-A1", "counterparty_key": "ACME"},
        {"subject_kind": "bank", "bank_txn_id": "BK-A2", "counterparty_key": "ACME"},
    ]
    pass1 = _minimal_pass(
        outcomes=[_outcome("BK-A1", "LG-A1"), _outcome("BK-A2", "LG-A2")],
        settlements=[_settlement("LG-A1"), _settlement("LG-A2")],
        exceptions=pass1_exceptions,
        release_decisions=[{"verdict": "allow"}, {"verdict": "allow"}],
        match_links=[{"bank_txn_id": "BK-A1", "ledger_id": "LG-A1"}, {"bank_txn_id": "BK-A2", "ledger_id": "LG-A2"}],
    )
    pass1["features"] = {
        "BK-A1": {"amount_delta_cents": 50, "reference": "1"},
        "BK-A2": {"amount_delta_cents": 50, "reference": "1"},
    }
    # Pass 2: both siblings resolved by the same rule before routing ever saw
    # them -- zero exceptions remain for that class.
    pass2 = _minimal_pass(
        outcomes=[
            _outcome("BK-B1", "LG-B1", reason="resolved_by_rule"),
            _outcome("BK-B2", "LG-B2", reason="resolved_by_rule"),
        ],
        settlements=[_settlement("LG-B1"), _settlement("LG-B2")],
        exceptions=[],
        release_decisions=[{"verdict": "allow"}, {"verdict": "allow"}],
        match_links=[{"bank_txn_id": "BK-B1", "ledger_id": "LG-B1"}, {"bank_txn_id": "BK-B2", "ledger_id": "LG-B2"}],
    )
    rules = [{"rule_id": "RULE-1", "resolution_id": "RES-1", "plain_english": "counterparty=ACME",
              "promoted_by": "bob", "promoted_at": "2026-06-01T00:00:00Z"}]
    rule_hits = [
        {"bank_txn_id": "BK-B1", "ledger_id": "LG-B1", "rule_id": "RULE-1", "resolution_id": "RES-1",
         "resolution_type": "fee_offset", "applied_cents": "1500", "guardrail_verdict": "allow", "predicate": "{}"},
        {"bank_txn_id": "BK-B2", "ledger_id": "LG-B2", "rule_id": "RULE-1", "resolution_id": "RES-1",
         "resolution_type": "fee_offset", "applied_cents": "1500", "guardrail_verdict": "allow", "predicate": "{}"},
    ]
    scoreboard = build_scoreboard(pass1=pass1, pass2=pass2, rules=rules, rule_hits=rule_hits,
                                   pass1_dir="p1", pass2_dir="p2", rules_path="rules.json")

    assert scoreboard["learned_rule_count"] == 1
    assert scoreboard["pass1"]["exceptions_remaining"] == 2
    assert scoreboard["pass2"]["exceptions_remaining"] == 0
    assert scoreboard["pass2"]["rule_driven_auto_resolves"] == 2
    assert scoreboard["pass2"]["trace_coverage_pct"] == "100.00"
    assert "ACME|1-100|exact" in scoreboard["exception_classes"]["eliminated_classes"]
    assert len(scoreboard["rule_trace"]) == 2
    assert all(hit["resolution_id"] == "RES-1" for hit in scoreboard["rule_trace"])
    assert all(hit["plain_english"] == "counterparty=ACME" for hit in scoreboard["rule_trace"])
    # json.dumps must round-trip cleanly (no Decimal/float leaked into the dict).
    json.dumps(scoreboard, sort_keys=True)


# ---------------------------------------------------------------------------
# io.py -- refuse on missing/malformed input
# ---------------------------------------------------------------------------


def test_read_outcomes_refuses_missing_file(tmp_path):
    with pytest.raises(metrics_io.MetricsInputError, match="not found"):
        metrics_io.read_outcomes(tmp_path / "nope.csv")


def test_read_outcomes_refuses_wrong_columns(tmp_path):
    path = tmp_path / "match_outcomes.csv"
    write_csv(str(path), ["bank_txn_id", "status"], [{"bank_txn_id": "BK-1", "status": "matched"}])
    with pytest.raises(metrics_io.MetricsInputError, match="Unexpected columns"):
        metrics_io.read_outcomes(path)


def test_read_rules_refuses_missing_rules_key(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"not_rules": []}), encoding="utf-8")
    with pytest.raises(metrics_io.MetricsInputError, match="missing the 'rules' key"):
        metrics_io.read_rules(path)


# ---------------------------------------------------------------------------
# CLI end-to-end (subprocess) -- refusal contract + byte-identical reruns
# ---------------------------------------------------------------------------


def run_cli(args, cwd):
    completed = subprocess.run(
        [sys.executable, "-m", "ledger_sense.metrics", *args], cwd=cwd, capture_output=True, text=True
    )
    return completed


def test_cli_refuses_nonzero_when_pass2_dir_missing(tmp_path):
    pass1_dir = tmp_path / "pass1"
    pass1_dir.mkdir()
    result = run_cli(
        ["scoreboard", "--pass1-dir", str(pass1_dir), "--pass2-dir", str(tmp_path / "does_not_exist")],
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "scoreboard refused" in result.stderr
    assert not (tmp_path / "scoreboard.json").exists()


def test_cli_refuses_nonzero_when_rules_json_missing(tmp_path):
    outcome_columns = metrics_io.OUTCOME_COLUMNS
    settlement_columns = metrics_io.SETTLEMENT_COLUMNS
    for pass_name in ("pass1", "pass2"):
        d = tmp_path / pass_name
        d.mkdir()
        write_csv(str(d / "match_outcomes.csv"), outcome_columns, [])
        write_csv(str(d / "ledger_settlements.csv"), settlement_columns, [])
        write_csv(str(d / "exceptions.csv"), metrics_io.EXCEPTION_COLUMNS, [])
        write_csv(str(d / "owner_queues.csv"), metrics_io.QUEUE_COLUMNS, [])
        write_csv(str(d / "release_decisions.csv"), metrics_io.RELEASE_COLUMNS, [])
        write_csv(str(d / "guardrail_audit.csv"), metrics_io.AUDIT_COLUMNS, [])
        write_csv(str(d / "match_links.csv"), metrics_io.MATCH_LINK_COLUMNS, [])
    result = run_cli(
        ["scoreboard", "--pass1-dir", str(tmp_path / "pass1"), "--pass2-dir", str(tmp_path / "pass2"),
         "--rules", str(tmp_path / "rules.json")],
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "scoreboard refused" in result.stderr
    assert "rules.json" in result.stderr
    assert not (tmp_path / "scoreboard.json").exists()


def _write_empty_pass(d):
    d.mkdir()
    write_csv(str(d / "match_outcomes.csv"), metrics_io.OUTCOME_COLUMNS, [])
    write_csv(str(d / "ledger_settlements.csv"), metrics_io.SETTLEMENT_COLUMNS, [])
    write_csv(str(d / "exceptions.csv"), metrics_io.EXCEPTION_COLUMNS, [])
    write_csv(str(d / "owner_queues.csv"), metrics_io.QUEUE_COLUMNS, [])
    write_csv(str(d / "release_decisions.csv"), metrics_io.RELEASE_COLUMNS, [])
    write_csv(str(d / "guardrail_audit.csv"), metrics_io.AUDIT_COLUMNS, [])
    write_csv(str(d / "match_links.csv"), metrics_io.MATCH_LINK_COLUMNS, [])


def test_cli_success_writes_scoreboard_json_byte_identical_across_reruns(tmp_path):
    pass1_dir, pass2_dir = tmp_path / "pass1", tmp_path / "pass2"
    _write_empty_pass(pass1_dir)
    _write_empty_pass(pass2_dir)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"schema_version": 1, "rules": []}), encoding="utf-8")
    write_csv(str(pass2_dir / "rule_hits.csv"), metrics_io.RULE_HIT_COLUMNS, [])

    out1, out2 = tmp_path / "scoreboard1.json", tmp_path / "scoreboard2.json"
    for out in (out1, out2):
        result = run_cli(
            ["scoreboard", "--pass1-dir", str(pass1_dir), "--pass2-dir", str(pass2_dir),
             "--rules", str(rules_path), "--out", str(out)],
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "Ledger Sense -- Agent 5 scoreboard" in result.stdout

    assert out1.read_bytes() == out2.read_bytes()
    scoreboard = json.loads(out1.read_text())
    assert scoreboard["learned_rule_count"] == 0
    assert scoreboard["pass1"]["str_naive"]["total"] == 0


def test_cli_refuses_nonzero_when_pass2_rule_hits_missing(tmp_path):
    pass1_dir, pass2_dir = tmp_path / "pass1", tmp_path / "pass2"
    _write_empty_pass(pass1_dir)
    _write_empty_pass(pass2_dir)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"schema_version": 1, "rules": []}), encoding="utf-8")
    # rule_hits.csv deliberately NOT written into pass2_dir.
    result = run_cli(
        ["scoreboard", "--pass1-dir", str(pass1_dir), "--pass2-dir", str(pass2_dir), "--rules", str(rules_path)],
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "rule_hits.csv" in result.stderr


# ---------------------------------------------------------------------------
# Full pipeline (slow): real matching + routing + guardrail + learning +
# metrics CLIs, subprocess only -- acceptance #1, #2, #3.
# ---------------------------------------------------------------------------

FEE_LEDGER_AMOUNT = Decimal("2000.00")
FEE_CENTS = 1500
N_SIBLINGS = 10  # >= the spec's 8-sibling class-elimination threshold


def _fee_pair(pass_prefix, i, ledger_id, bank_id, reference):
    inflow = i % 2 == 0
    ledger_amount = FEE_LEDGER_AMOUNT if inflow else -FEE_LEDGER_AMOUNT
    fee_sign = 1 if inflow else -1
    bank_amount = from_cents(cents(to_money(ledger_amount)) - fee_sign * FEE_CENTS)
    entry_type = "invoice_payment" if inflow else "payout"
    direction = "credit" if inflow else "debit"
    ledger = LedgerEntry(ledger_id, "2026-06-05T00:00:00Z", to_money(ledger_amount), "USD", entry_type,
                          "CP-ACME", "Acme Logistics", reference, "fee-bearing settlement", "1200", "billing")
    bank = BankTransaction(bank_id, "2026-06-05T00:00:00Z", bank_amount, "USD", "Acme Logistics", reference,
                            "ACH CREDIT ACME LOGISTICS", "ACCT-USD-01", "STMT-1", direction)
    return ledger, bank


def _clean_pair(i, ledger_id, bank_id, reference):
    ledger = LedgerEntry(ledger_id, "2026-06-03T00:00:00Z", to_money("500.00"), "USD", "invoice_payment",
                          f"CP-CLEAN-{i}", f"Clean Co {i}", reference, "ordinary settlement", "1200", "billing")
    bank = BankTransaction(bank_id, "2026-06-03T00:00:00Z", to_money("500.00"), "USD", f"Clean Co {i}", reference,
                            "ACH CREDIT", "ACCT-USD-01", "STMT-1", "credit")
    return ledger, bank


def _build_fixture(tmp_path, pass_prefix):
    """Build ``ledger.csv``/``bank.csv``/``match_links.csv`` -- the same
    fee_offset-shaped fixture ``tests/test_learning.py`` uses (independently
    reconstructed here, not imported, per this repo's own test-isolation
    convention), plus the ground-truth ``match_links.csv`` this agent alone
    is allowed to read (law L2)."""
    ledgers, banks, links = [], [], []
    for i in range(N_SIBLINGS):
        ledger_id, bank_id = f"LG-{pass_prefix}-FEE-{i:03d}", f"BK-{pass_prefix}-FEE-{i:03d}"
        reference = f"INV-2026-{pass_prefix}-{i:04d}"
        ledger, bank = _fee_pair(pass_prefix, i, ledger_id, bank_id, reference)
        ledgers.append(ledger)
        banks.append(bank)
        links.append(MatchLink(ledger_id, bank_id, "exact", "fee_offset", f"CASE-{pass_prefix}-FEE-{i:03d}",
                                "overlay:fee_offset"))
    for i in range(3):
        ledger_id, bank_id = f"LG-{pass_prefix}-CLEAN-{i:03d}", f"BK-{pass_prefix}-CLEAN-{i:03d}"
        reference = f"INV-2026-{pass_prefix}-CLEAN-{i:04d}"
        ledger, bank = _clean_pair(i, ledger_id, bank_id, reference)
        ledgers.append(ledger)
        banks.append(bank)
        links.append(MatchLink(ledger_id, bank_id, "exact", "clean", f"CASE-{pass_prefix}-CLEAN-{i:03d}",
                                "clean settlement"))

    out = tmp_path / pass_prefix
    out.mkdir()
    write_csv(str(out / "ledger.csv"), LEDGER_COLUMNS, [e.to_row() for e in ledgers])
    write_csv(str(out / "bank.csv"), BANK_COLUMNS, [b.to_row() for b in banks])
    write_csv(str(out / "match_links.csv"), MATCH_LINK_COLUMNS, [link.to_row() for link in links])
    return out


def run_module(module, args, cwd):
    completed = subprocess.run([sys.executable, "-m", module, *args], cwd=cwd, capture_output=True, text=True)
    assert completed.returncode == 0, f"{module} {args} failed:\n{completed.stdout}\n{completed.stderr}"
    return completed


def _read_csv(path):
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.mark.slow
def test_full_pipeline_scoreboard_reports_real_measured_numbers(tmp_path):
    as_of = "2026-06-30T00:00:00Z"

    # ---- Pass 1: generate (by hand), match, route, guardrail. ----
    pass1_dir = _build_fixture(tmp_path, "P1")
    run_module("ledger_sense.matching", ["--ledger", str(pass1_dir / "ledger.csv"), "--bank",
                                          str(pass1_dir / "bank.csv"), "--out-dir", str(pass1_dir)], tmp_path)
    run_module("ledger_sense.routing", [
        "--outcomes", str(pass1_dir / "match_outcomes.csv"), "--settlements", str(pass1_dir / "ledger_settlements.csv"),
        "--ledger", str(pass1_dir / "ledger.csv"), "--bank", str(pass1_dir / "bank.csv"),
        "--as-of", as_of, "--out-dir", str(pass1_dir),
    ], tmp_path)
    run_module("ledger_sense.guardrail", [
        "--ledger", str(pass1_dir / "ledger.csv"), "--bank", str(pass1_dir / "bank.csv"),
        "--outcomes", str(pass1_dir / "match_outcomes.csv"), "--settlements", str(pass1_dir / "ledger_settlements.csv"),
        "--as-of", as_of, "--out-dir", str(pass1_dir),
    ], tmp_path)

    pass1_exceptions = _read_csv(pass1_dir / "exceptions.csv")
    fee_class_pass1 = [r for r in pass1_exceptions if r["counterparty_key"] == "ACMELOGISTICS"]
    assert len(fee_class_pass1) >= 8

    # ---- A human resolves + promotes one fee_offset exception into a rule. ----
    candidates_path, rules_path = tmp_path / "candidates.json", tmp_path / "rules.json"
    resolved = subprocess.run([
        sys.executable, "-m", "ledger_sense.learning", "resolve",
        "--exceptions", str(pass1_dir / "exceptions.csv"), "--outcomes", str(pass1_dir / "match_outcomes.csv"),
        "--exception-id", fee_class_pass1[0]["exception_id"], "--resolution-type", "fee_offset",
        "--counterparty-key", "Acme Logistics", "--amount-delta-min", "0.00", "--amount-delta-max", "15.00",
        "--reference-transform", "exact", "--rationale", "Acme deducts a flat $15 processing fee off every invoice",
        "--resolved-by", "alice", "--resolved-at", "2026-06-15T00:00:00Z", "--candidates", str(candidates_path),
    ], cwd=tmp_path, capture_output=True, text=True)
    assert resolved.returncode == 0, resolved.stderr
    rule_id = next(l.split("=", 1)[1] for l in resolved.stdout.splitlines() if l.startswith("rule_id="))
    promoted = subprocess.run([
        sys.executable, "-m", "ledger_sense.learning", "promote", rule_id, "--confirm", "yes-always",
        "--promoted-by", "bob", "--promoted-at", "2026-06-15T01:00:00Z", "--rules", str(rules_path),
        "--candidates", str(candidates_path),
    ], cwd=tmp_path, capture_output=True, text=True)
    assert promoted.returncode == 0, promoted.stderr

    # ---- Pass 2: a genuinely new draw, rules applied before routing (§7.4). ----
    pass2_dir = _build_fixture(tmp_path, "P2")
    run_module("ledger_sense.matching", ["--ledger", str(pass2_dir / "ledger.csv"), "--bank",
                                          str(pass2_dir / "bank.csv"), "--out-dir", str(pass2_dir)], tmp_path)
    apply_result = subprocess.run([
        sys.executable, "-m", "ledger_sense.learning", "apply-rules",
        "--outcomes", str(pass2_dir / "match_outcomes.csv"), "--settlements", str(pass2_dir / "ledger_settlements.csv"),
        "--ledger", str(pass2_dir / "ledger.csv"), "--bank", str(pass2_dir / "bank.csv"),
        "--rules", str(rules_path), "--as-of", as_of, "--out-dir", str(pass2_dir),
    ], cwd=tmp_path, capture_output=True, text=True)
    assert apply_result.returncode == 0, apply_result.stderr
    run_module("ledger_sense.routing", [
        "--outcomes", str(pass2_dir / "match_outcomes.csv"), "--settlements", str(pass2_dir / "ledger_settlements.csv"),
        "--ledger", str(pass2_dir / "ledger.csv"), "--bank", str(pass2_dir / "bank.csv"),
        "--as-of", as_of, "--out-dir", str(pass2_dir),
    ], tmp_path)
    run_module("ledger_sense.guardrail", [
        "--ledger", str(pass2_dir / "ledger.csv"), "--bank", str(pass2_dir / "bank.csv"),
        "--outcomes", str(pass2_dir / "match_outcomes.csv"), "--settlements", str(pass2_dir / "ledger_settlements.csv"),
        "--as-of", as_of, "--out-dir", str(pass2_dir),
    ], tmp_path)

    # ---- Agent 5: the scoreboard, computed only from files now on disk. ----
    scoreboard_path = tmp_path / "scoreboard.json"
    result = subprocess.run([
        sys.executable, "-m", "ledger_sense.metrics", "scoreboard",
        "--pass1-dir", str(pass1_dir), "--pass2-dir", str(pass2_dir), "--rules", str(rules_path),
        "--out", str(scoreboard_path),
    ], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    print("\n" + result.stdout)  # real measured numbers, visible with -s

    scoreboard = json.loads(scoreboard_path.read_text())

    # Acceptance #1: real numbers, not asserted targets -- just sanity bounds.
    assert scoreboard["learned_rule_count"] == 1
    assert scoreboard["pass1"]["str_naive"]["total"] == N_SIBLINGS + 3
    assert scoreboard["pass2"]["str_naive"]["total"] == N_SIBLINGS + 3

    # Acceptance #2: the whole ACME fee_offset class disappears in pass 2.
    assert "ACMELOGISTICS|501-1500|exact" in scoreboard["exception_classes"]["eliminated_classes"] or any(
        row["class"].startswith("ACMELOGISTICS") and row["eliminated"]
        for row in scoreboard["exception_classes"]["rows"]
    )
    assert scoreboard["pass2"]["exceptions_remaining"] < scoreboard["pass1"]["exceptions_remaining"]

    # Acceptance #3: trace table covers 100% of pass-2 rule-driven auto-resolves.
    assert scoreboard["pass2"]["rule_driven_auto_resolves"] == N_SIBLINGS
    assert scoreboard["pass2"]["trace_coverage_pct"] == "100.00"
    assert len(scoreboard["rule_trace"]) == N_SIBLINGS
    assert all(hit["rule_id"] == rule_id for hit in scoreboard["rule_trace"])
    assert all(hit["resolution_id"] for hit in scoreboard["rule_trace"])

    # Ground truth actually engaged (law L2): every claimed match on both
    # passes checks out exactly against match_links.csv -- 100% precision,
    # since nothing in this fixture is designed to mismatch.
    assert scoreboard["pass1"]["str_real"]["precision_pct"] == "100.00"
    assert scoreboard["pass2"]["str_real"]["precision_pct"] == "100.00"
    assert scoreboard["pass2"]["str_real"]["real_str_pct"] == scoreboard["pass2"]["str_naive"]["pct"]
