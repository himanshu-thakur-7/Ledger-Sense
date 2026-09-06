"""W12: OpenAI resolution-learning rationale assist (spec: LEDGER-SENSE-v2-PRD.md, W12).

L20: every test in this file plugs a fake transport into ``LLMClient`` (or
monkeypatches ``llm_rationale``'s own client-building/suggestion functions
before exercising the CLI) -- nothing here ever imports the real ``openai``
package or opens a socket, matching ``test_llm_client.py``'s pattern.

Layout mirrors the task's four acceptance areas:
  1. Unit tests against a mocked OpenAI client (``suggest_predicate``/``build_client``).
  2. Regression: ``OPENAI_API_KEY`` unset -> ``ledger_sense resolve`` output byte-identical to v1.
  3. ``promote --confirm yes-always`` remains the only path that writes ``rules.json``;
     a suggested-but-unconfirmed predicate never appears there.
  4. ``manual_one_off``/``no_pattern`` never receive or use a suggestion (law L13).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ledger_sense.config import Config
from ledger_sense.data.io_csv import write_csv
from ledger_sense.learning import cli as learning_cli
from ledger_sense.llm_client import LLMClient, LLMRequest, LLMResponse, TransportError

# ---------------------------------------------------------------------------
# Shared fixtures/helpers (deliberately duplicated from test_learning.py's
# schemas rather than imported -- each test file stays self-contained, same
# style as learning/io.py duplicating Agent 1/2's column names instead of
# importing them).
# ---------------------------------------------------------------------------

OUTCOME_COLUMNS = [
    "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin", "reason",
    "reason_detail", "matched_amount", "residual_after", "candidates", "features",
    "llm_model", "llm_confidence", "llm_is_stub",
]
EXCEPTION_COLUMNS = [
    "exception_id", "pass_id", "subject_kind", "bank_txn_id", "ledger_id",
    "category", "classification_detail", "match_status", "match_reason",
    "settlement_reason", "counterparty_key", "counterparty_label",
    "amount", "currency", "severity", "owner_id", "owner_name", "owner_team",
    "assignment_basis", "opened_at", "sla_hours", "due_at",
    "hours_remaining", "sla_state", "sla_display", "queue_position",
    "age_days", "evidence",
]

FEE_FEATURES = {
    "counterparty_key": "ACMELOGISTICS", "currency_normalized": "USD",
    "amount_delta_cents": -1500, "reference": "1", "amount": "conflict",
}


def _exception_row(exception_id, bank_txn_id):
    return {
        "exception_id": exception_id, "pass_id": "P1", "subject_kind": "bank",
        "bank_txn_id": bank_txn_id, "ledger_id": "LG-1", "category": "amount_mismatch",
        "classification_detail": "test", "match_status": "escalated", "match_reason": "ambiguous_evidence",
        "settlement_reason": "", "counterparty_key": "ACMELOGISTICS", "counterparty_label": "Acme Logistics",
        "amount": "1985.00", "currency": "USD", "severity": "P2", "owner_id": "OWN-1", "owner_name": "Test Owner",
        "owner_team": "AR", "assignment_basis": "test", "opened_at": "2026-06-05T00:00:00Z", "sla_hours": "48",
        "due_at": "2026-06-07T00:00:00Z", "hours_remaining": "48", "sla_state": "on_track", "sla_display": "on_track",
        "queue_position": "1", "age_days": "0", "evidence": "{}",
    }


def _outcome_row(bank_txn_id, features):
    return {
        "bank_txn_id": bank_txn_id, "status": "escalated", "relation": "", "ledger_id": "LG-1",
        "tier": "cheap", "score": "70.00", "margin": "70.00", "reason": "ambiguous_evidence", "reason_detail": "",
        "matched_amount": "0.00", "residual_after": "2000.00", "candidates": "[]",
        "features": json.dumps(features), "llm_model": "", "llm_confidence": "", "llm_is_stub": "True",
    }


def _write_exceptions_and_outcomes(tmp_path, n=3):
    exceptions_path, outcomes_path = tmp_path / "exceptions.csv", tmp_path / "match_outcomes.csv"
    write_csv(str(exceptions_path), EXCEPTION_COLUMNS, [_exception_row(f"EXC-BK-{i}", f"BK-{i}") for i in range(n)])
    write_csv(str(outcomes_path), OUTCOME_COLUMNS, [_outcome_row(f"BK-{i}", FEE_FEATURES) for i in range(n)])
    return exceptions_path, outcomes_path


def run_cli(args, cwd, env=None):
    """Same subprocess pattern as test_learning.py's run_cli, with explicit env control."""
    completed = subprocess.run(
        [sys.executable, "-m", "ledger_sense.learning", *args],
        cwd=cwd, capture_output=True, text=True, env=env,
    )
    return completed


