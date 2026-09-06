"""Neatlogs tracing/observability wrapper (spec: LEDGER-SENSE-v2-PRD.md, W10;
fixed against the real SDK by TAPE-1).

``traced_run(agent_name, **metadata)`` is the single wrap point every agent
CLI entrypoint calls -- either as a decorator on its ``main`` function or as
a context manager around its body (both work; ``traced_run`` is a
``contextlib.ContextDecorator``). Each of the six entrypoints
(``data/cli.py``, ``matching/__main__.py``, ``routing/__main__.py``,
``guardrail/cli.py``, ``learning/cli.py``, ``metrics/cli.py``) adds exactly
one such call -- nothing else about those files changes.

TAPE-1 fix: W10's original implementation called a ``neatlogs.Client(...)``
that does not exist in the real, installed Neatlogs SDK (confirmed live by
W14's smoke test -- every span silently failed to send, 0/4, even though
L18's fallback kept every CLI exiting clean). The real SDK is a
module-level API, not a client object:

  * ``neatlogs.init(api_key=..., workflow_name="ledger-sense")`` -- once per
    process, before anything that might import ``openai`` runs (this
    module's ``__enter__`` always runs first, since ``traced_run`` wraps the
    *entire* entrypoint body/decorated function).
  * ``neatlogs.span(kind=WORKFLOW, name=agent_name)`` -- one span per agent
    run, entered/exited around the wrapped call.
  * ``neatlogs.flush()`` / ``neatlogs.shutdown()`` -- called once, at the end
    of the same ``traced_run`` block, since every entrypoint's single wrap
    point already spans that process's entire agent run (i.e. "on CLI exit").

Behavior (L18 -- absence of a live-mode key degrades gracefully, never
crashes, never changes v1's zero-key output):

  * ``config.tracing_enabled()`` False (no ``NEATLOGS_API_KEY``) -> this is a
    complete, zero-overhead no-op. Nothing is imported from ``neatlogs``, no
    span is opened, stdout is never touched -- the wrapped call runs exactly
    as if ``tracing.py`` did not exist.
  * ``tracing_enabled()`` True -> ``neatlogs.init(...)`` runs, then a single
    ``neatlogs.span(kind=WORKFLOW, name=agent_name)`` wraps the call, then
    ``flush()``/``shutdown()`` runs before this process exits.

Never raises (L18): initializing Neatlogs, opening/closing the span, or
attaching metadata to it is wrapped in a broad ``except Exception`` per step
that swallows the failure and prints exactly one stderr line -- the wrapped
call's own return value or exception always propagates completely
unchanged. Only the wrapped call's *own* exception is ever allowed through.

Redaction (L19): every string value attached to a span -- static metadata
and the run's own status/error -- is passed through ``llm_client.redact``
before it is ever handed to the (real or mocked) Neatlogs SDK.
"""

from __future__ import annotations

import sys
import time
from contextlib import ContextDecorator
from typing import Any

from .config import Config, load_config
from .llm_client import redact

# The exact workflow name every span in this project is grouped under.
_WORKFLOW_NAME = "ledger-sense"


