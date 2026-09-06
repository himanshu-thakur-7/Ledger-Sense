"""Agent 3 -- Resolution-Learning, the core bet (spec §7).

Captures how a human resolves a routed exception (``resolve``, §7.1) and,
on explicit human confirmation (``promote``, §7.3, law L14), turns it into a
predicate over Agent 1's own feature vocabulary (law L11) that a pass-2 run
checks before an escalated line ever reaches Agent 2's routing (§7.4). See
``BOARD.md`` W5 card and PRD §7 for the full spec this implements.

No agent import from ``ledger_sense.matching`` or ``ledger_sense.routing``
lives anywhere in this package (law L1) -- see ``io.py`` for the read-only,
duplicated-schema CSV boundary and ``apply.py`` for the file-level pass-2
hand-off.
"""

from .apply import ApplyResult, apply_rules
from .guardrail_check import veto
from .predicate import build_predicate, evaluate_predicate, render_english
from .resolution import NON_RULE_TYPES, RESOLUTION_TYPES, Resolution, ResolutionError, make_resolution
from .rules import RuleError, candidate_rule, load_candidates, load_rules, matching_rule, promote, save_candidates, save_rules

__all__ = [
    "ApplyResult",
    "apply_rules",
    "veto",
    "build_predicate",
    "evaluate_predicate",
    "render_english",
    "NON_RULE_TYPES",
    "RESOLUTION_TYPES",
    "Resolution",
    "ResolutionError",
    "make_resolution",
    "RuleError",
    "candidate_rule",
    "load_candidates",
    "load_rules",
    "matching_rule",
    "promote",
    "save_candidates",
    "save_rules",
]
