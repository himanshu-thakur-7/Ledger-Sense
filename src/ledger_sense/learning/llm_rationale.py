"""OpenAI resolution-learning rationale assist (spec: LEDGER-SENSE-v2-PRD.md, W12).

Takes a human's structured :class:`~ledger_sense.learning.resolution.Resolution`
(``resolution_type`` + free-text ``rationale``) and asks the shared
``llm_client.py`` wrapper (locked decision 3) to suggest a candidate
predicate -- restricted to ``predicate.py``'s existing, fixed vocabulary
(``PREDICATE_FIELDS``/``REFERENCE_TRANSFORMS``/``AMOUNT_CLASSES``), never a
new one (law L11). The suggestion is always clearly labeled a suggestion by
the caller (``learning/cli.py``'s ``resolve`` command); it never bypasses
the human's own ``promote --confirm yes-always`` step (law L14), which stays
the only path that ever writes ``rules.json``.

``manual_one_off``/``no_pattern`` never receive a suggestion (law L13, spec
§7.1) -- :func:`suggest_predicate` refuses those before the transport is
ever reached, matching :mod:`resolution`'s own rule that they must never
carry a predicate.

Graceful degradation (L18/L21): any failure here -- disabled config, a
transport error, a cost-cap/call-cap refusal, or a model answer that
doesn't parse into valid vocabulary -- returns ``None`` and never raises.
``resolve`` then falls back to fully manual entry exactly as in v1; this
module is never given authority beyond an optional prefill for the human's
own predicate flags.

This module never imports ``ledger_sense.matching``/``ledger_sense.routing``
(law L1) and, like every other file in this package, contains no float
literal (money/scores are Decimal/int-cents only, per this package's own
isolation test).
"""

from __future__ import annotations

import json
from typing import Optional

from ledger_sense.llm_client import LLMClient, LLMRequest, LLMResponse

from .predicate import AMOUNT_CLASSES, PREDICATE_FIELDS, REFERENCE_TRANSFORMS, squash
from .resolution import NON_RULE_TYPES

_PROMPT_TEMPLATE = """A human is closing a reconciliation exception with this structured resolution:

resolution_type: {resolution_type}
rationale (free text, written by the human): {rationale}

Suggest ONE candidate predicate over the matcher's existing feature space.
Respond with a single JSON object using ONLY these fields -- never invent a
new field: {fields}.

Rules:
- counterparty_key: an uppercase, alphanumeric-only string (no spaces or punctuation).
- currency: a 3-letter ISO currency code.
- amount_delta_cents_min / amount_delta_cents_max: non-negative integers, in cents (never a decimal).
- reference_transform: one of {reference_transforms}.
- amount_class: one of {amount_classes}.

Omit any field you are not confident about. Respond with JSON only, no prose, no markdown fences."""


def _build_prompt(resolution_type: str, rationale: str) -> str:
    return _PROMPT_TEMPLATE.format(
        resolution_type=resolution_type,
        rationale=rationale,
        fields=", ".join(PREDICATE_FIELDS),
        reference_transforms=", ".join(REFERENCE_TRANSFORMS),
        amount_classes=", ".join(AMOUNT_CLASSES),
    )


def _parse_suggestion(text: str) -> Optional[dict]:
    """Parse a model answer into a predicate restricted to the existing
    vocabulary. Any field outside ``PREDICATE_FIELDS``, any value outside
    the fixed enums, or any unparseable/non-object answer is dropped or
    rejected -- never smuggled through as a "new" predicate shape (law L11).
    Returns ``None`` for an answer that yields no usable field at all."""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None

    predicate: dict = {}
    for key, value in raw.items():
        if key not in PREDICATE_FIELDS:
            continue
        if key == "counterparty_key":
            if isinstance(value, str) and value.strip():
                predicate[key] = squash(value)
        elif key == "currency":
            if isinstance(value, str) and value.strip():
                predicate[key] = value.strip().upper()
        elif key in ("amount_delta_cents_min", "amount_delta_cents_max"):
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                predicate[key] = value
        elif key == "reference_transform":
            if value in REFERENCE_TRANSFORMS:
                predicate[key] = value
        elif key == "amount_class":
            if value in AMOUNT_CLASSES:
                predicate[key] = value

    if "amount_delta_cents_min" in predicate and "amount_delta_cents_max" in predicate:
        if predicate["amount_delta_cents_min"] > predicate["amount_delta_cents_max"]:
            # Inconsistent bounds from the model -- drop the upper bound
            # rather than guess which one the human actually meant.
            del predicate["amount_delta_cents_max"]

    return predicate or None


def suggest_predicate(
    *,
    resolution_type: str,
    rationale: str,
    client: Optional[LLMClient],
    cache_key=None,
) -> Optional[dict]:
    """Ask ``client`` to suggest a predicate for one resolution.

    Returns ``None`` -- never raises -- when: ``resolution_type`` is a
    first-class no-pattern outcome (law L13, checked before any call is
    made); ``client`` is ``None`` (e.g. no key configured); the transport
    raises (retryable failure exhausted, timeout, cost/call-cap refusal);
    or the model's answer doesn't parse into at least one valid vocabulary
    field.
    """
    if resolution_type in NON_RULE_TYPES:
        return None
    if client is None:
        return None

    prompt = _build_prompt(resolution_type, rationale)
    try:
        response = client.complete(prompt, cache_key=cache_key)
    except Exception:
        return None

    return _parse_suggestion(response.text)


def _openai_transport(request: LLMRequest) -> LLMResponse:
    """The real transport, plugged into ``LLMClient`` only when an OpenAI
    key is configured. ``openai`` is imported lazily, here, so this module
    (and every test importing it) never requires the package to be
    installed unless a real call is actually about to happen -- v1's
    zero-dependency default stays intact (L18)."""
    import openai  # imported lazily -- never required unless this line actually runs

    oai_client = openai.OpenAI()
    completion = oai_client.chat.completions.create(
        model=request.model,
        messages=[{"role": "user", "content": request.prompt}],
        timeout=request.timeout,
    )
    text = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    tokens = getattr(usage, "total_tokens", 0) if usage is not None else 0
    return LLMResponse(text=text, tokens=tokens)


def build_client(cfg) -> Optional[LLMClient]:
    """Build the shared ``LLMClient`` wired to the real OpenAI transport, or
    ``None`` when no key is configured (L18: absent key -> no client, caller
    falls back to fully manual entry). ``cfg`` is a
    :class:`ledger_sense.config.Config` (or the module-level singleton)."""
    if not cfg.openai_enabled():
        return None
    return LLMClient(
        _openai_transport,
        model=cfg.openai_model,
        cost_cap_usd=cfg.llm_cost_cap_usd,
    )
