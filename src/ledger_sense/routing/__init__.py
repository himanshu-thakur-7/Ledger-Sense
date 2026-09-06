"""Agent 2 — Ownership / Routing (spec §6).

Reads Agent 1's ``match_outcomes.csv`` / ``ledger_settlements.csv`` (plus the
original ``ledger.csv`` / ``bank.csv``) and writes ``exceptions.csv`` +
``owner_queues.csv``. Never reads the ground-truth MatchLink table and never imports
``ledger_sense.matching`` -- see ``tests/test_routing_isolation.py``.
"""