def _redact_value(value: Any) -> Any:
    """Recursively apply ``llm_client.redact`` to every string reachable
    from ``value`` (L19) -- dicts/lists/tuples are walked, everything else
    (ints, bools, None, ...) passes through unchanged."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _init(cfg: Config) -> None:
    """Lazily import and initialize the real Neatlogs SDK.

    Module-level API, not a client instance -- there is no ``neatlogs.Client``
    (that was W10's bug; confirmed against the real installed package by
    W14's live smoke test). Only ever called when ``cfg.tracing_enabled()``
    is True, from inside ``traced_run``'s own broad ``except Exception`` --
    so a missing ``neatlogs`` package (the base install stays
    dependency-free, L20) or a real init failure never crashes the caller.

    Every test in this repo (L20) monkeypatches ``_init``/``_span``/
    ``_flush`` directly with fakes -- none ever reaches the real
    ``import neatlogs`` below.
    """
    import neatlogs  # noqa: PLC0415 -- deliberately lazy, and BEFORE any openai import

    neatlogs.init(api_key=cfg.neatlogs_api_key, workflow_name=_WORKFLOW_NAME)


def _span(agent_name: str) -> Any:
    """Open one Neatlogs span (``kind=WORKFLOW``) for this agent run.

    Returns the SDK's own context-manager object; ``traced_run`` enters and
    exits it itself (rather than using ``with neatlogs.span(...):`` directly)
    so a failure entering or exiting it can be caught and degraded exactly
    like every other tracing failure (L18).
    """
    import neatlogs  # noqa: PLC0415

    kind = getattr(neatlogs, "WORKFLOW", "WORKFLOW")
    return neatlogs.span(kind=kind, name=agent_name)


def _flush() -> None:
    """Flush/shut down the Neatlogs SDK -- called once, at the end of this
    process's single ``traced_run`` block (i.e. "on CLI exit"). Tries both
    ``flush()`` and ``shutdown()``, whichever the installed SDK actually
    exposes; calling neither is not an error (nothing to flush)."""
    import neatlogs  # noqa: PLC0415

    for name in ("flush", "shutdown"):
        fn = getattr(neatlogs, name, None)
        if callable(fn):
            fn()


def _attach_metadata(span: Any, metadata: dict) -> None:
    """Best-effort, redacted metadata attachment onto ``span``.

    Tries whichever tagging method the real span object exposes
    (``add_tags`` first, then per-key ``set_attribute``); does nothing if
    neither exists. Never raises -- callers wrap this in their own
    ``except Exception`` too, but this never needs it to (L18/L19).
    """
    redacted = _redact_value(metadata)
    add_tags = getattr(span, "add_tags", None)
    if callable(add_tags):
        add_tags(redacted)
        return
    set_attribute = getattr(span, "set_attribute", None)
    if callable(set_attribute):
        for key, value in redacted.items():
            set_attribute(key, value)


class traced_run(ContextDecorator):
    """``with traced_run("guardrail"):`` or ``@traced_run("guardrail")`` --
    the one wrap point every agent CLI entrypoint calls (L18/L19/L20; see
    ``tests/test_tracing.py``).

    ``agent_name`` and any ``**metadata`` are attached to the span as-is
    (after redaction). Nothing here ever raises, and nothing here ever
    changes the wrapped call's return value or lets an exception it didn't
    raise itself escape.
    """

    def __init__(self, agent_name: str, **metadata: Any) -> None:
        self._agent_name = agent_name
        self._static_metadata = metadata
        self._enabled = False
        self._start = 0.0
        self._initialized = False
        self._span_cm: Any = None
        self._span: Any = None

    def __enter__(self) -> "traced_run":
        # A fresh read (not the module-level singleton) so a caller that
        # mutated the environment/`.env` since import time -- every test in
        # this repo, via monkeypatch -- is honored (see config.py's own
        # docstring on `load_config` vs. the singleton).
        cfg = load_config()
        self._enabled = cfg.tracing_enabled()
        self._start = time.monotonic()
        if not self._enabled:
            return self  # true no-op: nothing is imported, nothing is touched

        try:
            _init(cfg)
            self._initialized = True
        except Exception as exc:
            print(f"tracing: neatlogs init failed -- {exc}", file=sys.stderr)
            return self  # pipeline continues without a span (L18)

        try:
            self._span_cm = _span(self._agent_name)
            self._span = self._span_cm.__enter__()
        except Exception as exc:
            print(f"tracing: neatlogs span failed -- {exc}", file=sys.stderr)
            self._span_cm = None
            self._span = None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_seconds = time.monotonic() - self._start
        if not self._enabled:
            return False  # true no-op: nothing was touched, nothing to undo

        if self._span is not None:
            try:
                metadata = dict(self._static_metadata)
                metadata["duration_seconds"] = duration_seconds
                metadata["status"] = "error" if exc is not None else "ok"
                if exc is not None:
                    metadata["error"] = f"{type(exc).__name__}: {exc}"
                _attach_metadata(self._span, metadata)
            except Exception:
                pass  # tracing must never crash or change the pipeline (L18)

        if self._span_cm is not None:
            try:
                self._span_cm.__exit__(exc_type, exc, tb)
            except Exception:
                pass

        if self._initialized:
            try:
                _flush()
            except Exception as flush_exc:
                print(f"tracing: neatlogs flush failed -- {flush_exc}", file=sys.stderr)

        return False  # never swallow the wrapped call's own exception
