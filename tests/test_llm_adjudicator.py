"""W9: OpenAIAdjudicator — mocked-transport only.

L20: every test here plugs in a fake transport callable, exactly like
test_llm_client.py. No test in this file may import `openai`, `httpx`, or
open a socket, and none may rely on a real OPENAI_API_KEY. The ambient dev
environment may hold a *real* key (see config.py/W8) -- that is exactly why
every test here explicitly builds its own Config/adjudicator instead of
touching the module-level `ledger_sense.config.config` singleton.
"""

import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from ledger_sense.config import Config, load_config
from ledger_sense.data.models import BankTransaction, LedgerEntry
from ledger_sense.llm_client import LLMRequest, LLMResponse, TransportError
from ledger_sense.matching import match
from ledger_sense.matching.adjudication import Question, StubAdjudicator
from ledger_sense.matching.scoring import score_candidate


def ledger(id="LG-1", amount="100.00", reference="INV-1000001", name="Acme Logistics"):
    return LedgerEntry(id, "2026-01-10T00:00:00Z", Decimal(amount), "USD",
                       "invoice_payment", "CP-1", name, reference, "", "1200", "billing")


def bank(id="BK-1", amount="100.00", reference="INV-1000001", name="Acme Logistics"):
    return BankTransaction(id, "2026-01-10T00:00:00Z", Decimal(amount), "USD",
                           name, reference, "", "ACCT-USD-01", "STMT-1", "credit")


def make_question(bank_id="BK-1", ledger_id="LG-1", reason="ambiguous_evidence"):
    b, e = bank(bank_id), ledger(ledger_id)
    candidate = score_candidate(b, e)
    return Question(b, (candidate,), reason)


def question_for(e, b, reason="ambiguous_evidence"):
    return Question(b, (score_candidate(b, e),), reason)


def rescue_pair(bank_id="BK-1", ledger_id="LG-1"):
    """Exact name/amount/date but a reference that quotes someone else's invoice.

    ref=0 (present, non-matching) keeps score at 60 -- below the cheap tier's
    88 gate, so it escalates -- while the stub's own evidence formula ignores
    the reference entirely and lands at 100, clearing its 88 floor. This is
    exactly the "may rescue a wrong reference" case `adjudication.py`'s
    docstring describes.
    """
    e = ledger(ledger_id, name="Acme Logistics")
    b = bank(bank_id, name="Acme Logistics", reference="ZZZZZZZ-NOPE")
    return e, b


def fake_config(**overrides):
    return replace(Config(openai_api_key="sk-test-not-real", openai_model="gpt-4o-mini",
                          llm_cost_cap_usd=1.00), **overrides)


def json_transport(payload_by_prompt=None, default=None, calls=None, error_after=None, cost_usd=0.01):
    """A fake transport that returns canned JSON, recording every request it sees."""
    calls = calls if calls is not None else []

    def transport(request: LLMRequest) -> LLMResponse:
        calls.append(request)
        if error_after is not None and len(calls) > error_after:
            raise TransportError("simulated transport failure")
        text = default
        if payload_by_prompt is not None:
            for needle, value in payload_by_prompt.items():
                if needle in request.prompt:
                    text = value
                    break
        return LLMResponse(text=text, cost_usd=cost_usd, tokens=42)

    transport.calls = calls
    return transport


# ---------------------------------------------------------------------------
# Acceptance 1: mocked client, protocol shape -- zero live network calls
# ---------------------------------------------------------------------------

