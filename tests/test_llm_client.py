"""W8: llm_client.py — retry/timeout/cost-cap/caching, mocked transport only.

L20: every test here plugs in a fake transport callable. No test in this
file may import `openai`, `httpx`, or `neatlogs`, or open a socket — the
whole point of the injectable-transport design is that this suite proves the
wrapper's behavior without ever touching a real provider.
"""

import time

import pytest

from ledger_sense.llm_client import (
    CallCapExceeded,
    CostCapExceeded,
    LLMClient,
    LLMRequest,
    LLMResponse,
    RequestTimedOut,
    TransportError,
    redact,
)


def fake_sleep_recorder():
    """Returns (sleep_fn, calls) so backoff delays are asserted, never waited on."""
    calls = []

    def _sleep(seconds):
        calls.append(seconds)

    return _sleep, calls


def make_client(transport, **kwargs):
    sleep, sleep_calls = fake_sleep_recorder()
    kwargs.setdefault("sleep", sleep)
    client = LLMClient(transport, **kwargs)
    client._test_sleep_calls = sleep_calls  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_successful_call_returns_response_and_tracks_cost():
    def transport(request: LLMRequest) -> LLMResponse:
        assert request.prompt == "hello"
        return LLMResponse(text="world", cost_usd=0.02, tokens=10)

    client = make_client(transport, cost_cap_usd=1.00)
    response = client.complete("hello", estimated_cost_usd=0.02)

    assert response.text == "world"
    assert client.cumulative_cost_usd == pytest.approx(0.02)
    assert client.calls_made == 1


def test_request_carries_model_and_timeout():
    seen = {}

    def transport(request: LLMRequest) -> LLMResponse:
        seen["model"] = request.model
        seen["timeout"] = request.timeout
        return LLMResponse(text="ok")

    client = make_client(transport, model="gpt-4o-mini", timeout_seconds=5.0)
    client.complete("hi")
    assert seen == {"model": "gpt-4o-mini", "timeout": 5.0}


# ---------------------------------------------------------------------------
# Bounded retries with backoff
# ---------------------------------------------------------------------------

def test_retries_on_transport_error_then_succeeds():
    attempts = {"count": 0}

    def transport(request: LLMRequest) -> LLMResponse:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TransportError("transient failure")
        return LLMResponse(text="recovered")

    client = make_client(transport, max_retries=3)
    response = client.complete("hi")

    assert response.text == "recovered"
    assert attempts["count"] == 3
    # Two failures -> two backoff sleeps, exponentially increasing.
    assert len(client._test_sleep_calls) == 2
    assert client._test_sleep_calls[1] > client._test_sleep_calls[0]


def test_retries_are_bounded_then_raises():
    attempts = {"count": 0}

    def transport(request: LLMRequest) -> LLMResponse:
        attempts["count"] += 1
        raise TransportError("always fails")

    client = make_client(transport, max_retries=2)
    with pytest.raises(TransportError):
        client.complete("hi")

    # Initial attempt + 2 retries = 3 total calls, no more.
    assert attempts["count"] == 3


def test_zero_retries_means_single_attempt():
    attempts = {"count": 0}

    def transport(request: LLMRequest) -> LLMResponse:
        attempts["count"] += 1
        raise TransportError("fails")

    client = make_client(transport, max_retries=0)
    with pytest.raises(TransportError):
        client.complete("hi")
    assert attempts["count"] == 1


def test_non_transport_exception_is_not_retried():
    attempts = {"count": 0}

    def transport(request: LLMRequest) -> LLMResponse:
        attempts["count"] += 1
        raise ValueError("a real bug, not a transient failure")

    client = make_client(transport, max_retries=3)
    with pytest.raises(ValueError):
        client.complete("hi")
    assert attempts["count"] == 1


# ---------------------------------------------------------------------------
# Request timeout
# ---------------------------------------------------------------------------

def test_slow_transport_raises_timeout():
    def transport(request: LLMRequest) -> LLMResponse:
        time.sleep(0.5)
        return LLMResponse(text="too late")

    client = make_client(transport, max_retries=0, timeout_seconds=0.05)
    with pytest.raises(RequestTimedOut):
        client.complete("hi")


def test_timeout_is_retried_within_bound():
    attempts = {"count": 0}

    def transport(request: LLMRequest) -> LLMResponse:
        attempts["count"] += 1
        if attempts["count"] < 2:
            time.sleep(0.5)
        return LLMResponse(text="finally")

    client = make_client(transport, max_retries=2, timeout_seconds=0.05)
    response = client.complete("hi")
    assert response.text == "finally"
    assert attempts["count"] == 2


def test_per_call_timeout_override():
    def transport(request: LLMRequest) -> LLMResponse:
        assert request.timeout == 9.0
        return LLMResponse(text="ok")

    client = make_client(transport, timeout_seconds=30.0)
    client.complete("hi", timeout_seconds=9.0)


# ---------------------------------------------------------------------------
# Cost cap: short-circuits BEFORE the transport is ever called
# ---------------------------------------------------------------------------

def test_cost_cap_blocks_call_that_would_exceed_it():
    calls = []

    def transport(request: LLMRequest) -> LLMResponse:
        calls.append(request)
        return LLMResponse(text="ok", cost_usd=0.60)

    client = make_client(transport, cost_cap_usd=1.00)
    client.complete("first", estimated_cost_usd=0.60)
    assert client.cumulative_cost_usd == pytest.approx(0.60)

    with pytest.raises(CostCapExceeded):
        client.complete("second", estimated_cost_usd=0.60)

    # Only the first call actually reached the transport.
    assert len(calls) == 1
    assert client.cumulative_cost_usd == pytest.approx(0.60)


