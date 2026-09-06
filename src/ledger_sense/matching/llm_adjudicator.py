"""OpenAIAdjudicator: the real gray-zone adjudicator (spec: LEDGER-SENSE-v2-PRD.md, W9).

Implements `matching/adjudication.py`'s `Adjudicator` Protocol exactly, and operates
only on the `Question`/`Verdict` dataclasses that module already defines -- no new
candidate-generation logic, no widening of the gray-zone seam (L21). It judges only
the single top-ranked candidate a `Question` already carries; it never picks among
the offered candidates itself, since that would be new judgment authority v1's
deterministic scorer already owns.

Every call goes through the shared `llm_client.py` wrapper (bounded retries,
timeout, per-run cost cap, response cache keyed by `(ledger_id, bank_txn_id)`) --
this module never talks to a transport directly, and never imports the real
`openai` SDK except lazily, inside the default transport, so the base install and
every test stay dependency-free and fully offline (L20).

On any transport failure (retries exhausted, timeout, cost-cap breach) or a
malformed/invalid response, this falls back to `StubAdjudicator`'s decision for the
affected question(s) -- never crashes, never blocks the batch (L18/L22).
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Sequence

from ledger_sense.config import Config
from ledger_sense.config import config as _default_config
from ledger_sense.llm_client import LLMClient, LLMClientError, LLMRequest, LLMResponse, Transport

from .adjudication import Adjudicator, Question, StubAdjudicator, Verdict

_VALID_DECISIONS = {"match", "no_match", "needs_human"}

# A conservative flat per-call estimate (gpt-4o-mini-class, short structured prompt),
# expressed in cents like the rest of this codebase's money handling (no float
# literals in matching/, per test_matching_isolation.py). It only gates the
# pre-flight cost-cap check; the actual cost, once known, is what gets added to
# the running total (see llm_client.LLMClient.complete).
_ESTIMATED_COST_PER_CALL_CENTS = 1  # $0.01

# Per-call timeout budget, in whole seconds; bounded retries/backoff live in
# llm_client.py itself.
_TIMEOUT_SECONDS = 30


def _make_default_transport(api_key: str | None) -> Transport:
    """Build the real OpenAI transport, closing over the key rather than
    threading it through `LLMRequest.metadata` -- metadata is general-purpose
    context a future caller might reasonably log, and a secret should never
    depend on nobody doing that (L19).

    `openai` is imported lazily, inside the closure -- the base install needs
    no `openai` dependency, and no test in this repo ever exercises this
    function at all (L20); tests always inject a fake transport directly.
    """

    def _transport(request: LLMRequest) -> LLMResponse:
        import openai  # noqa: PLC0415 -- deliberately lazy, see docstring above

        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=request.model,
            temperature=request.metadata.get("temperature", 0),
            timeout=request.timeout,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": request.prompt}],
        )
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "total_tokens", 0) if usage else 0
        return LLMResponse(text=text, tokens=tokens, raw=response)

    return _transport


def _prompt_for(question: Question, candidate) -> str:
    payload = {
        "task": "Decide whether this bank transaction and ledger entry are the same "
                "underlying payment. Respond with a single JSON object only: "
                '{"decision": "match|no_match|needs_human", "confidence": 0..1, '
                '"rationale": "short reason"}.',
        "reason_escalated": question.reason,
        "bank_transaction": question.bank.to_row(),
        "candidate_ledger_entry": candidate.ledger.to_row(),
        "candidate_features": candidate.features.to_dict(),
        "candidate_score": str(candidate.score),
    }
    return json.dumps(payload, sort_keys=True)


class OpenAIAdjudicator:
    """The real adjudicator: OpenAI, structured JSON, cached, cost-capped, bounded."""

    llm_is_stub = False

    def __init__(self, cfg: Config | None = None, transport: Transport | None = None):
        cfg = cfg if cfg is not None else _default_config
        self.model = cfg.openai_model
        self._client = LLMClient(
            transport if transport is not None else _make_default_transport(cfg.openai_api_key),
            model=cfg.openai_model,
            cost_cap_usd=cfg.llm_cost_cap_usd,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
        self._stub = StubAdjudicator()

    @property
    def llm_calls(self) -> int:
        # Cumulative provider calls actually dispatched, per the Protocol's contract
        # -- a cache hit or a pre-flight cap/call refusal never reaches the transport.
        return self._client.calls_made

    def adjudicate(self, questions: Sequence[Question]) -> Sequence[Verdict]:
        verdicts: list[Verdict] = []
        fallback: list[Question] = []
        capped = False
        for question in questions:
            if capped:
                fallback.append(question)
                continue
            try:
                verdict = self._adjudicate_one(question)
            except LLMClientError:
                # Retries exhausted, timed out, or the cost/call cap was hit
                # pre-flight -- further calls in this batch would fail the same
                # way, so stop dispatching and let the stub take the rest.
                capped = True
                fallback.append(question)
                continue
            except Exception:
                # A malformed/invalid response for this one question only --
                # other questions in the batch may still resolve normally.
                fallback.append(question)
                continue
            verdicts.append(verdict)
        if fallback:
            verdicts.extend(self._stub.adjudicate(fallback))
        return verdicts

    def _adjudicate_one(self, question: Question) -> Verdict:
        candidate = question.candidates[0]
        cache_key = (candidate.ledger.ledger_id, question.bank.bank_txn_id)
        response = self._client.complete(
            _prompt_for(question, candidate),
            estimated_cost_usd=_ESTIMATED_COST_PER_CALL_CENTS / 100,
            cache_key=cache_key,
            timeout_seconds=_TIMEOUT_SECONDS,
            metadata={"temperature": 0, "ledger_id": candidate.ledger.ledger_id,
                      "bank_txn_id": question.bank.bank_txn_id},
        )
        decision, confidence, rationale = _parse_response(response.text)
        return Verdict(question.bank.bank_txn_id, candidate.ledger.ledger_id,
                       decision == "match", confidence, rationale)


def _parse_response(text: str) -> tuple[str, Decimal, str]:
    """Parse and validate the structured JSON contract; raise on anything invalid.

    Raising here (rather than returning a best-effort guess) is deliberate: the
    caller treats any exception as "this response cannot be trusted" and falls
    back to the stub for that question instead of ever handing engine.py a
    verdict it can't validate.
    """
    data = json.loads(text)
    decision = data["decision"]
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"Unknown decision {decision!r}")
    try:
        confidence = Decimal(str(data["confidence"]))
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise ValueError("Invalid confidence") from exc
    if not confidence.is_finite() or not 0 <= confidence <= 1:
        raise ValueError("Confidence out of range")
    rationale = str(data.get("rationale", ""))
    return decision, confidence, rationale


def get_adjudicator(cfg: Config | None = None) -> Adjudicator:
    """The one config-driven factory call: real adjudicator iff a key is configured.

    L18: absent `OPENAI_API_KEY` -> `config.openai_enabled()` is False -> this
    returns the same `StubAdjudicator` v1 always used, byte-identical output.
    """
    cfg = cfg if cfg is not None else _default_config
    if cfg.openai_enabled():
        return OpenAIAdjudicator(cfg)
    return StubAdjudicator()
