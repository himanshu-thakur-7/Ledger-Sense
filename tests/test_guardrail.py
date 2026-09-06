"""W4: fixture-first acceptance for the Agent 4 guardrail (spec §8)."""

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_sense.data.io_csv import write_csv
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS, BankTransaction, LedgerEntry
from ledger_sense.data.money import to_money
from ledger_sense.guardrail import load_policy, run, would_block_or_hold
from ledger_sense.guardrail.csv_io import AUDIT_COLUMNS, HELD_COLUMNS, OUTCOME_COLUMNS, RELEASE_COLUMNS, SETTLEMENT_COLUMNS
from ledger_sense.guardrail.normalize import contains_token_sequence, normalize_tokens
from ledger_sense.guardrail.rules import RULE_ORDER

FIXTURE = Path(__file__).parent / "fixtures" / "mini_pass1"
AS_OF = datetime(2026, 3, 31, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixture builders -- hand-built rows, spec §8's exact four input files only.
# ---------------------------------------------------------------------------

def write_ledger(path, entries):
    write_csv(str(path), LEDGER_COLUMNS, [e.to_row() for e in entries])


def write_bank(path, entries):
    write_csv(str(path), BANK_COLUMNS, [e.to_row() for e in entries])


def write_outcomes(path, rows):
    write_csv(str(path), OUTCOME_COLUMNS, rows)


def write_settlements(path, rows):
    write_csv(str(path), SETTLEMENT_COLUMNS, rows)


def outcome_row(bank_txn_id, ledger_id="", status="matched", tier="cheap", reason="high_confidence",
                 relation="exact", matched_amount="0.00"):
    return {
        "bank_txn_id": bank_txn_id, "status": status, "relation": relation, "ledger_id": ledger_id,
        "tier": tier, "score": "100.00", "margin": "50.00", "reason": reason, "reason_detail": "",
        "matched_amount": matched_amount, "residual_after": "0.00", "candidates": "[]", "features": "{}",
        "llm_model": "", "llm_confidence": "", "llm_is_stub": "True",
    }


def settlement_row(ledger_id, ledger_amount, bank_txn_ids):
    return {
        "ledger_id": ledger_id, "ledger_amount": ledger_amount, "matched_amount": ledger_amount, "residual": "0.00",
        "n_parts": str(len(bank_txn_ids)), "bank_txn_ids": json.dumps(list(bank_txn_ids)), "fully_settled": "True",
        "reason": "fully_settled",
    }


def ledger(id, amount="100.00", reference="INV-1", name="Acme Logistics", counterparty_id="CP-1"):
    return LedgerEntry(id, "2026-03-10T00:00:00Z", to_money(amount), "USD", "invoice_payment",
                       counterparty_id, name, reference, "", "1200", "billing")


def bank(id, amount="100.00", reference="INV-1", name="Acme Logistics", value_date="2026-03-10T00:00:00Z"):
    return BankTransaction(id, value_date, to_money(amount), "USD", name, reference, "",
                           "ACCT-USD-01", "STMT-1", "credit")


def build_batch(tmp_path, ledgers, banks, outcomes, settlements):
    ledger_path, bank_path, outcomes_path, settlements_path = (
        tmp_path / "ledger.csv", tmp_path / "bank.csv", tmp_path / "outcomes.csv", tmp_path / "settlements.csv",
    )
    write_ledger(ledger_path, ledgers)
    write_bank(bank_path, banks)
    write_outcomes(outcomes_path, outcomes)
    write_settlements(settlements_path, settlements)
    return ledger_path, bank_path, outcomes_path, settlements_path


def rows(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def decisions_by_id(out_dir):
    return {r["bank_txn_id"]: r for r in rows(out_dir / "release_decisions.csv")}


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

def test_output_contract_columns_and_one_row_per_bank_line(tmp_path):
    ledgers = [ledger("LG-1")]
    banks = [bank("BK-1")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "100.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    result = run(*paths, AS_OF, tmp_path / "out")

    release = rows(tmp_path / "out" / "release_decisions.csv")
    assert list(release[0]) == RELEASE_COLUMNS
    assert len(release) == 1  # AC1: exactly one release decision per bank line
    policy_applied = json.loads((tmp_path / "out" / "policy_applied.json").read_text())
    assert policy_applied["policy_version"] == result.policy_applied["policy_version"]


def test_release_decisions_and_audit_header_order(tmp_path):
    ledgers = [ledger("LG-1")]
    banks = [bank("BK-1")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "100.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")

    with (tmp_path / "out" / "release_decisions.csv").open(newline="") as f:
        assert next(csv.reader(f)) == RELEASE_COLUMNS
    with (tmp_path / "out" / "guardrail_audit.csv").open(newline="") as f:
        assert next(csv.reader(f)) == AUDIT_COLUMNS
    with (tmp_path / "out" / "held_settlements.csv").open(newline="") as f:
        assert next(csv.reader(f)) == HELD_COLUMNS


# ---------------------------------------------------------------------------
# denied_party -- block, with the precision guard
# ---------------------------------------------------------------------------

def test_denied_party_blocks_on_exact_list_token():
    policy = load_policy()
    assert any(e["name"] == "ORBEX" for e in policy["denied_parties"]), "fixture assumes ORBEX is in the default list"


def test_denied_party_precision_orbex_never_fires_on_orbexia(tmp_path):
    ledgers = [ledger("LG-1", name="Orbexia Corp"), ledger("LG-2", name="Orbex Holdings")]
    banks = [bank("BK-1", name="ORBEXIA CORP"), bank("BK-2", name="ORBEX HOLDINGS")]
    outcomes = [outcome_row("BK-1", "LG-1"), outcome_row("BK-2", "LG-2")]
    settlements = [settlement_row("LG-1", "100.00", ["BK-1"]), settlement_row("LG-2", "100.00", ["BK-2"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")

    assert decisions["BK-1"]["verdict"] == "allow", "ORBEX must never fire on unrelated similar name ORBEXIA CORP"
    assert decisions["BK-2"]["verdict"] == "block"
    assert decisions["BK-2"]["primary_rule"] == "denied_party"


def test_denied_party_token_matching_helper_directly():
    orbex = normalize_tokens("ORBEX")
    assert contains_token_sequence(normalize_tokens("Orbex Holdings Ltd"), orbex)
    assert not contains_token_sequence(normalize_tokens("Orbexia Corp"), orbex)


# ---------------------------------------------------------------------------
# duplicate_release -- independently re-detected, block
# ---------------------------------------------------------------------------

def test_duplicate_release_blocks_second_leg_even_without_agent1_flag(tmp_path):
    # Agent 1 reports BOTH legs as clean, independent matches -- guardrail must
    # re-derive the duplicate itself, not trust (or need) any upstream flag.
    ledgers = [ledger("LG-1", amount="145.73", reference="INV-2026-1000006", name="Nova Media LLC"),
               ledger("LG-2", amount="145.73", reference="INV-2026-1000006", name="Nova Media LLC")]
    banks = [bank("BK-1", amount="145.73", reference="INV-2026-1000006", name="Nova M. LLC",
                  value_date="2026-03-13T02:09:33Z"),
             bank("BK-2", amount="145.73", reference="INV-2026-1000006", name="Nova Media LL",
                  value_date="2026-03-13T18:30:57Z")]
    outcomes = [outcome_row("BK-1", "LG-1"), outcome_row("BK-2", "LG-2")]
    settlements = [settlement_row("LG-1", "145.73", ["BK-1"]), settlement_row("LG-2", "145.73", ["BK-2"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")

    assert decisions["BK-1"]["verdict"] == "allow"
    assert decisions["BK-2"]["verdict"] == "block"
    assert decisions["BK-2"]["primary_rule"] == "duplicate_release"
    assert "BK-1" in decisions["BK-2"]["reason"]


def test_duplicate_release_never_blocks_the_leg_agent1_already_matched(tmp_path):
    # Regression: the two legs' EARLIER value_date is not the one Agent 1
    # settled against the ledger obligation (its own tie-break is (score,
    # bank_txn_id), not value_date). A naive value_date-only tie-break would
    # block the clean, already-matched leg here -- exactly what "never block
    # a clean cheap-tier auto-match line" forbids.
    ledgers = [ledger("LG-1", amount="3631.15", reference="INV-1", name="Acme Logistics")]
    banks = [bank("BK-earlier", amount="3631.15", reference="INV-1", value_date="2026-03-05T17:14:24Z"),
             bank("BK-later", amount="3631.15", reference="INV-1", value_date="2026-03-07T08:25:55Z")]
    outcomes = [
        outcome_row("BK-earlier", "LG-1", status="escalated", reason="duplicate_of_matched",
                    relation="duplicate", matched_amount="0.00"),
        outcome_row("BK-later", "LG-1", status="matched", reason="high_confidence", matched_amount="3631.15"),
    ]
    settlements = [settlement_row("LG-1", "3631.15", ["BK-later"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-later"]["verdict"] == "allow", "must never block the leg Agent 1 already cleanly matched"
    assert decisions["BK-earlier"]["verdict"] == "block"
    assert decisions["BK-earlier"]["primary_rule"] == "duplicate_release"


def test_duplicate_release_ignores_different_reference_same_amount(tmp_path):
    # Recurring same-amount charges to the same counterparty, different invoice
    # -- must never be treated as a duplicate.
    ledgers = [ledger("LG-1", amount="50.00", reference="INV-1"), ledger("LG-2", amount="50.00", reference="INV-2")]
    banks = [bank("BK-1", amount="50.00", reference="INV-1"), bank("BK-2", amount="50.00", reference="INV-2")]
    outcomes = [outcome_row("BK-1", "LG-1"), outcome_row("BK-2", "LG-2")]
    settlements = [settlement_row("LG-1", "50.00", ["BK-1"]), settlement_row("LG-2", "50.00", ["BK-2"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "allow"
    assert decisions["BK-2"]["verdict"] == "allow"


# ---------------------------------------------------------------------------
# dual_control -- exact threshold, hold
# ---------------------------------------------------------------------------

def test_dual_control_hold_at_exact_threshold(tmp_path):
    ledgers = [ledger("LG-1", amount="200000.00")]
    banks = [bank("BK-1", amount="200000.00")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "200000.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "hold"
    assert decisions["BK-1"]["primary_rule"] == "dual_control"
    assert decisions["BK-1"]["required_approvals"] != ""


def test_dual_control_does_not_fire_one_cent_below_threshold(tmp_path):
    ledgers = [ledger("LG-1", amount="199999.99")]
    banks = [bank("BK-1", amount="199999.99")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "199999.99", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "allow"
    assert "dual_control" not in decisions["BK-1"]["all_firing_rules"]


def test_dual_control_fires_on_negative_amount_past_threshold(tmp_path):
    ledgers = [ledger("LG-1", amount="-250000.00")]
    banks = [bank("BK-1", amount="-250000.00")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "-250000.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "hold"
    assert decisions["BK-1"]["primary_rule"] == "dual_control"


# ---------------------------------------------------------------------------
# out_of_period -- default window is the calendar month of --as-of
# ---------------------------------------------------------------------------

def test_out_of_period_hold_outside_default_calendar_month(tmp_path):
    ledgers = [ledger("LG-1")]
    banks = [bank("BK-1", value_date="2026-04-02T00:00:00Z")]  # AS_OF is in March
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "100.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "hold"
    assert decisions["BK-1"]["primary_rule"] == "out_of_period"


def test_in_period_allows(tmp_path):
    ledgers = [ledger("LG-1")]
    banks = [bank("BK-1", value_date="2026-03-15T00:00:00Z")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "100.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "allow"


def test_explicit_period_flags_override_default(tmp_path):
    ledgers = [ledger("LG-1")]
    banks = [bank("BK-1", value_date="2026-04-02T00:00:00Z")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "100.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out", period_start="2026-04-01T00:00:00Z", period_end="2026-05-01T00:00:00Z")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "allow"


# ---------------------------------------------------------------------------
# upstream_veto -- matches upstream severity
# ---------------------------------------------------------------------------

def test_upstream_veto_block_for_anomalous_amount(tmp_path):
    ledgers = [ledger("LG-1")]
    banks = [bank("BK-1")]
    outcomes = [outcome_row("BK-1", "LG-1", status="escalated", reason="anomalous_amount")]
    settlements = [settlement_row("LG-1", "100.00", [])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "block"
    assert decisions["BK-1"]["primary_rule"] == "upstream_veto"
    assert decisions["BK-1"]["required_approvals"] == ""


def test_upstream_veto_hold_for_currency_conflict(tmp_path):
    ledgers = [ledger("LG-1")]
    banks = [bank("BK-1")]
    outcomes = [outcome_row("BK-1", "LG-1", status="escalated", reason="currency_conflict")]
    settlements = [settlement_row("LG-1", "100.00", [])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "hold"
    assert decisions["BK-1"]["primary_rule"] == "upstream_veto"
    assert decisions["BK-1"]["required_approvals"] != ""


# ---------------------------------------------------------------------------
# Priority: block beats hold beats allow
# ---------------------------------------------------------------------------

def test_block_beats_hold_when_both_fire(tmp_path):
    # dual_control (hold) AND denied_party (block) both fire on the same line.
    ledgers = [ledger("LG-1", amount="250000.00", name="Orbex Holdings")]
    banks = [bank("BK-1", amount="250000.00", name="ORBEX HOLDINGS")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "250000.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "block"
    assert decisions["BK-1"]["primary_rule"] == "denied_party"
    assert set(decisions["BK-1"]["all_firing_rules"].split("|")) == {"denied_party", "dual_control"}
    assert decisions["BK-1"]["required_approvals"] == "", "AC4: a blocked line names no approvals"


# ---------------------------------------------------------------------------
# AC2/AC3/AC4 and "never block a clean cheap-tier auto-match"
# ---------------------------------------------------------------------------

def test_ac2_every_block_and_hold_names_rule_reason_and_upstream_context(tmp_path):
    ledgers = [ledger("LG-1", amount="250000.00")]
    banks = [bank("BK-1", amount="250000.00")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "250000.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decision = decisions_by_id(tmp_path / "out")["BK-1"]
    assert decision["verdict"] == "hold"
    assert decision["primary_rule"]
    assert decision["reason"]
    assert decision["upstream_context"]
    assert decision["required_approvals"]


def test_ac3_every_audit_row_cites_a_known_policy_rule(tmp_path):
    ledgers = [ledger("LG-1", amount="250000.00", name="Orbex Holdings")]
    banks = [bank("BK-1", amount="250000.00", name="ORBEX HOLDINGS")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "250000.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    audit = rows(tmp_path / "out" / "guardrail_audit.csv")
    assert len(audit) == 2  # denied_party + dual_control both fired
    for row in audit:
        assert row["rule"] in RULE_ORDER


def test_ac4_blocked_lines_are_never_in_held_settlements(tmp_path):
    ledgers = [ledger("LG-1", name="Orbex Holdings")]
    banks = [bank("BK-1", name="ORBEX HOLDINGS")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "100.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    held = {r["bank_txn_id"] for r in rows(tmp_path / "out" / "held_settlements.csv")}
    assert "BK-1" not in held
    decisions = decisions_by_id(tmp_path / "out")
    assert decisions["BK-1"]["verdict"] == "block"


def test_never_blocks_a_clean_cheap_tier_auto_match(tmp_path):
    ledgers = [ledger(f"LG-{i}", amount="1234.56", reference=f"INV-{i}", name=f"Widgets Co {i}") for i in range(5)]
    banks = [bank(f"BK-{i}", amount="1234.56", reference=f"INV-{i}", name=f"Widgets Co {i}") for i in range(5)]
    outcomes = [outcome_row(f"BK-{i}", f"LG-{i}") for i in range(5)]
    settlements = [settlement_row(f"LG-{i}", "1234.56", [f"BK-{i}"]) for i in range(5)]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out")
    decisions = decisions_by_id(tmp_path / "out")
    for i in range(5):
        assert decisions[f"BK-{i}"]["verdict"] == "allow"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_two_runs_byte_identical(tmp_path):
    ledgers = [ledger("LG-1", amount="250000.00", name="Orbex Holdings"), ledger("LG-2", amount="50.00")]
    banks = [bank("BK-1", amount="250000.00", name="ORBEX HOLDINGS"), bank("BK-2", amount="50.00")]
    outcomes = [outcome_row("BK-1", "LG-1"), outcome_row("BK-2", "LG-2")]
    settlements = [settlement_row("LG-1", "250000.00", ["BK-1"]), settlement_row("LG-2", "50.00", ["BK-2"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out_a")
    run(*paths, AS_OF, tmp_path / "out_b")
    for name in ("release_decisions.csv", "guardrail_audit.csv", "held_settlements.csv", "policy_applied.json"):
        assert (tmp_path / "out_a" / name).read_bytes() == (tmp_path / "out_b" / name).read_bytes(), name


# ---------------------------------------------------------------------------
# would_block_or_hold -- the Agent 3 seam
# ---------------------------------------------------------------------------

def test_would_block_or_hold_denied_party():
    assert would_block_or_hold({"counterparty_name": "Orbex Holdings", "amount": "10.00", "currency": "USD"}) == "block"


def test_would_block_or_hold_dual_control():
    assert would_block_or_hold({"counterparty_name": "Acme", "amount": "200000.00", "currency": "USD"}) == "hold"


def test_would_block_or_hold_allow_on_ordinary_line():
    assert would_block_or_hold({"counterparty_name": "Acme", "amount": "10.00", "currency": "USD"}) == "allow"


def test_would_block_or_hold_upstream_veto():
    assert would_block_or_hold({"counterparty_name": "Acme", "amount": "10.00", "currency": "USD",
                                "upstream_reason": "anomalous_amount"}) == "block"


def test_would_block_or_hold_out_of_period_needs_explicit_clock():
    verdict = would_block_or_hold({
        "counterparty_name": "Acme", "amount": "10.00", "currency": "USD",
        "value_date": "2026-04-02T00:00:00Z", "as_of": "2026-03-31T00:00:00Z",
    })
    assert verdict == "hold"


# ---------------------------------------------------------------------------
# Policy book is JSON-overridable
# ---------------------------------------------------------------------------

def test_policy_book_is_json_overridable(tmp_path):
    custom = tmp_path / "custom_policy.json"
    custom.write_text(json.dumps({
        "policy_version": "2099.01-test",
        "denied_parties": [{"name": "TOTALLY FAKE CO", "id": None}],
        "dual_control_threshold": "50.00",
        "required_approvals": {"dual_control": ["ceo"], "out_of_period": [], "upstream_veto_hold": []},
    }))
    ledgers = [ledger("LG-1", amount="60.00")]
    banks = [bank("BK-1", amount="60.00")]
    outcomes = [outcome_row("BK-1", "LG-1")]
    settlements = [settlement_row("LG-1", "60.00", ["BK-1"])]
    paths = build_batch(tmp_path, ledgers, banks, outcomes, settlements)
    run(*paths, AS_OF, tmp_path / "out", policy_path=custom)
    decision = decisions_by_id(tmp_path / "out")["BK-1"]
    assert decision["policy_version"] == "2099.01-test"
    assert decision["verdict"] == "hold"  # $60 clears the overridden $50 threshold
    assert decision["primary_rule"] == "dual_control"


# ---------------------------------------------------------------------------
# No float() anywhere -- source-level AST check on this package too
# ---------------------------------------------------------------------------

def test_no_float_literals_or_calls_in_guardrail_package():
    import ast
    import ledger_sense.guardrail as pkg
    package = Path(pkg.__file__).parent
    for file in package.rglob("*.py"):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float), file
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "float", file


@pytest.mark.parametrize("path", [FIXTURE / "ledger.csv", FIXTURE / "bank.csv"])
def test_mini_pass1_ledger_and_bank_are_readable_inputs(path):
    # Sanity check that the shared mini_pass1 fixture's two non-ground-truth
    # files parse with our own strict header check -- guardrail never opens
    # match_links.csv from this fixture (see test_guardrail_isolation.py).
    assert path.exists()
    with path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == (LEDGER_COLUMNS if "ledger" in path.name else BANK_COLUMNS)
