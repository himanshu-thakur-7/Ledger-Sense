"""Agent 4 — Escalation / Guardrail (spec §8).

A deterministic policy layer deciding whether a payment may release,
independent of whether it matched. See ``engine.run`` for the CLI entry
point's implementation and ``would_block_or_hold`` for the plain function a
future Agent 3 (learning) calls before promoting a rule.
"""

from .engine import GuardrailResult, run, would_block_or_hold
from .policy import load_policy

__all__ = ["GuardrailResult", "run", "would_block_or_hold", "load_policy"]
