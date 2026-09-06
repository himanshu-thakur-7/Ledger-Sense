"""Agent 5 -- Metrics Orchestrator (spec §9).

Runs the whole pass-1/pass-2 chain's comparison, never the chain itself: it
reads Agents 1-4's own output files (never imports their internals -- law
L1) plus, uniquely among the five agents, ``match_links.csv`` -- the one
ground-truth table law L2 reserves for Agent 5 alone, so it can report a
straight-through rate and match precision actually checked against reality
instead of Agent 1's own claim of success.

See ``io.py`` for the read-only, duplicated-schema file boundary,
``classify.py`` for the exception-class shape vocabulary, ``scoreboard.py``
for the pure pass-1-vs-pass-2 computation, and ``cli.py`` for the
``scoreboard`` demo surface (BOARD.md W6 card / PRD §9).
"""

from .classify import amount_bucket, class_histogram, exception_class, reference_pattern
from .scoreboard import ScoreboardError, build_scoreboard, ground_truth_map, guardrail_split, straight_through

__all__ = [
    "amount_bucket",
    "class_histogram",
    "exception_class",
    "reference_pattern",
    "ScoreboardError",
    "build_scoreboard",
    "ground_truth_map",
    "guardrail_split",
    "straight_through",
]
