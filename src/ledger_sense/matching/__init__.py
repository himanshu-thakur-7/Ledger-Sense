"""Agent 1 — deterministic matching with a zero-cost adjudication seam (§5)."""

from .blocking import CandidateIndex
from .engine import MatchResult, match
from .io import run

__all__ = ["CandidateIndex", "MatchResult", "match", "run"]