def make_fake_client(response_text, cost_cap_usd=1.0):
    """LLMClient wired to a fake transport that always returns ``response_text``."""
    calls = []

    def transport(request: LLMRequest) -> LLMResponse:
        calls.append(request)
        return LLMResponse(text=response_text)

    client = LLMClient(transport, cost_cap_usd=cost_cap_usd)
    client._test_calls = calls  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# 1. Unit tests against a mocked OpenAI client
# ---------------------------------------------------------------------------

def test_suggest_predicate_returns_normalized_predicate_from_mocked_client():
    from ledger_sense.learning import llm_rationale

    client = make_fake_client(json.dumps({
        "counterparty_key": "Acme Logistics",
        "reference_transform": "exact",
        "amount_delta_cents_min": 0,
        "amount_delta_cents_max": 1500,
    }))

    suggestion = llm_rationale.suggest_predicate(
        resolution_type="fee_offset",
        rationale="Acme deducts a flat $15 processing fee",
        client=client,
    )

    assert suggestion == {
        "counterparty_key": "ACMELOGISTICS",  # squashed, matcher-style key (law L11)
        "reference_transform": "exact",
        "amount_delta_cents_min": 0,
        "amount_delta_cents_max": 1500,
    }
    assert len(client._test_calls) == 1


def test_suggest_predicate_drops_fields_outside_existing_vocabulary():
    from ledger_sense.learning import llm_rationale

    client = make_fake_client(json.dumps({
        "counterparty_key": "Acme Logistics",
        "brand_new_embedding_field": "should never appear (law L11)",
    }))

    suggestion = llm_rationale.suggest_predicate(
        resolution_type="counterparty_alias", rationale="renamed vendor", client=client,
    )

    assert suggestion == {"counterparty_key": "ACMELOGISTICS"}


def test_suggest_predicate_rejects_invalid_enum_value():
    from ledger_sense.learning import llm_rationale

    client = make_fake_client(json.dumps({
        "counterparty_key": "Acme Logistics",
        "reference_transform": "not-a-real-transform",
        "amount_class": "not-a-real-class",
    }))

    suggestion = llm_rationale.suggest_predicate(
        resolution_type="fee_offset", rationale="fee", client=client,
    )

    assert suggestion == {"counterparty_key": "ACMELOGISTICS"}


def test_suggest_predicate_returns_none_on_unparseable_json():
    from ledger_sense.learning import llm_rationale

    client = make_fake_client("not json at all")
    suggestion = llm_rationale.suggest_predicate(
        resolution_type="fee_offset", rationale="fee", client=client,
    )
    assert suggestion is None


def test_suggest_predicate_returns_none_on_empty_object():
    from ledger_sense.learning import llm_rationale

    client = make_fake_client("{}")
    suggestion = llm_rationale.suggest_predicate(
        resolution_type="fee_offset", rationale="fee", client=client,
    )
    assert suggestion is None


def test_suggest_predicate_returns_none_when_transport_fails_never_raises():
    from ledger_sense.learning import llm_rationale

    def failing_transport(request: LLMRequest) -> LLMResponse:
        raise TransportError("simulated outage")

    client = LLMClient(failing_transport, max_retries=0)
    suggestion = llm_rationale.suggest_predicate(
        resolution_type="fee_offset", rationale="fee", client=client,
    )
    assert suggestion is None


def test_suggest_predicate_never_calls_transport_for_manual_one_off():
    from ledger_sense.learning import llm_rationale

    def exploding_transport(request: LLMRequest) -> LLMResponse:
        raise AssertionError("manual_one_off must never reach the transport (law L13)")

    client = LLMClient(exploding_transport)
    suggestion = llm_rationale.suggest_predicate(
        resolution_type="manual_one_off", rationale="one-off vendor error", client=client,
    )
    assert suggestion is None


