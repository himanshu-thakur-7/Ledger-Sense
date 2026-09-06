"""Agent 3 -- Resolution-Learning acceptance tests (spec §7, BOARD.md W5 card).

Layout:
  * isolation           -- law L1/L2: no ``match_links`` access, no import of
                            ``ledger_sense.matching``/``ledger_sense.routing`` internals.
  * predicate / resolution / rules -- unit tests for the pure building blocks.
  * apply_rules unit tests -- guardrail veto (law L12), capacity safety (law L10),
                              non-escalated/duplicate rows never touched.
  * CLI end-to-end       -- ``ledger_sense resolve`` / ``promote`` / ``apply-rules``
                            via subprocess, exact printed contract (acceptance #7).
  * full pipeline        -- real ``ledger_sense.matching``/``ledger_sense.routing``
                            CLIs, measured pass-1-vs-pass-2 numbers (acceptance #1,2,4,5,6).
"""

import ast
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_sense.data.io_csv import write_csv
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS, BankTransaction, LedgerEntry
from ledger_sense.data.money import cents, from_cents, to_money
from ledger_sense.guardrail import load_policy
from ledger_sense.learning.apply import apply_rules
from ledger_sense.learning.predicate import (
    build_predicate,
    evaluate_predicate,
    reference_transform_of,
    render_english,
    squash,
)
from ledger_sense.learning.resolution import ResolutionError, make_resolution
from ledger_sense.learning.rules import RuleError, candidate_rule, load_rules, promote

FEE_CENTS = 1500  # $15.00, mirrors the generator's OVERLAY_FEE_CENTS (§4/BOARD.md W1)


# ---------------------------------------------------------------------------
# Isolation (law L1, L2) -- mirrors test_routing_isolation.py's pattern.
# ---------------------------------------------------------------------------

def assert_isolated(source: str) -> None:
    assert "match_links" not in source.lower()
    tree = ast.parse(source)
    allowed_data = {
        "ledger_sense.data.models": set(),
        "ledger_sense.data.money": {"cents", "from_cents", "money_str", "to_money"},
        "ledger_sense.data.io_csv": {"write_csv"},
    }
    allowed_guardrail = {"would_block_or_hold", "load_policy"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "ledger_sense" or module.startswith("ledger_sense."):
                assert not any(word in module for word in ("matching", "routing")), module
            if module.startswith("ledger_sense.data"):
                assert module in allowed_data, module
                if allowed_data[module]:
                    assert {a.name for a in node.names} <= allowed_data[module]
            if module == "ledger_sense.guardrail" or module == "ledger_sense.guardrail.engine":
                assert {a.name for a in node.names} <= allowed_guardrail
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "matching" not in alias.name and "routing" not in alias.name
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), "money/scores must be Decimal, never float"


def test_learning_package_isolation():
    import ledger_sense.learning

    package_dir = Path(ledger_sense.learning.__file__).parent
    for file in package_dir.rglob("*.py"):
        source = file.read_text(encoding="utf-8")
        assert "match_links" not in source.lower(), file
        assert_isolated(source)


