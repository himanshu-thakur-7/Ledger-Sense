"""Neatlogs tracing/observability wrapper (spec: LEDGER-SENSE-v2-PRD.md, W10).

``traced_run(agent_name, **metadata)`` is the single wrap point every agent CLI
entrypoint calls -- either as a decorator on its ``main`` function or as a
context manager around its body (both work; ``traced_run`` is a
``contextlib.ContextDecorator``). Each of the six entrypoints
(``data/cli.py``, ``matching/__main__.py``, ``routing/__main__.py``,
``guardrail/__main__.py``, ``learning/cli.py``, ``metrics/cli.py``) adds
exactly one such call -- nothing else about those files changes.

Behavior (L18 -- absence of a live-mode key degrades gracefully, never
crashes, never changes v1's zero-key output):

  * ``config.tracing_enabled()`` False (no ``NEATLOGS_API_KEY``) -> this is a
    complete, zero-overhead no-op. Nothing is imported from ``neatlogs``, no
    client is built, stdout is never touched -- the wrapped call runs exactly
    as if ``tracing.py`` did not exist.
  * ``tracing_enabled()`` True -> starts a Neatlogs span, timed, tagged with
    ``agent_name`` plus the caller's static ``**metadata``. Every entrypoint
    already prints an agent/row-count/verdict-breakdown/llm_calls-shaped
    summary line (see each ``__main__.py``/``cli.py``) -- rather than require
    a *second* edit to those files to thread that data through, the span
    best-effort-enriches itself by reading the wrapped call's own stdout
    (teed, so the terminal still sees exactly what it always saw).

Never raises (L18): building the Neatlogs client, sending the span, or
parsing stdout for extra metadata is wrapped in a broad ``except Exception``
that swallows the failure -- the wrapped call's own return value or exception
always propagates completely unchanged. Only the wrapped call's *own*
exception is ever allowed through.

Redaction (L19): every string value attached to a span -- static metadata and
whatever was parsed from stdout alike -- is passed through
``llm_client.redact`` before it is ever handed to the Neatlogs client, so a
credential-shaped value can never reach the (real or mocked) transport.
"""

from __future__ import annotations

import re
import sys
import time
from contextlib import ContextDecorator
from typing import Any

from .config import Config, load_config
from .llm_client import redact

# The exact `key=value` shape every entrypoint's first summary line already
# uses (e.g. "bank lines=54; ledger entries=49; matched=49"). Only numeric
# values are kept -- that is what "input/output row counts" means here; a
# non-numeric value (a model name, a policy version, ...) is not a row count
# and is left out rather than guessed at.
_INT_VALUE_RE = re.compile(r"^-?\d+$")

# Guardrail's per-line "allow: N/total (P%)" breakdown (guardrail entrypoint
# only -- no other entrypoint ever prints a line shaped like this).
_VERDICT_RE = re.compile(r"^(allow|block|hold):\s*(\d+)/(\d+)")

# matching/learning/routing's shared "llm_calls=N"/"llm_is_stub=True" shape,
# present only when an LLM seam actually ran (W9/W12/W13).
_LLM_CALLS_RE = re.compile(r"\bllm_calls=(\d+)\b")
_LLM_STUB_RE = re.compile(r"\bllm_is_stub=(True|False)\b")


def _build_client(cfg: Config) -> Any:
    """Lazily import and construct the real Neatlogs SDK client.

    Only ever called when ``cfg.tracing_enabled()`` is True, from inside
    ``traced_run``'s own broad ``except Exception`` -- so a missing
    ``neatlogs`` package (the base install stays dependency-free, L20) or a
    real construction failure never crashes the caller, it just means this
    run's span silently isn't sent (L18).

    Every test in this repo (L20) monkeypatches this function directly with
    a fake client -- none ever reaches the real ``import neatlogs`` below.
    """
    import neatlogs  # noqa: PLC0415 -- deliberately lazy, mirrors llm_adjudicator.py

    return neatlogs.Client(api_key=cfg.neatlogs_api_key)


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


def _parse_stdout_metadata(captured: str) -> dict:
    """Best-effort span enrichment from the wrapped call's own stdout.

    This is the only way the single wrap point sees row counts/verdict
    breakdowns/LLM stats without a second edit to the entrypoint files
    (see module docstring). Never raises -- an unparseable line is simply
    not included, it never breaks the span or the wrapped call.
    """
    metadata: dict = {}
    lines = captured.splitlines()

    if lines:
        for segment in lines[0].split(";"):
            if "=" not in segment:
                continue
            key, _, value = segment.partition("=")
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()
            if key and _INT_VALUE_RE.match(value):
                metadata[key] = int(value)

    verdicts = {}
    for line in lines:
        match = _VERDICT_RE.match(line.strip())
        if match:
            verdicts[match.group(1)] = int(match.group(2))
    if verdicts:
        metadata["guardrail_verdicts"] = verdicts

    for line in lines:
        calls_match = _LLM_CALLS_RE.search(line)
        if calls_match:
            metadata["llm_calls"] = int(calls_match.group(1))
        stub_match = _LLM_STUB_RE.search(line)
        if stub_match:
            metadata["llm_is_stub"] = stub_match.group(1) == "True"

    return metadata


class _Tee:
    """Forwards every write to the real stream while also buffering a copy,
    so tracing can read a CLI's own stdout without changing what actually
    reaches the terminal (or a test's ``capsys``/``subprocess`` capture)."""

    def __init__(self, original):
        self._original = original
        self._chunks: list[str] = []

    def write(self, text: str) -> int:
        self._chunks.append(text)
        return self._original.write(text)

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)

    def getvalue(self) -> str:
        return "".join(self._chunks)


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
        self._cfg: Config | None = None
        self._enabled = False
        self._start = 0.0
        self._tee: _Tee | None = None
        self._orig_stdout = None

    def __enter__(self) -> "traced_run":
        # A fresh read (not the module-level singleton) so a caller that
        # mutated the environment/`.env` since import time -- every test in
        # this repo, via monkeypatch -- is honored (see config.py's own
        # docstring on `load_config` vs. the singleton).
        self._cfg = load_config()
        self._enabled = self._cfg.tracing_enabled()
        self._start = time.monotonic()
        if self._enabled:
            self._orig_stdout = sys.stdout
            self._tee = _Tee(sys.stdout)
            sys.stdout = self._tee
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_seconds = time.monotonic() - self._start
        if not self._enabled:
            return False  # true no-op: nothing was touched, nothing to undo

        captured = ""
        if self._tee is not None:
            sys.stdout = self._orig_stdout
            captured = self._tee.getvalue()

        try:
            self._emit_span(duration_seconds, captured, exc)
        except Exception:
            pass  # tracing must never crash or change the pipeline (L18)

        return False  # never swallow the wrapped call's own exception

    def _emit_span(self, duration_seconds: float, captured: str, exc: BaseException | None) -> None:
        metadata = dict(self._static_metadata)
        metadata.update(_parse_stdout_metadata(captured))
        metadata["duration_seconds"] = duration_seconds
        metadata["status"] = "error" if exc is not None else "ok"
        if exc is not None:
            metadata["error"] = f"{type(exc).__name__}: {exc}"

        payload = {"agent": self._agent_name, **metadata}
        payload = _redact_value(payload)

        client = _build_client(self._cfg)
        client.send(payload)
