"""W13: OpenAI routing fallback classifier -- extends routing/classify.py's
rule 7 (`unidentified_counterpart`, "no earlier condition matched") only.

L20: every test here plugs a fake transport into ``LLMClient`` (mirrors
``tests/test_llm_client.py``'s pattern) or monkeypatches
``llm_classifier.get_client`` wholesale -- no test in this file may import
``openai`` or open a socket.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_sense.config import Config
from ledger_sense.data.io_csv import write_csv
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS, BankTransaction
from ledger_sense.data.money import to_money
from ledger_sense.guardrail import run as guardrail_run
from ledger_sense.llm_client import LLMClient, LLMResponse, TransportError
from ledger_sense.routing import classify, engine, llm_classifier
from ledger_sense.routing.io import OUTCOME_COLUMNS, SETTLEMENT_COLUMNS
from ledger_sense.routing.io import run as routing_run

AS_OF_STR = "2026-06-15T00:00:00Z"
AS_OF_DT = datetime(2026, 6, 15, tzinfo=timezone.utc)


def rows(path):
    import csv
    with Path(path).open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


# ---------------------------------------------------------------------------
# Fixture builders -- hand-built rows, mirrors tests/test_guardrail.py.
# ---------------------------------------------------------------------------

def make_bank(bank_txn_id, amount="100.00", name="Some Counterparty", value_date="2026-06-01T00:00:00Z"):
    return BankTransaction(bank_txn_id, value_date, to_money(amount), "USD", name, "REF-1", "",
                            "ACCT-USD-01", "STMT-1", "credit")


def outcome_row(bank_txn_id, reason, relation="", features=None, status="no_candidate", ledger_id=""):
    return {
        "bank_txn_id": bank_txn_id, "status": status, "relation": relation, "ledger_id": ledger_id,
        "tier": "cheap", "score": "0.00", "margin": "0.00", "reason": reason, "reason_detail": "",
        "matched_amount": "0.00", "residual_after": "0.00", "candidates": "[]",
        "features": json.dumps(features or {}), "llm_model": "", "llm_confidence": "", "llm_is_stub": "True",
    }


def _build_batch(tmp_path):
    """BK-1..4 hit rules 1-4; BK-9 is a rule-7 case that is *also* dual-control
    (amount 250000 >= the 200000 policy threshold) so guardrail reaches a real,
    non-trivial verdict for it."""
    ledger_path, bank_path, outcomes_path, settlements_path = (
        tmp_path / "ledger.csv", tmp_path / "bank.csv", tmp_path / "outcomes.csv", tmp_path / "settlements.csv",
    )
    write_csv(str(ledger_path), LEDGER_COLUMNS, [])
    banks = [
        make_bank("BK-1"),
        make_bank("BK-2"),
        make_bank("BK-3"),
        make_bank("BK-4"),
        make_bank("BK-9", amount="250000.00"),
    ]
    write_csv(str(bank_path), BANK_COLUMNS, [b.to_row() for b in banks])
    outcomes = [
        outcome_row("BK-1", "anomalous_amount"),
        outcome_row("BK-2", "ambiguous_evidence", relation="duplicate"),
        outcome_row("BK-3", "ledger_already_settled"),
        outcome_row("BK-4", "ambiguous_evidence", features={"amount": "conflict", "name": "1.0"}),
        outcome_row("BK-9", "no_candidate"),
    ]
    write_csv(str(outcomes_path), OUTCOME_COLUMNS, outcomes)
    write_csv(str(settlements_path), SETTLEMENT_COLUMNS, [])
    return ledger_path, bank_path, outcomes_path, settlements_path


def _run_routing(ledger_path, bank_path, outcomes_path, settlements_path, out_dir):
    out_dir.mkdir(exist_ok=True)
    return routing_run(outcomes_path, settlements_path, ledger_path, bank_path, AS_OF_STR, out_dir)


def _mock_llm_client_builder(calls, category="amount_mismatch", confidence="0.60"):
    def builder(cfg):
        def transport(request):
            calls.append(request)
            return LLMResponse(text=json.dumps({"category": category, "confidence": confidence}))
        return LLMClient(transport, model=cfg.openai_model, cost_cap_usd=cfg.llm_cost_cap_usd)
    return builder


# ---------------------------------------------------------------------------
# 1. Unit tests against a mocked OpenAI client (classify_via_llm, in isolation).
# ---------------------------------------------------------------------------

def test_classify_via_llm_returns_category_and_confidence_from_mocked_client():
    def transport(request):
        assert request.model == "gpt-4o-mini"
        return LLMResponse(text=json.dumps({"category": "timing", "confidence": "0.83"}))
    client = LLMClient(transport, model="gpt-4o-mini")

    result = llm_classifier.classify_via_llm(client, "BK-1", "no_candidate", "", {})

    assert result.category == "timing"
    assert result.confidence == Decimal("0.83")


def test_classify_via_llm_rejects_a_sixth_category():
    def transport(request):
        return LLMResponse(text=json.dumps({"category": "fraud", "confidence": "0.99"}))
    client = LLMClient(transport)

    assert llm_classifier.classify_via_llm(client, "BK-1", "no_candidate", "", {}) is None


def test_classify_via_llm_rejects_confidence_out_of_range():
    def transport(request):
        return LLMResponse(text=json.dumps({"category": "timing", "confidence": "1.5"}))
    client = LLMClient(transport)

    assert llm_classifier.classify_via_llm(client, "BK-1", "no_candidate", "", {}) is None


def test_classify_via_llm_rejects_malformed_json():
    def transport(request):
        return LLMResponse(text="not json at all")
    client = LLMClient(transport)

    assert llm_classifier.classify_via_llm(client, "BK-1", "no_candidate", "", {}) is None


def test_classify_via_llm_returns_none_on_transport_error_never_raises():
    def transport(request):
        raise TransportError("boom")
    client = LLMClient(transport, max_retries=0)

    assert llm_classifier.classify_via_llm(client, "BK-1", "no_candidate", "", {}) is None


def test_classify_via_llm_returns_none_once_cost_cap_is_exhausted():
    def transport(request):
        return LLMResponse(text=json.dumps({"category": "timing", "confidence": "0.5"}), cost_usd=5.0)
    client = LLMClient(transport, cost_cap_usd=1.00)

    first = llm_classifier.classify_via_llm(client, "BK-1", "no_candidate", "", {})
    assert first is not None and first.category == "timing"

    # Cumulative cost (5.0) now exceeds the 1.00 cap -- the next call is
    # refused pre-flight (CostCapExceeded) and must degrade gracefully, not raise.
    second = llm_classifier.classify_via_llm(client, "BK-2", "no_candidate", "", {})
    assert second is None


# ---------------------------------------------------------------------------
# 1b. Unit tests for apply_llm_fallback -- the function routing/engine.py calls.
# ---------------------------------------------------------------------------

def test_apply_llm_fallback_ignores_non_rule7_details():
    def exploding_builder(cfg):
        raise AssertionError("must not build a client for a non-rule-7 detail")

    category, detail, confidence = llm_classifier.apply_llm_fallback(
        Config(openai_api_key="sk-test"), "BK-3", "ledger_already_settled", "", {},
        "amount_mismatch", "bank-rule-3: reason=ledger_already_settled",
        client_builder=exploding_builder,
    )

    assert (category, detail, confidence) == (
        "amount_mismatch", "bank-rule-3: reason=ledger_already_settled", None,
    )


def test_apply_llm_fallback_classifies_a_rule7_row():
    def builder(cfg):
        def transport(request):
            return LLMResponse(text=json.dumps({"category": "amount_mismatch", "confidence": "0.9"}))
        return LLMClient(transport)

    category, detail, confidence = llm_classifier.apply_llm_fallback(
        Config(openai_api_key="sk-test"), "BK-9", "no_candidate", "", {},
        "unidentified_counterpart", "bank-rule-7: no earlier condition matched",
        client_builder=builder,
    )

    assert category == "amount_mismatch"
    assert confidence == Decimal("0.9")
    assert llm_classifier.LLM_TAG in detail


def test_apply_llm_fallback_skips_when_openai_disabled():
    def exploding_builder(cfg):
        raise AssertionError("must not build a client when openai is disabled")

    category, detail, confidence = llm_classifier.apply_llm_fallback(
        Config(openai_api_key=None), "BK-9", "no_candidate", "", {},
        "unidentified_counterpart", "bank-rule-7: no earlier condition matched",
        client_builder=exploding_builder,
    )

    assert (category, detail, confidence) == (
        "unidentified_counterpart", "bank-rule-7: no earlier condition matched", None,
    )


def test_apply_llm_fallback_falls_back_when_llm_response_is_unusable():
    def builder(cfg):
        def transport(request):
            return LLMResponse(text="garbage, not json")
        return LLMClient(transport)

    category, detail, confidence = llm_classifier.apply_llm_fallback(
        Config(openai_api_key="sk-test"), "BK-9", "no_candidate", "", {},
        "unidentified_counterpart", "bank-rule-7: no earlier condition matched",
        client_builder=builder,
    )

    assert (category, detail, confidence) == (
        "unidentified_counterpart", "bank-rule-7: no earlier condition matched", None,
    )


def test_apply_llm_fallback_falls_back_when_client_builder_returns_none():
    category, detail, confidence = llm_classifier.apply_llm_fallback(
        Config(openai_api_key="sk-test"), "BK-9", "no_candidate", "", {},
        "unidentified_counterpart", "bank-rule-7: no earlier condition matched",
        client_builder=lambda cfg: None,
    )

    assert (category, detail, confidence) == (
        "unidentified_counterpart", "bank-rule-7: no earlier condition matched", None,
    )


# ---------------------------------------------------------------------------
# 2. Regression -- OPENAI_API_KEY absent -> routing output byte-identical to v1.
# ---------------------------------------------------------------------------

def test_regression_openai_disabled_matches_classify_bank_directly(tmp_path, monkeypatch):
    # Force the "no key configured" state explicitly -- this repo's own
    # .env may carry a real OPENAI_API_KEY (W8), so the regression must not
    # rely on the ambient environment happening to be key-less.
    disabled_config = Config(openai_api_key=None)
    assert not disabled_config.openai_enabled()
    monkeypatch.setattr(engine, "config", disabled_config)

    ledger_path, bank_path, outcomes_path, settlements_path = _build_batch(tmp_path)
    exception_rows, _ = _run_routing(ledger_path, bank_path, outcomes_path, settlements_path, tmp_path / "routed")
    by_bank = {r["bank_txn_id"]: r for r in exception_rows}

    for outcome in (
        outcome_row("BK-1", "anomalous_amount"),
        outcome_row("BK-2", "ambiguous_evidence", relation="duplicate"),
        outcome_row("BK-3", "ledger_already_settled"),
        outcome_row("BK-4", "ambiguous_evidence", features={"amount": "conflict", "name": "1.0"}),
        outcome_row("BK-9", "no_candidate"),
    ):
        bank_txn_id = outcome["bank_txn_id"]
        expected_category, expected_detail = classify.classify_bank(
            outcome["reason"], outcome["relation"], json.loads(outcome["features"]))
        actual = by_bank[bank_txn_id]
        assert actual["category"] == expected_category
        assert actual["classification_detail"] == expected_detail
        assert "llm-fallback" not in actual["classification_detail"]
        assert "llm_classified" not in json.loads(actual["evidence"])


# ---------------------------------------------------------------------------
# 3. Rules 1-6 are never intercepted or altered -- only rule-7 reaches the LLM.
# ---------------------------------------------------------------------------

def test_only_rule7_rows_reach_the_llm_fallback(tmp_path, monkeypatch):
    ledger_path, bank_path, outcomes_path, settlements_path = _build_batch(tmp_path)

    calls = []
    monkeypatch.setattr(llm_classifier, "get_client", _mock_llm_client_builder(calls))
    monkeypatch.setattr(engine, "config", Config(openai_api_key="sk-test"))

    exception_rows, _ = _run_routing(ledger_path, bank_path, outcomes_path, settlements_path, tmp_path / "routed")
    by_bank = {r["bank_txn_id"]: r for r in exception_rows}

    assert len(calls) == 1, "only the rule-7 row (BK-9) should ever reach the LLM"
    assert "BK-9" in calls[0].prompt

    assert by_bank["BK-1"]["category"] == "suspect_posting"
    assert "bank-rule-1" in by_bank["BK-1"]["classification_detail"]
    assert by_bank["BK-2"]["category"] == "duplicate"
    assert "bank-rule-2" in by_bank["BK-2"]["classification_detail"]
    assert by_bank["BK-3"]["category"] == "amount_mismatch"
    assert "bank-rule-3" in by_bank["BK-3"]["classification_detail"]
    assert by_bank["BK-4"]["category"] == "amount_mismatch"
    assert "bank-rule-4" in by_bank["BK-4"]["classification_detail"]
    for bank_txn_id in ("BK-1", "BK-2", "BK-3", "BK-4"):
        assert "llm-fallback" not in by_bank[bank_txn_id]["classification_detail"]

    # BK-9's rule-7 result WAS overridden by the (mocked) LLM.
    assert by_bank["BK-9"]["category"] == "amount_mismatch"
    assert "llm-fallback" in by_bank["BK-9"]["classification_detail"]


# ---------------------------------------------------------------------------
# 4. Every LLM-classified row is tagged/auditable in exceptions.csv.
# ---------------------------------------------------------------------------

def test_llm_classified_row_is_tagged_and_auditable_in_exceptions_csv(tmp_path, monkeypatch):
    ledger_path, bank_path, outcomes_path, settlements_path = _build_batch(tmp_path)

    calls = []
    monkeypatch.setattr(llm_classifier, "get_client", _mock_llm_client_builder(calls, confidence="0.60"))
    monkeypatch.setattr(engine, "config", Config(openai_api_key="sk-test"))

    exception_rows, _ = _run_routing(ledger_path, bank_path, outcomes_path, settlements_path, tmp_path / "routed")
    by_bank = {r["bank_txn_id"]: r for r in exception_rows}

    tagged = by_bank["BK-9"]
    assert "llm-fallback" in tagged["classification_detail"]
    evidence = json.loads(tagged["evidence"])
    assert evidence["llm_classified"] is True
    assert evidence["llm_confidence"] == "0.60"

    for bank_txn_id in ("BK-1", "BK-2", "BK-3", "BK-4"):
        untagged_evidence = json.loads(by_bank[bank_txn_id]["evidence"])
        assert "llm_classified" not in untagged_evidence
        assert "llm-fallback" not in by_bank[bank_txn_id]["classification_detail"]


# ---------------------------------------------------------------------------
# 5. The guardrail's independent re-check is completely unaffected.
# ---------------------------------------------------------------------------

def test_guardrail_source_never_references_routings_exceptions_output():
    import ledger_sense.guardrail as guardrail_pkg
    package_dir = Path(guardrail_pkg.__file__).parent
    for file in package_dir.rglob("*.py"):
        text = file.read_text()
        assert "exceptions.csv" not in text
        assert "classification_detail" not in text
        assert "ledger_sense.routing" not in text
        assert "llm_classified" not in text


def test_guardrail_verdict_is_independent_of_llm_classification(tmp_path, monkeypatch):
    ledger_path, bank_path, outcomes_path, settlements_path = _build_batch(tmp_path)

    # Baseline: no OpenAI key, rule 7 stands as unidentified_counterpart.
    _run_routing(ledger_path, bank_path, outcomes_path, settlements_path, tmp_path / "routed_v1")
    guard_v1 = tmp_path / "guard_v1"
    guard_v1.mkdir()
    guardrail_run(ledger_path, bank_path, outcomes_path, settlements_path, AS_OF_DT, guard_v1)

    # LLM enabled + mocked, deliberately relabels BK-9 to a DIFFERENT category.
    calls = []
    monkeypatch.setattr(llm_classifier, "get_client", _mock_llm_client_builder(calls, category="duplicate"))
    monkeypatch.setattr(engine, "config", Config(openai_api_key="sk-test"))

    exception_rows_v2, _ = _run_routing(
        ledger_path, bank_path, outcomes_path, settlements_path, tmp_path / "routed_v2")
    by_bank_v2 = {r["bank_txn_id"]: r for r in exception_rows_v2}
    assert by_bank_v2["BK-9"]["category"] == "duplicate", "sanity: the LLM really did relabel this row"

    guard_v2 = tmp_path / "guard_v2"
    guard_v2.mkdir()
    guardrail_run(ledger_path, bank_path, outcomes_path, settlements_path, AS_OF_DT, guard_v2)

    # Guardrail never reads exceptions.csv (it isn't even one of its four
    # input files) -- its output is byte-identical whether or not the LLM fired.
    assert (guard_v1 / "release_decisions.csv").read_text() == (guard_v2 / "release_decisions.csv").read_text()
    assert (guard_v1 / "guardrail_audit.csv").read_text() == (guard_v2 / "guardrail_audit.csv").read_text()
    assert (guard_v1 / "held_settlements.csv").read_text() == (guard_v2 / "held_settlements.csv").read_text()

    # And BK-9 got a real, non-trivial guardrail verdict, so the check above
    # actually proves something rather than comparing two "allow"s.
    decisions = rows(guard_v1 / "release_decisions.csv")
    bk9_decision = next(r for r in decisions if r["bank_txn_id"] == "BK-9")
    assert bk9_decision["verdict"] == "hold"
    assert bk9_decision["primary_rule"] == "dual_control"