def test_cost_cap_allows_call_exactly_at_the_cap():
    def transport(request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="ok", cost_usd=1.00)

    client = make_client(transport, cost_cap_usd=1.00)
    response = client.complete("hi", estimated_cost_usd=1.00)
    assert response.text == "ok"


def test_cost_cap_uses_actual_cost_when_reported():
    def transport(request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="ok", cost_usd=0.10)

    client = make_client(transport, cost_cap_usd=1.00)
    client.complete("hi", estimated_cost_usd=0.90)  # conservative estimate
    # Actual cost (0.10) is what gets tallied, not the estimate (0.90).
    assert client.cumulative_cost_usd == pytest.approx(0.10)


def test_remaining_budget_reflects_spend():
    def transport(request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="ok", cost_usd=0.25)

    client = make_client(transport, cost_cap_usd=1.00)
    client.complete("hi", estimated_cost_usd=0.25)
    assert client.remaining_budget_usd() == pytest.approx(0.75)


def test_zero_cost_cap_blocks_any_paid_call():
    def transport(request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="ok", cost_usd=0.01)

    client = make_client(transport, cost_cap_usd=0.0)
    with pytest.raises(CostCapExceeded):
        client.complete("hi", estimated_cost_usd=0.01)


# ---------------------------------------------------------------------------
# Call-count cap (optional, also short-circuits before dispatch)
# ---------------------------------------------------------------------------

def test_max_calls_blocks_further_dispatch():
    calls = []

    def transport(request: LLMRequest) -> LLMResponse:
        calls.append(request)
        return LLMResponse(text="ok")

    client = make_client(transport, max_calls=1)
    client.complete("first")
    with pytest.raises(CallCapExceeded):
        client.complete("second")
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Response caching keyed by caller-supplied key
# ---------------------------------------------------------------------------

def test_cache_hit_skips_transport_and_cost():
    calls = []

    def transport(request: LLMRequest) -> LLMResponse:
        calls.append(request)
        return LLMResponse(text="ok", cost_usd=0.10)

    client = make_client(transport, cost_cap_usd=1.00)
    first = client.complete("hi", cache_key="LG-1:BANK-1", estimated_cost_usd=0.10)
    second = client.complete("hi", cache_key="LG-1:BANK-1", estimated_cost_usd=0.10)

    assert first == second
    assert len(calls) == 1
    assert client.cumulative_cost_usd == pytest.approx(0.10)
    assert client.cache_hits == 1


def test_different_cache_keys_both_dispatch():
    calls = []

    def transport(request: LLMRequest) -> LLMResponse:
        calls.append(request)
        return LLMResponse(text="ok")

    client = make_client(transport)
    client.complete("hi", cache_key="a")
    client.complete("hi", cache_key="b")
    assert len(calls) == 2


def test_no_cache_key_never_caches():
    calls = []

    def transport(request: LLMRequest) -> LLMResponse:
        calls.append(request)
        return LLMResponse(text="ok")

    client = make_client(transport)
    client.complete("hi")
    client.complete("hi")
    assert len(calls) == 2


def test_cache_hit_bypasses_exhausted_cost_cap():
    def transport(request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="ok", cost_usd=1.00)

    client = make_client(transport, cost_cap_usd=1.00)
    client.complete("hi", cache_key="k", estimated_cost_usd=1.00)
    # Cap is now exhausted; a cached lookup must still succeed without
    # touching the transport or the cap check.
    response = client.complete("hi", cache_key="k", estimated_cost_usd=1.00)
    assert response.text == "ok"


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------

def test_negative_cost_cap_rejected():
    with pytest.raises(ValueError):
        LLMClient(lambda request: LLMResponse(text="x"), cost_cap_usd=-1.0)


def test_negative_max_retries_rejected():
    with pytest.raises(ValueError):
        LLMClient(lambda request: LLMResponse(text="x"), max_retries=-1)


# ---------------------------------------------------------------------------
# redact(): credential-shaped strings never survive into a log line (L19)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
        "OPENAI_API_KEY=sk-abcdefghijklmnop1234",
        "DODO_API_KEY=dodo_live_abcdef123456",
        'api_key: "abcdef1234567890"',
        "Authorization: Bearer abcdef1234567890xyz",
        "NEATLOGS_API_KEY='neatlogs-secret-value-123'",
    ],
)
def test_redact_strips_credential_shaped_values(raw):
    redacted = redact(raw)
    assert "abcdef" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_preserves_surrounding_context():
    message = "request to model gpt-4o-mini failed, OPENAI_API_KEY=sk-abcdefghijklmnop set"
    redacted = redact(message)
    assert "request to model gpt-4o-mini failed" in redacted
    assert "OPENAI_API_KEY=" in redacted
    assert "sk-abcdefghijklmnop" not in redacted


def test_redact_leaves_ordinary_text_untouched():
    message = "matched LG-1 to BANK-42 with confidence 0.93"
    assert redact(message) == message


def test_redact_handles_empty_string():
    assert redact("") == ""


def test_redact_is_idempotent():
    once = redact("OPENAI_API_KEY=sk-abcdefghijklmnop1234")
    twice = redact(once)
    assert once == twice
