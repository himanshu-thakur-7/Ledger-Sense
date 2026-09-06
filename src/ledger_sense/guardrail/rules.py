"""The five guardrail rules (spec §8.1) -- exact policy book, no extra rules.

Each rule function takes a fully-built line context (see ``engine.build_line``)
and the loaded policy, and returns ``None`` (does not fire) or a small dict
describing why it fired: ``{"verdict": "block"|"hold", "detail": str}``.

Rule order is fixed and is the tie-break for ``primary_rule`` when several
rules fire at the same verdict level (spec: "block beats hold beats allow";
within a level we need our own deterministic pick, so we use policy-book
order, always this same order).
"""

from decimal import Decimal

from .normalize import contains_token_sequence, normalize_tokens
from .period import in_period

RULE_ORDER = ("denied_party", "duplicate_release", "dual_control", "out_of_period", "upstream_veto")

# Agent 1's §5.6 guardrail-interlock reasons, and the release-severity a
# human compliance reviewer would assign to each if they were looking at it
# directly (spec: "block if they'd have blocked, hold if held"). Agent 1
# itself never emits a block/hold verdict -- it only ever escalates -- so this
# mapping is guardrail's own judgment call, documented in the PR: a
# zero/flipped-sign amount is exactly the §4.2 "guardrail bait" case (money
# that must not move at all -> block); a currency mismatch is a data-quality
# problem that can still be resolved with a second look -> hold.
UPSTREAM_INTERLOCK_SEVERITY = {
    "anomalous_amount": "block",
    "currency_conflict": "hold",
}


def rule_denied_party(line: dict, policy: dict):
    candidates = [line.get("bank_counterparty_tokens", []), line.get("ledger_counterparty_tokens", [])]
    ids = {v for v in (line.get("counterparty_id"),) if v}
    for entry in policy["denied_parties"]:
        entry_id = entry.get("id")
        if entry_id and entry_id in ids:
            return {"verdict": "block", "detail": f"counterparty_id matches denied-party entry id '{entry_id}'"}
        entry_tokens = normalize_tokens(entry["name"])
        for tokens in candidates:
            if contains_token_sequence(tokens, entry_tokens):
                return {"verdict": "block", "detail": f"counterparty name matches denied-party entry '{entry['name']}'"}
    return None


def rule_duplicate_release(line: dict, policy: dict):
    sibling = line.get("duplicate_sibling")
    if sibling is None:
        return None
    return {"verdict": "block", "detail": f"independently re-detected duplicate fingerprint, sibling {sibling}"}


def rule_dual_control(line: dict, policy: dict):
    threshold = Decimal(policy["dual_control_threshold"])
    if abs(line["amount"]) >= threshold:
        return {"verdict": "hold", "detail": f"abs(amount)={line['amount']} >= dual-control threshold {threshold}"}
    return None


def rule_out_of_period(line: dict, policy: dict):
    period_start, period_end = line["period"]
    if not in_period(line["value_date"], period_start, period_end):
        return {
            "verdict": "hold",
            "detail": (
                f"value_date {line['value_date'].isoformat()} outside period "
                f"[{period_start.isoformat()}, {period_end.isoformat()})"
            ),
        }
    return None


def rule_upstream_veto(line: dict, policy: dict):
    reason = line.get("upstream_reason")
    severity = UPSTREAM_INTERLOCK_SEVERITY.get(reason)
    if severity is None:
        return None
    return {
        "verdict": severity,
        "detail": f"Agent 1's §5.6 interlock already flagged this line ({reason}); carrying forward as {severity}",
    }


RULES = {
    "denied_party": rule_denied_party,
    "duplicate_release": rule_duplicate_release,
    "dual_control": rule_dual_control,
    "out_of_period": rule_out_of_period,
    "upstream_veto": rule_upstream_veto,
}


def evaluate_line(line: dict, policy: dict) -> dict:
    """Run every rule against ``line``. Returns ``{rule_name: {"verdict":..., "detail":...}}`` for firing rules only."""
    firing = {}
    for name in RULE_ORDER:
        result = RULES[name](line, policy)
        if result is not None:
            firing[name] = result
    return firing


def resolve_verdict(firing: dict):
    """Apply "block beats hold beats allow" and pick a deterministic primary rule.

    Returns ``(verdict, primary_rule)``; ``primary_rule`` is ``None`` for ``allow``.
    """
    for verdict in ("block", "hold"):
        for name in RULE_ORDER:
            if name in firing and firing[name]["verdict"] == verdict:
                return verdict, name
    return "allow", None