def test_learning_never_imports_matching_or_routing_at_runtime():
    # A fresh interpreter, not this test process (which may already have
    # other agents' packages loaded via other test modules) -- proves
    # importing ``ledger_sense.learning`` alone never pulls in Agent 1/2.
    completed = subprocess.run(
        [sys.executable, "-c",
         "import sys, ledger_sense.learning; "
         "loaded=[n for n in sys.modules if n.startswith('ledger_sense.matching') "
         "or n.startswith('ledger_sense.routing')]; "
         "assert not loaded, loaded; print('ok')"],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# predicate.py
# ---------------------------------------------------------------------------

def test_squash_matches_matcher_style_key():
    assert squash("Acme Logistics, Inc.") == "ACMELOGISTICSINC"


def test_reference_transform_of():
    assert reference_transform_of({"reference": "1"}) == "exact"
    assert reference_transform_of({"reference": "0.6"}) == "fuzzy"
    assert reference_transform_of({"reference": "0.0"}) == "wrong"
    assert reference_transform_of({"reference": None}) == "missing"
    assert reference_transform_of({}) is None


def test_evaluate_predicate_empty_never_matches():
    assert evaluate_predicate({}, {"counterparty_key": "ACME"}) is False


def test_evaluate_predicate_and_semantics():
    predicate = build_predicate(
        counterparty_key="Acme Logistics", currency="USD",
        amount_delta_min="0.00", amount_delta_max="15.00",
        reference_transform="exact",
    )
    matching_features = {
        "counterparty_key": "ACMELOGISTICS", "currency_normalized": "USD",
        "amount_delta_cents": -1500, "reference": "1",
    }
    assert evaluate_predicate(predicate, matching_features) is True
    # Wrong counterparty.
    assert evaluate_predicate(predicate, {**matching_features, "counterparty_key": "OTHER"}) is False
    # Delta magnitude out of range (both signs checked via abs()).
    assert evaluate_predicate(predicate, {**matching_features, "amount_delta_cents": 1501}) is False
    assert evaluate_predicate(predicate, {**matching_features, "amount_delta_cents": -1501}) is False
    # Boundary is inclusive.
    assert evaluate_predicate(predicate, {**matching_features, "amount_delta_cents": 1500}) is True
    assert evaluate_predicate(predicate, {**matching_features, "amount_delta_cents": -1500}) is True
    # Wrong reference transform.
    assert evaluate_predicate(predicate, {**matching_features, "reference": "0.6"}) is False


def test_build_predicate_rejects_bad_min_max_and_enum():
    with pytest.raises(ValueError):
        build_predicate(amount_delta_min="10.00", amount_delta_max="1.00")
    with pytest.raises(ValueError):
        build_predicate(reference_transform="bogus")
    with pytest.raises(ValueError):
        build_predicate(amount_class="bogus")


def test_render_english_uses_dollars_not_cents():
    predicate = build_predicate(counterparty_key="Acme", amount_delta_min="0.00", amount_delta_max="15.00",
                                 reference_transform="exact")
    text = render_english(predicate)
    assert "0.00 < |amount_delta| <= 15.00" in text
    assert "counterparty=ACME" in text
    assert "reference=exact" in text


# ---------------------------------------------------------------------------
# resolution.py (spec §7.1, law L13)
# ---------------------------------------------------------------------------

def test_make_resolution_requires_evidence_for_a_pattern_type():
    with pytest.raises(ResolutionError):
        make_resolution(exception_id="EXC-1", resolution_type="fee_offset", evidence={},
                         rationale="fee", resolved_by="alice", resolved_at="2026-06-01T00:00:00Z")


def test_make_resolution_refuses_evidence_on_first_class_outcomes():
    for resolution_type in ("manual_one_off", "no_pattern"):
        with pytest.raises(ResolutionError):
            make_resolution(exception_id="EXC-1", resolution_type=resolution_type,
                             evidence={"counterparty_key": "ACME"}, rationale="one-off",
                             resolved_by="alice", resolved_at="2026-06-01T00:00:00Z")


def test_make_resolution_manual_one_off_is_first_class_not_a_failure():
    resolution = make_resolution(exception_id="EXC-1", resolution_type="manual_one_off", evidence={},
                                  rationale="unique vendor mixup, will not recur", resolved_by="alice",
                                  resolved_at="2026-06-01T00:00:00Z")
    assert resolution.resolution_type == "manual_one_off"
    assert resolution.evidence == {}


def test_make_resolution_rejects_bad_type_and_missing_fields():
    with pytest.raises(ResolutionError):
        make_resolution(exception_id="EXC-1", resolution_type="not_a_real_type", evidence={"x": 1},
                         rationale="r", resolved_by="alice", resolved_at="2026-06-01T00:00:00Z")
    with pytest.raises(ResolutionError):
        make_resolution(exception_id="", resolution_type="fee_offset", evidence={"currency": "USD"},
                         rationale="r", resolved_by="alice", resolved_at="2026-06-01T00:00:00Z")
    with pytest.raises(ResolutionError):
        make_resolution(exception_id="EXC-1", resolution_type="fee_offset", evidence={"currency": "USD"},
                         rationale="", resolved_by="alice", resolved_at="2026-06-01T00:00:00Z")


def test_resolution_id_is_deterministic():
    kwargs = dict(exception_id="EXC-1", resolution_type="fee_offset", evidence={"currency": "USD"},
                  rationale="r", resolved_by="alice", resolved_at="2026-06-01T00:00:00Z")
    a = make_resolution(**kwargs)
    b = make_resolution(**kwargs)
    assert a.resolution_id == b.resolution_id
    assert a.resolution_id.startswith("RES-")


# ---------------------------------------------------------------------------
# rules.py (spec §7.3/§7.4, law L13, L14) -- acceptance #1 and #5
# ---------------------------------------------------------------------------

def make_candidate(evidence=None, resolution_type="fee_offset"):
    resolution = make_resolution(
        exception_id="EXC-BANK-BK-1", resolution_type=resolution_type,
        evidence=evidence if evidence is not None else {"currency": "USD"},
        rationale="test", resolved_by="alice", resolved_at="2026-06-01T00:00:00Z",
    )
    return resolution, candidate_rule(resolution, support_count=9) if resolution_type not in (
        "manual_one_off", "no_pattern") else None


def test_candidate_rule_refuses_first_class_outcomes():
    resolution = make_resolution(exception_id="EXC-1", resolution_type="no_pattern", evidence={},
                                  rationale="no reusable pattern here", resolved_by="alice",
                                  resolved_at="2026-06-01T00:00:00Z")
    with pytest.raises(RuleError):
        candidate_rule(resolution, support_count=0)


def test_candidate_rule_carries_resolution_id_and_support_count():
    resolution, candidate = make_candidate()
    assert candidate["resolution_id"] == resolution.resolution_id
    assert candidate["support_count"] == 9
    assert candidate["status"] == "candidate"


def test_promote_refuses_any_confirm_other_than_exact_literal(tmp_path):
    resolution, candidate = make_candidate()
    for bad_confirm in ("yes", "YES-ALWAYS", "y", "", "true", "yes-always "):
        with pytest.raises(RuleError):
            promote(candidate, promoted_by="bob", promoted_at="2026-06-02T00:00:00Z", confirm=bad_confirm,
                    rules_path=tmp_path / "rules.json", candidates_path=tmp_path / "cand.json", candidates=[candidate])
    assert not (tmp_path / "rules.json").exists()


def test_promote_writes_rules_json_with_rule_id_and_resolution_id(tmp_path):
    resolution, candidate = make_candidate()
    record = promote(candidate, promoted_by="bob", promoted_at="2026-06-02T00:00:00Z", confirm="yes-always",
                      rules_path=tmp_path / "rules.json", candidates_path=tmp_path / "cand.json",
                      candidates=[candidate])
    assert record["rule_id"] == candidate["rule_id"]
    assert record["resolution_id"] == resolution.resolution_id
    rules = load_rules(tmp_path / "rules.json")
    assert len(rules) == 1
    assert rules[0]["rule_id"] and rules[0]["resolution_id"]
    # Every promoted rule has a resolution_id (acceptance #1).
    assert all(r.get("resolution_id") for r in rules)


def test_promote_refuses_first_class_outcomes_even_with_confirm(tmp_path):
    resolution = make_resolution(exception_id="EXC-1", resolution_type="manual_one_off", evidence={},
                                  rationale="one-off", resolved_by="alice", resolved_at="2026-06-01T00:00:00Z")
    fake_candidate = {
        "rule_id": "RULE-shouldnotexist", "resolution_id": resolution.resolution_id,
        "resolution_type": "manual_one_off", "predicate": {}, "rationale": "one-off",
        "resolved_by": "alice", "resolved_at": "2026-06-01T00:00:00Z", "plain_english": "(none)",
        "support_count": 0, "status": "candidate",
    }
    with pytest.raises(RuleError):
        promote(fake_candidate, promoted_by="bob", promoted_at="2026-06-02T00:00:00Z", confirm="yes-always",
                rules_path=tmp_path / "rules.json", candidates_path=tmp_path / "cand.json",
                candidates=[fake_candidate])
    assert not (tmp_path / "rules.json").exists()


def test_promote_refuses_double_promotion(tmp_path):
    resolution, candidate = make_candidate()
    rules_path, cand_path = tmp_path / "rules.json", tmp_path / "cand.json"
    promote(candidate, promoted_by="bob", promoted_at="2026-06-02T00:00:00Z", confirm="yes-always",
            rules_path=rules_path, candidates_path=cand_path, candidates=[candidate])
    with pytest.raises(RuleError):
        promote(candidate, promoted_by="bob", promoted_at="2026-06-03T00:00:00Z", confirm="yes-always",
                rules_path=rules_path, candidates_path=cand_path, candidates=[candidate])


# ---------------------------------------------------------------------------
# apply.py unit tests -- guardrail veto (law L12) and capacity safety (law L10)
# ---------------------------------------------------------------------------

def outcome_row(bank_txn_id, ledger_id, features, status="escalated", relation="", reason="ambiguous_evidence"):
    return {
        "bank_txn_id": bank_txn_id, "status": status, "relation": relation, "ledger_id": ledger_id,
        "tier": "cheap", "score": "70.00", "margin": "70.00", "reason": reason, "reason_detail": "",
        "matched_amount": "0.00", "residual_after": "2000.00", "candidates": "[]",
        "features": json.dumps(features), "llm_model": "", "llm_confidence": "", "llm_is_stub": "True",
    }


def settlement_row(ledger_id, ledger_amount="2000.00", residual="2000.00", fully_settled="False",
                    reason="never_settled", n_parts="0", bank_txn_ids="[]"):
    return {
        "ledger_id": ledger_id, "ledger_amount": ledger_amount, "matched_amount": "0.00",
        "residual": residual, "n_parts": n_parts, "bank_txn_ids": bank_txn_ids,
        "fully_settled": fully_settled, "reason": reason,
    }


FEE_FEATURES = {
    "counterparty_key": "ACMELOGISTICS", "currency_normalized": "USD",
    "amount_delta_cents": -FEE_CENTS, "reference": "1", "amount": "conflict",
}
FEE_RULE = {
    "rule_id": "RULE-test", "resolution_id": "RES-test", "resolution_type": "fee_offset",
    "predicate": build_predicate(counterparty_key="Acme Logistics", amount_delta_min="0.00",
                                  amount_delta_max="15.00", reference_transform="exact"),
}


def test_apply_rules_resolves_a_matching_escalated_line():
    outcomes = [outcome_row("BK-1", "LG-1", FEE_FEATURES)]
    settlements = [settlement_row("LG-1")]
    ledger_rows = {"LG-1": {"counterparty_name": "Acme Logistics", "counterparty_id": "CP-1", "currency": "USD"}}
    bank_rows = {"BK-1": {"amount": "1985.00", "currency": "USD", "value_date": "2026-06-05T00:00:00Z",
                          "counterparty_name_raw": "Acme Logistics"}}
    result = apply_rules(outcomes, settlements, [FEE_RULE], ledger_rows, bank_rows, as_of="2026-06-30T00:00:00Z")
    assert len(result.hits) == 1
    assert result.hits[0]["rule_id"] == "RULE-test"
    assert result.outcomes[0]["status"] == "matched"
    assert result.outcomes[0]["reason_detail"].startswith("resolved_by_rule=RULE-test")
    assert result.settlements[0]["fully_settled"] == "True"
    assert result.settlements[0]["residual"] == "0.00"


def test_apply_rules_never_fires_on_a_row_the_guardrail_would_block():
    # upstream_reason "anomalous_amount" is Agent 1's §5.6 interlock reason for
    # a zero/flipped-sign amount -- guardrail's own upstream_veto rule carries
    # this forward as a block (see guardrail/rules.py UPSTREAM_INTERLOCK_SEVERITY).
    outcomes = [outcome_row("BK-BLOCK", "LG-1", FEE_FEATURES, reason="anomalous_amount")]
    settlements = [settlement_row("LG-1")]
    ledger_rows = {"LG-1": {"counterparty_name": "Acme Logistics", "counterparty_id": "CP-1", "currency": "USD"}}
    bank_rows = {"BK-BLOCK": {"amount": "1985.00", "currency": "USD", "value_date": "2026-06-05T00:00:00Z",
                              "counterparty_name_raw": "Acme Logistics"}}
    result = apply_rules(outcomes, settlements, [FEE_RULE], ledger_rows, bank_rows, as_of="2026-06-30T00:00:00Z")
    assert result.hits == []
    assert result.vetoed == 1
    assert result.outcomes[0]["status"] == "escalated"
    assert result.settlements[0]["fully_settled"] == "False"


def test_apply_rules_never_fires_over_the_dual_control_threshold():
    policy = load_policy()  # bundled default threshold: $200,000.00
    features = {**FEE_FEATURES, "amount_delta_cents": -FEE_CENTS}
    rule = {**FEE_RULE, "predicate": build_predicate(counterparty_key="Acme Logistics", amount_delta_min="0.00",
                                                      amount_delta_max="15.00", reference_transform="exact")}
    outcomes = [outcome_row("BK-BIG", "LG-BIG", features)]
    settlements = [settlement_row("LG-BIG", ledger_amount="250000.00", residual="250000.00")]
    ledger_rows = {"LG-BIG": {"counterparty_name": "Acme Logistics", "counterparty_id": "CP-1", "currency": "USD"}}
    bank_rows = {"BK-BIG": {"amount": "249985.00", "currency": "USD", "value_date": "2026-06-05T00:00:00Z",
                            "counterparty_name_raw": "Acme Logistics"}}
    result = apply_rules(outcomes, settlements, [rule], ledger_rows, bank_rows, as_of="2026-06-30T00:00:00Z",
                          policy=policy)
    assert result.hits == []
    assert result.vetoed == 1
    assert result.settlements[0]["fully_settled"] == "False"


def test_predicate_vocabulary_never_includes_a_bare_transaction_id():
    # Law L11: a rule is a predicate over the matcher's own feature space --
    # never "bank_txn_id X is fine". The raw bank record does ride along
    # inside a match_outcomes.csv features cell (Agent 1's own file-spine
    # contract), but the predicate evaluator must never read it.
    from ledger_sense.learning.predicate import PREDICATE_FIELDS

    assert "bank_txn_id" not in PREDICATE_FIELDS
    features_with_id_only = {"bank": {"bank_txn_id": "BK-SHOULD-NEVER-MATCH"}}
    predicate = build_predicate(counterparty_key="Acme Logistics", amount_delta_min="0.00", amount_delta_max="15.00")
    assert evaluate_predicate(predicate, features_with_id_only) is False


def test_apply_rules_never_fires_on_a_denied_party():
    policy = load_policy()  # bundled default policy_book.json, denied_parties includes "ORBEX"
    features = {**FEE_FEATURES, "counterparty_key": "ORBEX"}
    rule = {**FEE_RULE, "predicate": build_predicate(counterparty_key="Orbex", amount_delta_min="0.00",
                                                      amount_delta_max="15.00", reference_transform="exact")}
    outcomes = [outcome_row("BK-ORBEX", "LG-1", features)]
    settlements = [settlement_row("LG-1")]
    ledger_rows = {"LG-1": {"counterparty_name": "Orbex", "counterparty_id": "CP-1", "currency": "USD"}}
    bank_rows = {"BK-ORBEX": {"amount": "1985.00", "currency": "USD", "value_date": "2026-06-05T00:00:00Z",
                              "counterparty_name_raw": "Orbex"}}
    result = apply_rules(outcomes, settlements, [rule], ledger_rows, bank_rows, as_of="2026-06-30T00:00:00Z",
                          policy=policy)
    assert result.hits == []
    assert result.vetoed == 1


def test_apply_rules_precision_orbexia_corp_is_not_orbex():
    # Same precision guard spec §8.1 requires of guardrail itself: a rule must
    # not inherit a false-positive veto on an unrelated similar name either.
    policy = load_policy()
    features = {**FEE_FEATURES, "counterparty_key": "ORBEXIACORP"}
    rule = {**FEE_RULE, "predicate": build_predicate(counterparty_key="Orbexia Corp", amount_delta_min="0.00",
                                                      amount_delta_max="15.00", reference_transform="exact")}
    outcomes = [outcome_row("BK-ORBEXIA", "LG-1", features)]
    settlements = [settlement_row("LG-1")]
    ledger_rows = {"LG-1": {"counterparty_name": "Orbexia Corp", "counterparty_id": "CP-1", "currency": "USD"}}
    bank_rows = {"BK-ORBEXIA": {"amount": "1985.00", "currency": "USD", "value_date": "2026-06-05T00:00:00Z",
                                "counterparty_name_raw": "Orbexia Corp"}}
    result = apply_rules(outcomes, settlements, [rule], ledger_rows, bank_rows, as_of="2026-06-30T00:00:00Z",
                          policy=policy)
    assert len(result.hits) == 1


def test_apply_rules_never_double_settles_when_capacity_already_exhausted():
    outcomes = [outcome_row("BK-2", "LG-2", FEE_FEATURES)]
    settlements = [settlement_row("LG-2", residual="0.00", fully_settled="True", reason="fully_settled",
                                   n_parts="1", bank_txn_ids='["BK-OTHER"]')]
    ledger_rows = {"LG-2": {"counterparty_name": "Acme Logistics", "counterparty_id": "CP-1", "currency": "USD"}}
    bank_rows = {"BK-2": {"amount": "1985.00", "currency": "USD", "value_date": "2026-06-05T00:00:00Z",
                          "counterparty_name_raw": "Acme Logistics"}}
    result = apply_rules(outcomes, settlements, [FEE_RULE], ledger_rows, bank_rows, as_of="2026-06-30T00:00:00Z")
    assert result.hits == []
    assert result.no_capacity == 1
    assert result.outcomes[0]["status"] == "escalated"


def test_apply_rules_skips_non_escalated_and_duplicate_relation_rows():
    outcomes = [
        outcome_row("BK-MATCHED", "LG-3", FEE_FEATURES, status="matched"),
        outcome_row("BK-DUP", "LG-3", FEE_FEATURES, relation="duplicate"),
        outcome_row("BK-REJECTED", "LG-3", FEE_FEATURES, status="rejected"),
    ]
    settlements = [settlement_row("LG-3")]
    ledger_rows = {"LG-3": {"counterparty_name": "Acme Logistics", "counterparty_id": "CP-1", "currency": "USD"}}
    bank_rows = {bid: {"amount": "1985.00", "currency": "USD", "value_date": "2026-06-05T00:00:00Z",
                       "counterparty_name_raw": "Acme Logistics"} for bid in ("BK-MATCHED", "BK-DUP", "BK-REJECTED")}
    result = apply_rules(outcomes, settlements, [FEE_RULE], ledger_rows, bank_rows, as_of="2026-06-30T00:00:00Z")
    assert result.hits == []
    assert result.escalated_seen == 0  # none of these three count as "escalated" for rule purposes


def test_apply_rules_no_rules_loaded_changes_nothing():
    outcomes = [outcome_row("BK-1", "LG-1", FEE_FEATURES)]
    settlements = [settlement_row("LG-1")]
    result = apply_rules(outcomes, settlements, [], {}, {}, as_of="2026-06-30T00:00:00Z")
    assert result.hits == []
    assert result.outcomes == outcomes
    assert result.rules_loaded == 0


# ---------------------------------------------------------------------------
# CLI end-to-end (acceptance #7) -- exact printed contract, subprocess only
# (no in-process import of the CLI module, so this also exercises the
# installed ``ledger_sense.learning.cli:main`` entry point the console
# script ``ledger_sense`` wires up).
# ---------------------------------------------------------------------------

def run_cli(args, cwd):
    completed = subprocess.run(
        [sys.executable, "-m", "ledger_sense.learning", *args],
        cwd=cwd, capture_output=True, text=True,
    )
    return completed


def _write(path, columns, rows):
    write_csv(str(path), columns, rows)


EXCEPTION_COLUMNS = [
    "exception_id", "pass_id", "subject_kind", "bank_txn_id", "ledger_id",
    "category", "classification_detail", "match_status", "match_reason",
    "settlement_reason", "counterparty_key", "counterparty_label",
    "amount", "currency", "severity", "owner_id", "owner_name", "owner_team",
    "assignment_basis", "opened_at", "sla_hours", "due_at",
    "hours_remaining", "sla_state", "sla_display", "queue_position",
    "age_days", "evidence",
]
OUTCOME_COLUMNS = [
    "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin", "reason",
    "reason_detail", "matched_amount", "residual_after", "candidates", "features",
    "llm_model", "llm_confidence", "llm_is_stub",
]


def _exception_row(exception_id, bank_txn_id, counterparty_key="ACMELOGISTICS", subject_kind="bank"):
    return {
        "exception_id": exception_id, "pass_id": "P1", "subject_kind": subject_kind,
        "bank_txn_id": bank_txn_id, "ledger_id": "LG-1", "category": "amount_mismatch",
        "classification_detail": "test", "match_status": "escalated", "match_reason": "ambiguous_evidence",
        "settlement_reason": "", "counterparty_key": counterparty_key, "counterparty_label": "Acme Logistics",
        "amount": "1985.00", "currency": "USD", "severity": "P2", "owner_id": "OWN-1", "owner_name": "Test Owner",
        "owner_team": "AR", "assignment_basis": "test", "opened_at": "2026-06-05T00:00:00Z", "sla_hours": "48",
        "due_at": "2026-06-07T00:00:00Z", "hours_remaining": "48", "sla_state": "on_track", "sla_display": "on_track",
        "queue_position": "1", "age_days": "0", "evidence": "{}",
    }


def test_cli_resolve_prints_exact_contract_and_promote_writes_rules_json(tmp_path):
    exceptions_path, outcomes_path = tmp_path / "exceptions.csv", tmp_path / "match_outcomes.csv"
    n = 9
    _write(exceptions_path, EXCEPTION_COLUMNS,
           [_exception_row(f"EXC-BANK-BK-{i}", f"BK-{i}") for i in range(n)])
    _write(outcomes_path, OUTCOME_COLUMNS,
           [outcome_row(f"BK-{i}", "LG-1", FEE_FEATURES) for i in range(n)])

    candidates_path, rules_path = tmp_path / "candidates.json", tmp_path / "rules.json"
    resolved = run_cli([
        "resolve", "--exceptions", str(exceptions_path), "--outcomes", str(outcomes_path),
        "--exception-id", "EXC-BANK-BK-0", "--resolution-type", "fee_offset",
        "--counterparty-key", "Acme Logistics", "--amount-delta-min", "0.00", "--amount-delta-max", "15.00",
        "--reference-transform", "exact", "--rationale", "Acme deducts a flat $15 processing fee",
        "--resolved-by", "alice", "--resolved-at", "2026-06-10T00:00:00Z", "--candidates", str(candidates_path),
    ], cwd=tmp_path)
    assert resolved.returncode == 0, resolved.stderr
    assert "resolution_id=RES-" in resolved.stdout
    assert "rule_id=RULE-" in resolved.stdout
    assert "candidate predicate: counterparty=ACMELOGISTICS" in resolved.stdout
    assert f"support count against current exception pile: {n}" in resolved.stdout
    assert "status=candidate" in resolved.stdout

    rule_id = next(line.split("=", 1)[1] for line in resolved.stdout.splitlines() if line.startswith("rule_id="))
    resolution_id = next(line.split("=", 1)[1] for line in resolved.stdout.splitlines()
                          if line.startswith("resolution_id="))

    bad_promote = run_cli(["promote", rule_id, "--confirm", "yes", "--promoted-by", "bob",
                            "--promoted-at", "2026-06-10T01:00:00Z", "--rules", str(rules_path),
                            "--candidates", str(candidates_path)], cwd=tmp_path)
    assert bad_promote.returncode != 0
    assert not rules_path.exists()

    good_promote = run_cli(["promote", rule_id, "--confirm", "yes-always", "--promoted-by", "bob",
                             "--promoted-at", "2026-06-10T01:00:00Z", "--rules", str(rules_path),
                             "--candidates", str(candidates_path)], cwd=tmp_path)
    assert good_promote.returncode == 0, good_promote.stderr
    assert good_promote.stdout.strip() == f"{rule_id} <- {resolution_id}"

    rules = json.loads(rules_path.read_text())["rules"]
    assert len(rules) == 1
    assert rules[0]["rule_id"] == rule_id
    assert rules[0]["resolution_id"] == resolution_id


def test_cli_resolve_manual_one_off_never_produces_a_candidate_or_rule(tmp_path):
    exceptions_path, outcomes_path = tmp_path / "exceptions.csv", tmp_path / "match_outcomes.csv"
    _write(exceptions_path, EXCEPTION_COLUMNS, [_exception_row("EXC-BANK-BK-0", "BK-0")])
    _write(outcomes_path, OUTCOME_COLUMNS, [outcome_row("BK-0", "LG-1", FEE_FEATURES)])
    candidates_path, rules_path = tmp_path / "candidates.json", tmp_path / "rules.json"

    resolved = run_cli([
        "resolve", "--exceptions", str(exceptions_path), "--outcomes", str(outcomes_path),
        "--exception-id", "EXC-BANK-BK-0", "--resolution-type", "manual_one_off",
        "--rationale", "one-off vendor error, will not recur", "--resolved-by", "alice",
        "--resolved-at", "2026-06-10T00:00:00Z", "--candidates", str(candidates_path),
    ], cwd=tmp_path)
    assert resolved.returncode == 0, resolved.stderr
    assert "status=resolved" in resolved.stdout
    assert "rule_id=" not in resolved.stdout
    assert not candidates_path.exists() or json.loads(candidates_path.read_text())["candidates"] == []

    # There is no candidate rule_id to promote -- manual_one_off never creates a rule (acceptance #5).
    promote_attempt = run_cli(["promote", "RULE-doesnotexist", "--confirm", "yes-always", "--promoted-by", "bob",
                               "--promoted-at", "2026-06-10T01:00:00Z", "--rules", str(rules_path),
                               "--candidates", str(candidates_path)], cwd=tmp_path)
    assert promote_attempt.returncode != 0
    assert not rules_path.exists()


# ---------------------------------------------------------------------------
# Full pipeline: real matching + learning + routing CLIs (acceptance #1, 2,
# 4, 5, 6). Numbers below are measured from these subprocess runs, not
# asserted into existence -- see the printed summary at the end of the test.
# ---------------------------------------------------------------------------

FEE_LEDGER_AMOUNT = Decimal("2000.00")
N_SIBLINGS = 10  # >= the spec's 8-sibling class-elimination threshold


def _fee_pair(pass_prefix, i, ledger_id, bank_id, reference):
    """One fee_offset-shaped (ledger, bank) pair: exact reference, same
    counterparty/name/date, amount off by exactly $15 (mirrors the
    generator's OVERLAY_DEFECT in src/ledger_sense/data/generator.py) --
    alternating inflow/outflow so the predicate must key on |amount_delta|,
    not a fixed sign."""
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
    ledgers, banks = [], []
    for i in range(N_SIBLINGS):
        ledger_id, bank_id = f"LG-{pass_prefix}-FEE-{i:03d}", f"BK-{pass_prefix}-FEE-{i:03d}"
        reference = f"INV-2026-{pass_prefix}-{i:04d}"
        ledger, bank = _fee_pair(pass_prefix, i, ledger_id, bank_id, reference)
        ledgers.append(ledger)
        banks.append(bank)
    for i in range(3):
        ledger_id, bank_id = f"LG-{pass_prefix}-CLEAN-{i:03d}", f"BK-{pass_prefix}-CLEAN-{i:03d}"
        reference = f"INV-2026-{pass_prefix}-CLEAN-{i:04d}"
        ledger, bank = _clean_pair(i, ledger_id, bank_id, reference)
        ledgers.append(ledger)
        banks.append(bank)

    out = tmp_path / pass_prefix
    out.mkdir()
    write_csv(str(out / "ledger.csv"), LEDGER_COLUMNS, [e.to_row() for e in ledgers])
    write_csv(str(out / "bank.csv"), BANK_COLUMNS, [b.to_row() for b in banks])
    return out


def run_module(module, args, cwd):
    completed = subprocess.run([sys.executable, "-m", module, *args], cwd=cwd, capture_output=True, text=True)
    assert completed.returncode == 0, f"{module} {args} failed:\n{completed.stdout}\n{completed.stderr}"
    return completed


def straight_through_rate(outcomes_path, settlements_path):
    """Identical formula to tests/test_routing.py's own STR measurement:
    a bank line only counts as straight-through if it matched *and* its
    ledger side is fully settled."""
    import csv

    with open(outcomes_path, newline="", encoding="utf-8") as fh:
        outcomes = list(csv.DictReader(fh))
    with open(settlements_path, newline="", encoding="utf-8") as fh:
        settlements = {row["ledger_id"]: row for row in csv.DictReader(fh)}
    total = len(outcomes)
    straight = sum(
        1 for row in outcomes
        if row["status"] == "matched" and settlements.get(row["ledger_id"], {}).get("reason") == "fully_settled"
    )
    return straight, total


@pytest.mark.slow
def test_full_pipeline_pass2_str_climbs_from_rule_application_alone(tmp_path):
    as_of = "2026-06-30T00:00:00Z"

    # ---- Pass 1: generate, match, route; a human resolves one exception. ----
    pass1_dir = _build_fixture(tmp_path, "P1")
    run_module("ledger_sense.matching", ["--ledger", str(pass1_dir / "ledger.csv"), "--bank",
                                          str(pass1_dir / "bank.csv"), "--out-dir", str(pass1_dir)], tmp_path)
    run_module("ledger_sense.routing", [
        "--outcomes", str(pass1_dir / "match_outcomes.csv"), "--settlements", str(pass1_dir / "ledger_settlements.csv"),
        "--ledger", str(pass1_dir / "ledger.csv"), "--bank", str(pass1_dir / "bank.csv"),
        "--as-of", as_of, "--out-dir", str(pass1_dir),
    ], tmp_path)

    pass1_exceptions = _read_csv(pass1_dir / "exceptions.csv")
    fee_class_pass1 = [r for r in pass1_exceptions if r["counterparty_key"] == "ACMELOGISTICS"]
    assert len(fee_class_pass1) >= 8, f"expected >=8 fee_offset siblings, got {len(fee_class_pass1)}"
    # Each fee_offset bank line's top candidate is its own (never-settled)
    # ledger entry, so §6.3 pair-and-suppress folds bank+ledger into one
    # "pair" subject rather than two -- either way it is one exception row
    # per economic event, keyed to the bank line the rule will resolve.
    assert all(r["subject_kind"] in ("bank", "pair") for r in fee_class_pass1)
    assert all(r["bank_txn_id"] for r in fee_class_pass1)

    one_exception = fee_class_pass1[0]
    candidates_path, rules_path = tmp_path / "candidates.json", tmp_path / "rules.json"
    resolved = run_cli([
        "resolve", "--exceptions", str(pass1_dir / "exceptions.csv"),
        "--outcomes", str(pass1_dir / "match_outcomes.csv"),
        "--exception-id", one_exception["exception_id"], "--resolution-type", "fee_offset",
        "--counterparty-key", "Acme Logistics", "--amount-delta-min", "0.00", "--amount-delta-max", "15.00",
        "--reference-transform", "exact", "--rationale", "Acme deducts a flat $15 processing fee off every invoice",
        "--resolved-by", "alice", "--resolved-at", "2026-06-15T00:00:00Z", "--candidates", str(candidates_path),
    ], cwd=tmp_path)
    assert resolved.returncode == 0, resolved.stderr
    assert f"support count against current exception pile: {len(fee_class_pass1)}" in resolved.stdout
    rule_id = next(l.split("=", 1)[1] for l in resolved.stdout.splitlines() if l.startswith("rule_id="))

    promoted = run_cli(["promote", rule_id, "--confirm", "yes-always", "--promoted-by", "bob",
                        "--promoted-at", "2026-06-15T01:00:00Z", "--rules", str(rules_path),
                        "--candidates", str(candidates_path)], cwd=tmp_path)
    assert promoted.returncode == 0, promoted.stderr

    # A separate, non-manual_one_off human resolution never creates extra rules,
    # and manual_one_off/no_pattern never do either (acceptance #5).
    manual = run_cli([
        "resolve", "--exceptions", str(pass1_dir / "exceptions.csv"),
        "--outcomes", str(pass1_dir / "match_outcomes.csv"),
        "--exception-id", fee_class_pass1[1]["exception_id"], "--resolution-type", "manual_one_off",
        "--rationale", "one-off, will not recur", "--resolved-by", "alice",
        "--resolved-at", "2026-06-15T00:05:00Z", "--candidates", str(candidates_path),
    ], cwd=tmp_path)
    assert manual.returncode == 0
    rules_after_manual = json.loads(rules_path.read_text())["rules"]
    assert len(rules_after_manual) == 1  # unchanged -- manual_one_off created nothing

    # ---- Pass 2: a genuinely new draw -- different bank_txn_id/ledger_id/reference. ----
    pass2_dir = _build_fixture(tmp_path, "P2")
    run_module("ledger_sense.matching", ["--ledger", str(pass2_dir / "ledger.csv"), "--bank",
                                          str(pass2_dir / "bank.csv"), "--out-dir", str(pass2_dir)], tmp_path)

    # Control: route pass 2's raw match_outcomes.csv with NO rules applied.
    no_rules_dir = tmp_path / "P2_no_rules"
    no_rules_dir.mkdir()
    run_module("ledger_sense.routing", [
        "--outcomes", str(pass2_dir / "match_outcomes.csv"), "--settlements", str(pass2_dir / "ledger_settlements.csv"),
        "--ledger", str(pass2_dir / "ledger.csv"), "--bank", str(pass2_dir / "bank.csv"),
        "--as-of", as_of, "--out-dir", str(no_rules_dir),
    ], tmp_path)
    str_no_rules, total_no_rules = straight_through_rate(pass2_dir / "match_outcomes.csv",
                                                          pass2_dir / "ledger_settlements.csv")
    exceptions_no_rules = _read_csv(no_rules_dir / "exceptions.csv")
    fee_class_no_rules = [r for r in exceptions_no_rules if r["counterparty_key"] == "ACMELOGISTICS"]
    assert len(fee_class_no_rules) >= 8, "pass 2's own draw must independently produce the same-sized class"

    # Treatment: apply-rules (learning's pass-2 insertion) before routing.
    learned_dir = tmp_path / "P2_learned"
    learned_dir.mkdir()
    apply_result = run_cli([
        "apply-rules", "--outcomes", str(pass2_dir / "match_outcomes.csv"),
        "--settlements", str(pass2_dir / "ledger_settlements.csv"), "--ledger", str(pass2_dir / "ledger.csv"),
        "--bank", str(pass2_dir / "bank.csv"), "--rules", str(rules_path), "--as-of", as_of,
        "--out-dir", str(learned_dir),
    ], cwd=tmp_path)
    assert apply_result.returncode == 0, apply_result.stderr
    rule_hits = _read_csv(learned_dir / "rule_hits.csv")
    assert len(rule_hits) == N_SIBLINGS, f"expected all {N_SIBLINGS} pass-2 siblings to resolve, got {len(rule_hits)}"
    assert all(hit["rule_id"] == rule_id for hit in rule_hits)
    assert all(hit["resolution_id"] for hit in rule_hits)  # every auto-resolve stamps rule_id + resolution_id

    run_module("ledger_sense.routing", [
        "--outcomes", str(learned_dir / "match_outcomes.csv"), "--settlements", str(learned_dir / "ledger_settlements.csv"),
        "--ledger", str(pass2_dir / "ledger.csv"), "--bank", str(pass2_dir / "bank.csv"),
        "--as-of", as_of, "--out-dir", str(learned_dir),
    ], tmp_path)
    str_with_rules, total_with_rules = straight_through_rate(learned_dir / "match_outcomes.csv",
                                                              learned_dir / "ledger_settlements.csv")
    exceptions_with_rules = _read_csv(learned_dir / "exceptions.csv")
    fee_class_with_rules = [r for r in exceptions_with_rules if r["counterparty_key"] == "ACMELOGISTICS"]

    print(f"\nSTR pass2 without rules = {str_no_rules}/{total_no_rules} = {str_no_rules / total_no_rules * 100:.2f}%")
    print(f"STR pass2 with rules    = {str_with_rules}/{total_with_rules} = {str_with_rules / total_with_rules * 100:.2f}%")
    print(f"fee_offset exceptions without rules: {len(fee_class_no_rules)}; with rules: {len(fee_class_with_rules)}")
    print(f"rule hits: {len(rule_hits)}; straight-through delta: {str_with_rules - str_no_rules}")

    assert total_with_rules == total_no_rules  # identical data on both sides -- acceptance #6
    # Acceptance #4: pass-2 STR climbs, and the delta is exactly the rule-hit count
    # (never a coincidence of easier data, since both runs share the same pass-2 files).
    assert str_with_rules - str_no_rules == len(rule_hits)
    assert str_with_rules > str_no_rules
    # Acceptance #2: the whole exception class (all N siblings) disappears.
    assert len(fee_class_with_rules) == 0
    assert len(fee_class_no_rules) == N_SIBLINGS


def _read_csv(path):
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
