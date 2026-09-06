"""W13: OpenAI routing fallback classifier (spec: LEDGER-SENSE-v2-PRD.md, W13).

Extends the consumption of ``classify.classify_bank``'s rule 7 only
(``unidentified_counterpart``, "no earlier condition matched") -- rules 1-6
are computed entirely inside ``classify_bank`` and this module is never
consulted for them (``apply_llm_fallback`` re-checks the rule-7 marker itself
before ever building a client, as defense in depth on top of the guard
``routing/engine.py`` applies at the call site).

Classifies into the exact same fixed 5-category taxonomy as
``classify.CATEGORIES`` with a confidence score -- ``classify_via_llm``
refuses (returns ``None``) any response naming a 6th category, so a 6th
category can never reach an exceptions.csv row (L21).

Every LLM-classified row is tagged/auditable: ``apply_llm_fallback`` returns
a ``classification_detail`` string carrying ``LLM_TAG`` plus the confidence,
and ``routing/engine.py`` also folds ``llm_classified``/``llm_confidence``
into the row's existing ``evidence`` JSON cell -- no new exceptions.csv
column is added.

Failing closed (L18/L21): an absent ``OPENAI_API_KEY`` means
``config.openai_enabled()`` is False and ``apply_llm_fallback`` returns its
inputs completely unchanged -- v1's rule-7 output is byte-identical. Even
with a key configured, any transport failure, cost-cap breach, or malformed/
invalid LLM response also degrades to the unchanged rule-7 result rather
than raising -- this module never blocks or crashes the routing pass.

This module never imports anything from ``ledger_sense.guardrail`` and the
guardrail never imports this module or reads exceptions.csv -- its
independent re-check is untouched (see ``tests/test_llm_classifier.py``'s
guardrail-independence tests).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from ledger_sense.llm_client import LLMClient, LLMClientError, LLMRequest, LLMResponse, TransportError

from .classify import CATEGORIES

# The exact substring classify.classify_bank's rule 7 puts in its detail
# string (see routing/classify.py) -- the only case this module ever acts on.
RULE7_MARKER = "bank-rule-7:"

# Prefix folded into classification_detail for every row the LLM actually
# reclassified, so a human (or a later grep) can find every LLM-touched row.
LLM_TAG = "llm-fallback"


@dataclass(frozen=True)
class LLMClassification:
    """A validated LLM response -- always one of the 5 fixed categories."""

    category: str
    confidence: Decimal


def _build_prompt(bank_txn_id: str, reason: str, relation: str, features: dict) -> str:
    payload = {
        "bank_txn_id": bank_txn_id,
        "reason": reason,
        "relation": relation,
        "features": features,
        "allowed_categories": list(CATEGORIES),
    }
    return (
        "Classify this unresolved bank reconciliation exception into exactly "
        "one of allowed_categories. Respond with JSON only, no other text: "
        '{"category": "<one of allowed_categories>", "confidence": <number 0-1>}.\n'
        + json.dumps(payload, sort_keys=True)
    )


def _parse_response(text: str) -> Optional[LLMClassification]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    category = data.get("category")
    if category not in CATEGORIES:
        return None

    try:
        confidence = Decimal(str(data.get("confidence", "0")))
    except (InvalidOperation, TypeError):
        return None
    if confidence < 0 or confidence > 1:
        return None

    return LLMClassification(category=category, confidence=confidence)


def classify_via_llm(
    client: LLMClient, bank_txn_id: str, reason: str, relation: str, features: dict,
) -> Optional[LLMClassification]:
    """Ask ``client`` to classify one rule-7 row. Returns ``None`` on ANY
    failure (transport error, timeout, cost/call-cap breach, malformed JSON,
    invalid category, out-of-range confidence) -- never raises, so a caller
    can always fall back to the unchanged rule-7 result (L21)."""
    try:
        response: LLMResponse = client.complete(
            _build_prompt(bank_txn_id, reason, relation, features),
            cache_key=("routing-fallback", bank_txn_id),
            metadata={"bank_txn_id": bank_txn_id, "seam": "routing-fallback"},
        )
    except LLMClientError:
        return None
    return _parse_response(response.text)


def get_client(cfg) -> Optional[LLMClient]:
    """Build a real ``LLMClient`` wired to the OpenAI SDK, or ``None`` when
    disabled. ``openai`` is imported lazily, inside the transport closure, so
    importing this module -- or calling it with a disabled config -- never
    requires the package or touches the network (L18/L20)."""
    if not cfg.openai_enabled():
        return None

    def _openai_transport(request: LLMRequest) -> LLMResponse:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - exercised only in a real llm env
            raise TransportError(f"openai package not installed: {exc}") from exc
        try:
            sdk_client = openai.OpenAI(api_key=cfg.openai_api_key)
            completion = sdk_client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                temperature=0,
                timeout=request.timeout,
            )
        except Exception as exc:  # pragma: no cover - real SDK/network failure
            raise TransportError(str(exc)) from exc
        text = completion.choices[0].message.content or ""
        usage = getattr(completion, "usage", None)
        tokens = getattr(usage, "total_tokens", 0) if usage is not None else 0
        return LLMResponse(text=text, tokens=tokens)

    return LLMClient(_openai_transport, model=cfg.openai_model, cost_cap_usd=cfg.llm_cost_cap_usd)


def apply_llm_fallback(
    config,
    bank_txn_id: str,
    reason: str,
    relation: str,
    features: dict,
    category: str,
    detail: str,
    *,
    client_builder=None,
) -> tuple[str, str, Optional[Decimal]]:
    """Called by ``routing/engine.py`` after ``classify_bank`` runs. Returns
    ``(category, detail, confidence)``.

    Rules 1-6 pass straight through unchanged (``confidence`` is ``None``) --
    this only ever acts when ``category``/``detail`` are rule 7's own output
    AND ``config.openai_enabled()``. ``client_builder`` defaults to
    module-level :func:`get_client` (looked up by name at call time, not
    bound at definition time, so tests can monkeypatch
    ``llm_classifier.get_client`` and have this function pick it up)."""
    if category != "unidentified_counterpart" or RULE7_MARKER not in detail:
        return category, detail, None
    if not config.openai_enabled():
        return category, detail, None

    builder = client_builder if client_builder is not None else get_client
    client = builder(config)
    if client is None:
        return category, detail, None

    result = classify_via_llm(client, bank_txn_id, reason, relation, features)
    if result is None:
        return category, detail, None

    new_detail = f"{LLM_TAG}: category={result.category} confidence={result.confidence} (fallback for: {detail})"
    return result.category, new_detail, result.confidence
