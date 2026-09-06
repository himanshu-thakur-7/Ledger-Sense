"""Free-text intent classification for the close desk (spec: BOARD.md TAPE-1
part C). Regex first, always -- every intent below must work byte-for-byte
identically with ``OPENAI_API_KEY`` unset. OpenAI may only *paraphrase* free
text into one of this module's own fixed, closed vocabulary of no-argument
intent names (``pull``/``analyze``/``next_close``/``status``/``logs``/
``quit``) when the regexes find nothing at all -- it is never asked to
invent a ``resolve``/``promote``'s structured arguments (a rule's predicate
still needs either explicit flags or ``learning.llm_rationale``'s own,
already-existing, separately law-L11-bounded suggestion step, which the
real ``ledger_sense resolve`` CLI itself already runs when a key is
configured -- see ``actions.resolve``).

A single line of free text may name more than one simple intent at once
(the card's own example: ``"pull the bank and show discrepancies"``) -- every
regex is a *search*, not a full match, and every intent found is returned in
the order it appears in the text.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional

from ..config import Config

# --- simple, no-argument intents -- may combine freely in one line ---------

_SIMPLE_PATTERNS = (
    ("pull", re.compile(r"\b(pull|get data|fetch dodo)\b", re.IGNORECASE)),
    ("analyze", re.compile(r"\b(analy[sz]e|find discrepancies|show discrepancies|"
                            r"what'?s broken|show exceptions)\b", re.IGNORECASE)),
    ("next_close", re.compile(r"\b(next close|run pass ?2|did it learn)\b", re.IGNORECASE)),
    ("status", re.compile(r"\b(status|where are we)\b", re.IGNORECASE)),
    ("logs", re.compile(r"\b(logs?|traces?)\b", re.IGNORECASE)),
    ("quit", re.compile(r"\b(quit|exit)\b", re.IGNORECASE)),
)

SIMPLE_INTENT_NAMES = tuple(name for name, _ in _SIMPLE_PATTERNS)

RESOLUTION_TYPES = (
    "fee_offset", "reference_transform", "counterparty_alias",
    "timing_tolerance", "manual_one_off", "no_pattern",
)


@dataclass
class Intent:
    name: str
    args: dict = field(default_factory=dict)


def _parse_resolve(rest: str) -> Optional[Intent]:
    """``resolve <exception_id|that|that one> <resolution_type> [--flags] [rationale]``.
    Returns ``None`` (never raises) on anything that doesn't parse -- the
    caller reports a plain "couldn't parse that resolve" instead."""
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return None
    if not tokens:
        return None

    if tokens[0].lower() == "that" and len(tokens) > 1 and tokens[1].lower() == "one":
        exception_ref, remainder = "that one", tokens[2:]
    else:
        exception_ref, remainder = tokens[0], tokens[1:]

    resolution_type = None
    rest_tokens = []
    for token in remainder:
        if resolution_type is None and token in RESOLUTION_TYPES:
            resolution_type = token
        else:
            rest_tokens.append(token)
    if resolution_type is None:
        return None

    flags = {
        "--counterparty-key": None, "--currency": None, "--amount-delta-min": None,
        "--amount-delta-max": None, "--reference-transform": None, "--amount-class": None,
        "--rationale": None, "--resolved-by": None, "--resolved-at": None,
    }
    leftover = []
    i = 0
    while i < len(rest_tokens):
        token = rest_tokens[i]
        if token in flags and i + 1 < len(rest_tokens):
            flags[token] = rest_tokens[i + 1]
            i += 2
        else:
            leftover.append(token)
            i += 1

    rationale = flags.pop("--rationale") or (" ".join(leftover) if leftover else None)
    resolved_by = flags.pop("--resolved-by")
    resolved_at = flags.pop("--resolved-at")

    return Intent("resolve", {
        "exception_ref": exception_ref,
        "resolution_type": resolution_type,
        "predicate_flags": flags,
        "rationale": rationale,
        "resolved_by": resolved_by,
        "resolved_at": resolved_at,
    })


def _parse_promote(rest: str) -> Optional[Intent]:
    """``promote <rule_id> <confirm>``."""
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return None
    if len(tokens) < 2:
        return None
    return Intent("promote", {"rule_id": tokens[0], "confirm": tokens[1]})


def classify(text: str, cfg: Optional[Config] = None) -> List[Intent]:
    """Classify one line of desk input into an ordered list of
    :class:`Intent`. Regex-first, always -- ``cfg`` is only ever consulted
    (for an OpenAI paraphrase) when every regex below finds nothing."""
    stripped = text.strip()
    if not stripped:
        return []

    lowered = stripped.lower()
    if lowered.startswith("resolve"):
        parsed = _parse_resolve(stripped[len("resolve"):])
        return [parsed] if parsed else []
    if lowered.startswith("promote"):
        parsed = _parse_promote(stripped[len("promote"):])
        return [parsed] if parsed else []

    found = []
    for name, pattern in _SIMPLE_PATTERNS:
        match = pattern.search(stripped)
        if match:
            found.append((match.start(), name))
    if found:
        found.sort(key=lambda pair: pair[0])
        seen = set()
        ordered = []
        for _, name in found:
            if name not in seen:
                seen.add(name)
                ordered.append(Intent(name))
        return ordered

    # Nothing matched -- OpenAI may paraphrase into one of the simple intent
    # names above, but only if a key is configured (L18: keyless must behave
    # exactly like a normal "didn't understand" case).
    if cfg is not None and cfg.openai_enabled():
        paraphrased = _llm_paraphrase(stripped, cfg)
        if paraphrased in SIMPLE_INTENT_NAMES:
            return [Intent(paraphrased)]
    return []


_PARAPHRASE_PROMPT = """A human typed this free-text order at a CFO-office reconciliation
close desk's terminal prompt:

"{text}"

Classify it as EXACTLY ONE of these words, and respond with that single word
only, no punctuation, no prose: {options}

If it doesn't clearly match any of them, respond with the single word: none"""


def _llm_paraphrase(text: str, cfg: Config) -> Optional[str]:
    """Best-effort OpenAI paraphrase into one of ``SIMPLE_INTENT_NAMES``.
    Never raises -- any transport/parse failure just means "didn't
    understand" (same as a keyless miss, L18)."""
    try:
        from ..llm_client import LLMClient, LLMRequest, LLMResponse

        def _transport(request: "LLMRequest") -> "LLMResponse":
            import openai  # noqa: PLC0415 -- lazy, mirrors llm_rationale.py

            client = openai.OpenAI()
            completion = client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                timeout=request.timeout,
            )
            return LLMResponse(text=completion.choices[0].message.content or "")

        client = LLMClient(_transport, model=cfg.openai_model, cost_cap_usd=cfg.llm_cost_cap_usd)
        prompt = _PARAPHRASE_PROMPT.format(text=text, options=", ".join(SIMPLE_INTENT_NAMES))
        response = client.complete(prompt, cache_key=("intent-paraphrase", text))
        answer = response.text.strip().lower()
        return answer if answer in SIMPLE_INTENT_NAMES else None
    except Exception:
        return None