def test_suggest_predicate_never_calls_transport_for_no_pattern():
    from ledger_sense.learning import llm_rationale

    def exploding_transport(request: LLMRequest) -> LLMResponse:
        raise AssertionError("no_pattern must never reach the transport (law L13)")

    client = LLMClient(exploding_transport)
    suggestion = llm_rationale.suggest_predicate(
        resolution_type="no_pattern", rationale="no reusable pattern here", client=client,
    )
    assert suggestion is None


def test_build_client_returns_none_when_openai_disabled():
    from ledger_sense.learning import llm_rationale

    cfg = Config()  # no openai_api_key -> disabled (L18)
    assert llm_rationale.build_client(cfg) is None


def test_build_client_returns_configured_client_when_enabled():
    from ledger_sense.learning import llm_rationale

    cfg = Config(openai_api_key="sk-test-key", openai_model="gpt-4o", llm_cost_cap_usd=2.5)
    client = llm_rationale.build_client(cfg)
    assert client is not None
    assert client.model == "gpt-4o"
    assert client.cost_cap_usd == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# 2. Regression: OPENAI_API_KEY unset -> `ledger_sense resolve` output
#    byte-identical to v1's (no evidence flags AND with evidence flags).
# ---------------------------------------------------------------------------

def _env_without_openai_key(tmp_path):
    import os
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    # Ensure no stray .env in tmp_path/parents leaks a key into this run.
    env["LEDGER_SENSE_OPENAI_MODEL"] = env.get("LEDGER_SENSE_OPENAI_MODEL", "gpt-4o-mini")
    return env


def test_resolve_with_no_evidence_and_no_key_fails_exactly_like_v1(tmp_path):
    exceptions_path, outcomes_path = _write_exceptions_and_outcomes(tmp_path, n=1)
    candidates_path = tmp_path / "candidates.json"

    result = run_cli([
        "resolve", "--exceptions", str(exceptions_path), "--outcomes", str(outcomes_path),
        "--exception-id", "EXC-BK-0", "--resolution-type", "fee_offset",
        "--rationale", "Acme deducts a flat $15 processing fee",
        "--resolved-by", "alice", "--resolved-at", "2026-06-10T00:00:00Z",
        "--candidates", str(candidates_path),
    ], cwd=tmp_path, env=_env_without_openai_key(tmp_path))

    # v1 behavior, unchanged: no evidence -> refused, no SUGGESTION noise, nothing written.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "resolve refused: a pattern resolution needs at least one evidence/predicate field over "
        "the matcher's own feature space (law L11) -- use manual_one_off or no_pattern for a "
        "resolution with no pattern"
    )
    assert not candidates_path.exists()


