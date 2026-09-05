"""Ledger Sense.

An autonomous office of the CFO: five file-spine agents (matching, routing,
learning, guardrail, metrics) plus a deterministic synthetic data generator
(`ledger_sense.data`) that learns an organization's recurring way of
resolving the exceptions rule engines can't handle.

Each subpackage below is a stub. Every agent talks to the others only
through files on disk (see BOARD.md, law L1) — no subpackage may import
another agent's internals.
"""

__version__ = "0.1.0"