def test_openai_adjudicator_satisfies_protocol_shape():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    transport = json_transport(default='{"decision": "no_match", "confidence": 0.1, "rationale": "n/a"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)

    assert adjudicator.llm_is_stub is False
    assert adjudicator.llm_calls == 0
    assert adjudicator.model == "gpt-4o-mini"


def test_mocked_match_response_yields_accepting_verdict_for_top_candidate():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    transport = json_transport(default='{"decision": "match", "confidence": 0.93, "rationale": "names line up"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)
    question = make_question()

    verdicts = adjudicator.adjudicate((question,))

    assert len(transport.calls) == 1
    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.bank_txn_id == "BK-1"
    assert verdict.ledger_id == "LG-1"
    assert verdict.accept is True
    assert verdict.confidence == Decimal("0.93")
    assert adjudicator.llm_calls == 1


def test_mocked_no_match_response_is_not_accepted():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    transport = json_transport(default='{"decision": "no_match", "confidence": 0.2, "rationale": "different payer"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)

    verdicts = adjudicator.adjudicate((make_question(),))

    assert len(verdicts) == 1
    assert verdicts[0].accept is False


def test_mocked_needs_human_response_is_not_accepted_and_does_not_crash():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    transport = json_transport(default='{"decision": "needs_human", "confidence": 0.5, "rationale": "unclear"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)

    verdicts = adjudicator.adjudicate((make_question(),))

    assert len(verdicts) == 1
    assert verdicts[0].accept is False


def test_request_is_temperature_zero_with_a_timeout():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    transport = json_transport(default='{"decision": "match", "confidence": 0.9, "rationale": "ok"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)

    adjudicator.adjudicate((make_question(),))

    request = transport.calls[0]
    assert request.metadata.get("temperature") == 0
    assert request.timeout > 0


# ---------------------------------------------------------------------------
# Acceptance 2: OPENAI_API_KEY unset -> byte-identical to v1's StubAdjudicator
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).parent / "fixtures" / "mini_pass1"


def key_absent_config(monkeypatch) -> Config:
    """A Config with no OPENAI_API_KEY, regardless of the ambient shell's env.

    `load_config` reads the *real* process environment before any `.env` file
    (see config.py), so a merely-nonexistent dotenv path is not enough on its
    own -- a worker's real shell may have a live key exported (L20 must hold
    no matter what the ambient environment looks like).
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return load_config(dotenv_path=Path("/nonexistent/.env"))


def test_get_adjudicator_is_stub_when_key_absent(monkeypatch):
    from ledger_sense.matching.llm_adjudicator import get_adjudicator

    cfg = key_absent_config(monkeypatch)
    assert cfg.openai_enabled() is False

    adjudicator = get_adjudicator(cfg)

    assert isinstance(adjudicator, StubAdjudicator)
    assert adjudicator.llm_is_stub is True
    assert adjudicator.llm_calls == 0


def test_get_adjudicator_output_byte_identical_to_stub_when_key_absent(tmp_path, monkeypatch):
    from ledger_sense.matching.io import run
    from ledger_sense.matching.llm_adjudicator import get_adjudicator

    cfg = key_absent_config(monkeypatch)

    run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", tmp_path / "stub", StubAdjudicator())
    run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", tmp_path / "factory", get_adjudicator(cfg))

    for name in ("match_outcomes.csv", "ledger_settlements.csv"):
        assert (tmp_path / "stub" / name).read_bytes() == (tmp_path / "factory" / name).read_bytes()


# ---------------------------------------------------------------------------
# Acceptance 3: mocked "always match" resolves a gray-zone candidate the stub
# would have escalated; llm_calls/llm_is_stub=False flow into match_outcomes.csv
# ---------------------------------------------------------------------------

def gray_zone_pair():
    """Exact amount/date/currency but a weak name match (0.50 < stub's 0.90 floor).

    Shares its blocking key (squashed name's first 4 chars, "ACME") with the
    ledger entry, so it actually reaches scoring instead of being dropped by
    the block -- score=50 lands in the escalated band (>=45, <88, no interlock
    veto) and the stub's own gate (name >= 0.90) refuses it, pinned by
    test_stub_escalates_the_gray_zone_pair below so a future scoring change
    can't silently invalidate this fixture.
    """
    e = ledger(name="Acme Logistics")
    b = bank(name="Acme Wexley Group", reference="NO-MATCH-REF")
    return e, b


def test_stub_escalates_the_gray_zone_pair():
    e, b = gray_zone_pair()
    result = match([e], [b], StubAdjudicator())
    row = result.outcomes[0]
    assert row["status"] == "escalated"
    assert result.llm_calls == 0


def test_mocked_always_match_resolves_what_the_stub_escalated():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    e, b = gray_zone_pair()
    transport = json_transport(default='{"decision": "match", "confidence": 0.87, "rationale": "same invoice, alias payer"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)

    result = match([e], [b], adjudicator)

    row = result.outcomes[0]
    assert row["status"] == "matched"
    assert row["tier"] == "llm"
    assert row["ledger_id"] == e.ledger_id
    assert row["llm_model"] == "gpt-4o-mini"
    assert row["llm_confidence"] == "0.87"
    assert row["llm_is_stub"] is False
    assert result.llm_calls == 1
    assert result.llm_is_stub is False


# ---------------------------------------------------------------------------
# Acceptance 4: cost cap hit mid-batch -> remaining candidates fall back to
# stub behavior, no crash
# ---------------------------------------------------------------------------

def test_stub_would_accept_the_rescue_pair():
    """Pins rescue_pair()'s fixture: engine escalates it, StubAdjudicator accepts it."""
    e, b = rescue_pair()
    result = match([e], [b], StubAdjudicator())
    row = result.outcomes[0]
    assert row["status"] == "matched"
    # It only settles via the adjudicator (stub) seam, not the cheap tier gate.
    assert row["tier"] == "llm"
    assert row["reason"] == "stub_amount_name_agreement"


def test_cost_cap_breach_mid_batch_falls_back_to_stub_without_crashing():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    e1, b1 = rescue_pair("BK-1", "LG-1")          # resolved by the (first, affordable) LLM call
    e2, b2 = rescue_pair("BK-2", "LG-2")          # cap breached here -> stub fallback -> accepted
    e3, b3 = gray_zone_pair()                      # never even attempted -> stub fallback -> no verdict
    b3 = replace(b3, bank_txn_id="BK-3")
    e3 = replace(e3, ledger_id="LG-3")
    questions = (question_for(e1, b1), question_for(e2, b2), question_for(e3, b3))

    cfg = fake_config(llm_cost_cap_usd=0.05)
    transport = json_transport(
        default='{"decision": "match", "confidence": 0.9, "rationale": "ok"}', cost_usd=0.05)
    adjudicator = OpenAIAdjudicator(cfg, transport=transport)

    verdicts = adjudicator.adjudicate(questions)

    # Only the first call was ever dispatched -- the second breaches the cap
    # pre-flight (never reaching the transport) and the third is never tried.
    assert len(transport.calls) == 1
    assert adjudicator.llm_calls == 1

    by_id = {v.bank_txn_id: v for v in verdicts}
    assert by_id["BK-1"].accept is True
    assert by_id["BK-2"].accept is True
    assert by_id["BK-2"].reason == "stub_amount_name_agreement"
    assert "BK-3" not in by_id


# ---------------------------------------------------------------------------
# Acceptance 5: same (ledger_id, bank_txn_id) adjudicated twice in one run ->
# the mocked transport is called exactly once
# ---------------------------------------------------------------------------

def test_same_pair_adjudicated_twice_calls_transport_once():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    transport = json_transport(default='{"decision": "match", "confidence": 0.9, "rationale": "ok"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)
    question = make_question(bank_id="BK-1", ledger_id="LG-1")

    first = adjudicator.adjudicate((question,))
    second = adjudicator.adjudicate((question,))

    assert len(transport.calls) == 1
    assert adjudicator.llm_calls == 1
    assert first == second


def test_cache_is_keyed_by_ledger_and_bank_pair_not_just_bank_id():
    from ledger_sense.matching.llm_adjudicator import OpenAIAdjudicator

    transport = json_transport(default='{"decision": "match", "confidence": 0.9, "rationale": "ok"}')
    adjudicator = OpenAIAdjudicator(fake_config(), transport=transport)

    adjudicator.adjudicate((make_question(bank_id="BK-1", ledger_id="LG-1"),))
    adjudicator.adjudicate((make_question(bank_id="BK-1", ledger_id="LG-2"),))

    assert len(transport.calls) == 2
    assert adjudicator.llm_calls == 2


# ---------------------------------------------------------------------------
# __main__.py's one call-site swap: `--adjudicator auto` uses get_adjudicator()
# ---------------------------------------------------------------------------

def test_cli_auto_adjudicator_matches_stub_when_key_absent(tmp_path):
    """L20: even the new `auto` CLI path never calls out when no key is configured."""
    fixture = Path(__file__).parent / "fixtures" / "mini_pass1"
    env = {**os.environ}
    for key in ("OPENAI_API_KEY", "DODO_API_KEY", "NEATLOGS_API_KEY"):
        env.pop(key, None)
    completed = subprocess.run([
        sys.executable, "-m", "ledger_sense.matching", "--ledger", str(fixture / "ledger.csv"),
        "--bank", str(fixture / "bank.csv"), "--out-dir", str(tmp_path), "--adjudicator", "auto",
    ], env=env, check=True, capture_output=True, text=True)
    assert "llm_calls=0" in completed.stdout
    assert "llm_is_stub=True" in completed.stdout
