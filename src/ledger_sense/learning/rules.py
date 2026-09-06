"""``rules.json`` -- the only artifact a promoted rule may ever live in
(spec §7.3/§7.4).

Candidates (produced by the ``resolve`` CLI) live in a *separate* file this
module also owns (``rule_candidates.json`` by default) so that ``resolve``
itself never touches ``rules.json`` -- only :func:`promote` writes it, and
only on an explicit, exact ``"yes-always"`` confirmation (law L14). Every
promoted record carries both its own ``rule_id`` and the ``resolution_id``
of the human resolution that produced it (acceptance #1).
"""

import hashlib
import json
from pathlib import Path

from .predicate import evaluate_predicate, render_english
from .resolution import NON_RULE_TYPES, Resolution

RULES_SCHEMA_VERSION = 1


class RuleError(ValueError):
    """A promote/candidate operation was refused."""


def rule_id_for(resolution_id: str) -> str:
    digest = hashlib.sha256(resolution_id.encode("utf-8")).hexdigest()
    return f"RULE-{digest[:12]}"


def candidate_rule(resolution: Resolution, support_count: int) -> dict:
    """Build the candidate record ``resolve`` prints and persists. Never
    touches disk itself -- the CLI decides where candidates live."""
    if resolution.resolution_type in NON_RULE_TYPES:
        raise RuleError(f"{resolution.resolution_type} is never a candidate rule (law L13)")
    return {
        "rule_id": rule_id_for(resolution.resolution_id),
        "resolution_id": resolution.resolution_id,
        "resolution_type": resolution.resolution_type,
        "predicate": dict(resolution.evidence),
        "rationale": resolution.rationale,
        "resolved_by": resolution.resolved_by,
        "resolved_at": resolution.resolved_at,
        "plain_english": render_english(resolution.evidence),
        "support_count": support_count,
        "status": "candidate",
    }


def load_candidates(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("candidates", [])


def save_candidates(path, candidates: list) -> None:
    Path(path).write_text(
        json.dumps({"candidates": candidates}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_rules(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("rules", [])


def save_rules(path, rule_list: list) -> None:
    Path(path).write_text(
        json.dumps({"schema_version": RULES_SCHEMA_VERSION, "rules": rule_list}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def promote(
    candidate: dict,
    *,
    promoted_by: str,
    promoted_at: str,
    confirm: str,
    rules_path,
    candidates_path,
    candidates: list,
) -> dict:
    """The only function that writes ``rules.json``.

    ``confirm`` must be the exact literal string ``"yes-always"`` -- any
    other value, including an empty string, is refused (law L14: an
    explicit human "yes, always", never a corroboration count).
    """
    if confirm != "yes-always":
        raise RuleError("--confirm must be exactly 'yes-always'")
    if candidate["resolution_type"] in NON_RULE_TYPES:
        raise RuleError(f"{candidate['resolution_type']} is never promoted to a rule (law L13)")
    if not candidate.get("predicate"):
        raise RuleError("refusing to promote a rule with an empty predicate (law L11)")
    existing = load_rules(rules_path)
    if any(r["rule_id"] == candidate["rule_id"] for r in existing):
        raise RuleError(f"{candidate['rule_id']} is already promoted")
    record = {
        "rule_id": candidate["rule_id"],
        "resolution_id": candidate["resolution_id"],
        "resolution_type": candidate["resolution_type"],
        "predicate": candidate["predicate"],
        "rationale": candidate["rationale"],
        "resolved_by": candidate["resolved_by"],
        "resolved_at": candidate["resolved_at"],
        "promoted_by": promoted_by,
        "promoted_at": promoted_at,
        "support_count_at_promotion": candidate["support_count"],
        "plain_english": candidate["plain_english"],
    }
    existing.append(record)
    save_rules(rules_path, existing)
    remaining = [c for c in candidates if c["rule_id"] != candidate["rule_id"]]
    save_candidates(candidates_path, remaining)
    return record


def matching_rule(rule_list: list, features: dict) -> dict:
    """First rule (in file order) whose predicate matches ``features``, or
    ``None``. First-hit, deterministic -- promotion order decides precedence
    for two rules that could both fire on the same line."""
    return next((rule for rule in rule_list if evaluate_predicate(rule["predicate"], features)), None)
