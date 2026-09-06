"""Shared LLM client wrapper (spec: LEDGER-SENSE-v2-PRD.md, locked decision 3, W8).

One thin wrapper around an *injectable* transport callable -- this module never
imports or calls a real provider SDK (OpenAI, Dodo, Neatlogs). W9/W12/W13 plug
the real `openai` call in as the ``transport`` argument; every test in this
repo plugs in a fake instead, so the full suite stays 100% offline (L20).

Responsibilities enforced here, once, for every future caller (L22):
  * bounded retries with exponential backoff on transient transport failures
  * a request timeout per call
  * a per-run cumulative cost cap that short-circuits *before* a call that
    would exceed it is ever made
  * an optional per-run call-count cap, same short-circuit behavior
  * response caching keyed by a caller-supplied key (e.g. `(ledger_id,
    bank_txn_id)`), so repeat questions never re-spend
  * a `redact()` helper that strips credential-shaped substrings before
    anything is logged (L19)
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LLMClientError(Exception):
    """Base class for every error this module raises."""


class TransportError(LLMClientError):
    """Raised by a transport implementation on a retryable failure."""


class RequestTimedOut(LLMClientError):
    """A single transport call exceeded its timeout budget."""


class CostCapExceeded(LLMClientError):
    """The call was refused before dispatch because it would breach the cap.

    Raised pre-flight -- the transport is never invoked when this fires, so a
    breach never actually happens, it is only ever prevented (L22).
    """


class CallCapExceeded(LLMClientError):
    """The call was refused before dispatch because the call-count cap was hit."""


# ---------------------------------------------------------------------------
# Request/response shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMResponse:
    """What a transport callable must return."""

    text: str
    cost_usd: float = 0.0
    tokens: int = 0
    raw: Any = None


@dataclass(frozen=True)
class LLMRequest:
    """What a transport callable receives."""

    prompt: str
    model: str
    timeout: float
    metadata: dict = field(default_factory=dict)


Transport = Callable[[LLMRequest], LLMResponse]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    """Thin retry/timeout/cost-cap/cache wrapper around an injected transport.

    ``transport`` is any callable ``LLMRequest -> LLMResponse``. It should
    raise `TransportError` for a retryable failure (network blip, 5xx, rate
    limit) -- any other exception it raises propagates immediately, uncaught,
    without retry.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        model: str = "gpt-4o-mini",
        cost_cap_usd: float = 1.00,
        max_calls: int | None = None,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.1,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if cost_cap_usd < 0:
            raise ValueError("cost_cap_usd must be >= 0")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")

        self._transport = transport
        self.model = model
        self.cost_cap_usd = cost_cap_usd
        self.max_calls = max_calls
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.timeout_seconds = timeout_seconds
        self._sleep = sleep

        self.cumulative_cost_usd = 0.0
        self.calls_made = 0
        self.cache_hits = 0
        self._cache: dict[Hashable, LLMResponse] = {}

    # -- public API ---------------------------------------------------------

    def complete(
        self,
        prompt: str,
        *,
        estimated_cost_usd: float = 0.0,
        cache_key: Hashable | None = None,
        timeout_seconds: float | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        """Run one request through the transport, applying every guard rail.

        `estimated_cost_usd` is a conservative caller-supplied estimate,
        checked against the remaining cost-cap budget *before* the transport
        is invoked. If the transport's response reports an actual
        `cost_usd`, that actual figure (not the estimate) is what gets added
        to the running total.
        """
        if cache_key is not None and cache_key in self._cache:
            self.cache_hits += 1
            return self._cache[cache_key]

        if self.max_calls is not None and self.calls_made >= self.max_calls:
            raise CallCapExceeded(
                f"call cap reached ({self.calls_made}/{self.max_calls}); refusing to dispatch"
            )

        projected = self.cumulative_cost_usd + max(estimated_cost_usd, 0.0)
        if projected > self.cost_cap_usd:
            raise CostCapExceeded(
                f"projected cost ${projected:.4f} exceeds cap ${self.cost_cap_usd:.2f}; "
                "refusing to dispatch"
            )

        request = LLMRequest(
            prompt=prompt,
            model=self.model,
            timeout=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
            metadata=metadata or {},
        )
        response = self._call_with_retries(request)

        self.calls_made += 1
        actual_cost = response.cost_usd if response.cost_usd else estimated_cost_usd
        self.cumulative_cost_usd += max(actual_cost, 0.0)

        if cache_key is not None:
            self._cache[cache_key] = response
        return response

    def remaining_budget_usd(self) -> float:
        return max(self.cost_cap_usd - self.cumulative_cost_usd, 0.0)

    # -- internals ------------------------------------------------------

    def _call_with_retries(self, request: LLMRequest) -> LLMResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._call_with_timeout(request)
            except (TransportError, RequestTimedOut) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep(self.backoff_base_seconds * (2 ** attempt))
                    continue
                raise
        # Unreachable: the loop above always either returns or raises.
        raise last_error  # pragma: no cover

    def _call_with_timeout(self, request: LLMRequest) -> LLMResponse:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._transport, request)
            try:
                return future.result(timeout=request.timeout)
            except FutureTimeoutError as exc:
                raise RequestTimedOut(
                    f"transport exceeded {request.timeout}s timeout"
                ) from exc


# ---------------------------------------------------------------------------
# Redaction (L19: nothing credential-shaped ever reaches a log line)
# ---------------------------------------------------------------------------

_REDACTED = "[REDACTED]"

# Provider key prefixes (OpenAI-style `sk-...`, and generic `Bearer <token>`).
_PREFIXED_TOKEN_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}\b")
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9_\-\.]{8,})")

# `NAME_KEY=value`, `api_key: "value"`, `DODO_API_KEY=value`, etc. -- matches
# any identifier ending in key/token/secret/password followed by `=`/`:` and a
# value, redacting only the value.
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|token|secret|password|passwd)[A-Za-z0-9_]*"
    r"\s*[:=]\s*)"
    r"(['\"]?)([^\s'\",}]{4,})(\2)"
)


def redact(text: str) -> str:
    """Best-effort strip of API-key/credential-shaped substrings from `text`.

    Not a general secret scanner -- it targets the shapes v2's own
    integrations produce (OpenAI/Dodo/Neatlogs keys and `KEY=value`/`Bearer
    ...` patterns) so nothing from `config.py` can leak through a log line or
    an exception message.
    """
    if not text:
        return text
    redacted = _PREFIXED_TOKEN_RE.sub(_REDACTED, text)
    redacted = _BEARER_RE.sub(lambda m: m.group(1) + _REDACTED, redacted)
    redacted = _ASSIGNMENT_RE.sub(lambda m: m.group(1) + _REDACTED, redacted)
    return redacted
