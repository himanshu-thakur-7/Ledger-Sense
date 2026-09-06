"""Structured resolution capture (spec §7.1).

A ``resolution`` is what a human actually records when they close a routed
exception: the ``exceptions.csv`` row it closes, an enum saying what kind of
pattern it is, a feature-space predicate (never a bare transaction id --
law L11), free-text rationale (audit/demo narration only -- no rule ever
consults it), and who/when. ``manual_one_off`` and ``no_pattern`` are
first-class outcomes (law L13): they are captured exactly like any other
resolution, they simply never carry a predicate and can never be promoted
(see ``rules.py``, which refuses both by construction).
"""

import hashlib
import json
from dataclasses import asdict, dataclass

RESOLUTION_TYPES = (
    "fee_offset",
    "reference_transform",
    "counterparty_alias",
    "timing_tolerance",
    "manual_one_off",
    "no_pattern",
)
NON_RULE_TYPES = frozenset({"manual_one_off", "no_pattern"})


class ResolutionError(ValueError):
    """A structured resolution was refused -- never a free-text approve."""


@dataclass(frozen=True)
class Resolution:
    resolution_id: str
    exception_id: str
    resolution_type: str
    evidence: dict
    rationale: str
    resolved_by: str
    resolved_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def resolution_id_for(exception_id, resolution_type, evidence, rationale, resolved_by, resolved_at) -> str:
    """Deterministic id: the same structured inputs always produce the same
    id, so re-running the exact same ``resolve`` call never mints a
    duplicate resolution."""
    payload = json.dumps(
        {
            "exception_id": exception_id,
            "resolution_type": resolution_type,
            "evidence": evidence,
            "rationale": rationale,
            "resolved_by": resolved_by,
            "resolved_at": resolved_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"RES-{digest[:16]}"


def make_resolution(*, exception_id, resolution_type, evidence, rationale, resolved_by, resolved_at) -> Resolution:
    """Validate and build a :class:`Resolution`. Raises :class:`ResolutionError`
    for anything a free-text "approve" would have silently let through."""
    if resolution_type not in RESOLUTION_TYPES:
        raise ResolutionError(f"resolution_type must be one of {RESOLUTION_TYPES}, got {resolution_type!r}")
    if not exception_id or not exception_id.strip():
        raise ResolutionError("exception_id is required")
    if not resolved_by or not resolved_by.strip():
        raise ResolutionError("resolved_by is required")
    if not resolved_at or not resolved_at.strip():
        raise ResolutionError("resolved_at is required (explicit ISO-8601, never wall-clock)")
    if not rationale or not rationale.strip():
        raise ResolutionError("rationale is required (audit trail, even though no rule ever reads it)")
    if resolution_type in NON_RULE_TYPES and evidence:
        raise ResolutionError(
            f"{resolution_type} is a first-class outcome, not a pattern (law L13) -- "
            "it must not carry a feature-space predicate"
        )
    if resolution_type not in NON_RULE_TYPES and not evidence:
        raise ResolutionError(
            "a pattern resolution needs at least one evidence/predicate field over the matcher's "
            "own feature space (law L11) -- use manual_one_off or no_pattern for a resolution with no pattern"
        )
    rid = resolution_id_for(exception_id, resolution_type, evidence, rationale, resolved_by, resolved_at)
    return Resolution(rid, exception_id, resolution_type, dict(evidence), rationale, resolved_by, resolved_at)