def test_resolve_with_explicit_evidence_and_no_key_matches_v1_contract(tmp_path):
    exceptions_path, outcomes_path = _write_exceptions_and_outcomes(tmp_path, n=4)
    candidates_path = tmp_path / "candidates.json"

    result = run_cli([
        "resolve", "--exceptions", str(exceptions_path), "--outcomes", str(outcomes_path),
        "--exception-id", "EXC-BK-0", "--resolution-type", "fee_offset",
        "--counterparty-key", "Acme Logistics", "--amount-delta-min", "0.00", "--amount-delta-max", "15.00",
        "--reference-transform", "exact", "--rationale", "Acme deducts a flat $15 processing fee",
        "--resolved-by", "alice", "--resolved-at", "2026-06-10T00:00:00Z",
        "--candidates", str(candidates_path),
    ], cwd=tmp_path, env=_env_without_openai_key(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "SUGGESTION" not in result.stdout
    lines = result.stdout.splitlines()
    assert lines[0].startswith("resolution_id=RES-")
    assert lines[1] == "exception_id=EXC-BK-0"
    assert lines[2] == "resolution_type=fee_offset"
    assert lines[3].startswith("rule_id=RULE-")
    assert lines[4] == "candidate predicate: counterparty=ACMELOGISTICS AND 0.00 < |amount_delta| <= 15.00 AND reference=exact"
    assert lines[5] == "support count against current exception pile: 4"
    assert lines[6] == "status=candidate"


# ---------------------------------------------------------------------------
# 3. `promote --confirm yes-always` remains the only path that writes
#    rules.json; a suggested-but-unconfirmed predicate never appears there.
# ---------------------------------------------------------------------------

def test_suggestion_becomes_candidate_only_promote_writes_rules_json(tmp_path, monkeypatch, capsys):
    from ledger_sense.learning import llm_rationale

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    suggested = {"counterparty_key": "ACMELOGISTICS", "reference_transform": "exact",
                 "amount_delta_cents_min": 0, "amount_delta_cents_max": 1500}
    monkeypatch.setattr(
        llm_rationale, "suggest_predicate",
        lambda *, resolution_type, rationale, client, **kw: suggested,
    )

    exceptions_path, outcomes_path = _write_exceptions_and_outcomes(tmp_path, n=2)
    candidates_path = tmp_path / "candidates.json"
    rules_path = tmp_path / "rules.json"

    parser = learning_cli.build_arg_parser()
    args = parser.parse_args([
        "resolve", "--exceptions", str(exceptions_path), "--outcomes", str(outcomes_path),
        "--exception-id", "EXC-BK-0", "--resolution-type", "fee_offset",
        "--rationale", "Acme deducts a flat $15 processing fee",
        "--resolved-by", "alice", "--resolved-at", "2026-06-10T00:00:00Z",
        "--candidates", str(candidates_path),
    ])
    rc = learning_cli.cmd_resolve(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "SUGGESTION" in out
    assert "counterparty=ACMELOGISTICS" in out

    rule_id = next(line.split("=", 1)[1] for line in out.splitlines() if line.startswith("rule_id="))
    candidates = json.loads(candidates_path.read_text())["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["predicate"] == suggested

    # The suggestion is only a candidate so far -- rules.json must not exist yet.
    assert not rules_path.exists()

    # A non-"yes-always" confirm must still refuse, and rules.json still must not exist.
    bad_args = parser.parse_args([
        "promote", rule_id, "--confirm", "no-thanks", "--promoted-by", "bob",
        "--promoted-at", "2026-06-10T01:00:00Z", "--rules", str(rules_path), "--candidates", str(candidates_path),
    ])
    bad_rc = learning_cli.cmd_promote(bad_args)
    assert bad_rc != 0
    assert not rules_path.exists()

    # Only the explicit "yes-always" confirm actually writes rules.json.
    good_args = parser.parse_args([
        "promote", rule_id, "--confirm", "yes-always", "--promoted-by", "bob",
        "--promoted-at", "2026-06-10T01:00:00Z", "--rules", str(rules_path), "--candidates", str(candidates_path),
    ])
    good_rc = learning_cli.cmd_promote(good_args)
    assert good_rc == 0
    rules = json.loads(rules_path.read_text())["rules"]
    assert len(rules) == 1
    assert rules[0]["predicate"] == suggested


# ---------------------------------------------------------------------------
# 4. manual_one_off/no_pattern never receive or use a suggestion (law L13).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resolution_type,expected_status_suffix", [
    ("manual_one_off", "status=resolved (first-class outcome, law L13 -- no candidate rule, ever)"),
    ("no_pattern", "status=resolved (first-class outcome, law L13 -- no candidate rule, ever)"),
])
def test_non_rule_types_never_receive_a_suggestion_even_when_openai_enabled(
    tmp_path, monkeypatch, capsys, resolution_type, expected_status_suffix,
):
    from ledger_sense.learning import llm_rationale

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

    def exploding_suggest(*, resolution_type, rationale, client, **kw):
        raise AssertionError(f"{resolution_type} must never receive a suggestion (law L13)")

    monkeypatch.setattr(llm_rationale, "suggest_predicate", exploding_suggest)

    exceptions_path, outcomes_path = _write_exceptions_and_outcomes(tmp_path, n=1)
    candidates_path = tmp_path / "candidates.json"

    parser = learning_cli.build_arg_parser()
    args = parser.parse_args([
        "resolve", "--exceptions", str(exceptions_path), "--outcomes", str(outcomes_path),
        "--exception-id", "EXC-BK-0", "--resolution-type", resolution_type,
        "--rationale", "one-off vendor error, will not recur",
        "--resolved-by", "alice", "--resolved-at", "2026-06-10T00:00:00Z",
        "--candidates", str(candidates_path),
    ])
    rc = learning_cli.cmd_resolve(args)
    out = capsys.readouterr().out

    assert rc == 0
    assert "SUGGESTION" not in out
    assert expected_status_suffix in out
    assert not candidates_path.exists() or json.loads(candidates_path.read_text())["candidates"] == []
