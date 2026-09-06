"""Orchestrates the guardrail run: builds one line context per bank line, applies
the five §8.1 rules, and produces the four §8.2 output tables.
"""

from dataclasses import dataclass

from ledger_sense.data.money import cents, money_str, to_money

from . import csv_io
from .duplicates import find_duplicate_releases
from .normalize import normalize_tokens
from .period import parse_instant, resolve_period
from .policy import load_policy
from .rules import RULE_ORDER, RULES, evaluate_line, resolve_verdict


@dataclass(frozen=True)
class GuardrailResult:
    decisions: list
    audit: list
    held: list
    policy_applied: dict
    verdict_counts: dict


def _approvals_for(primary_rule, verdict, policy):
    if verdict != "hold" or primary_rule is None:
        return ()
    key = "upstream_veto_hold" if primary_rule == "upstream_veto" else primary_rule
    return tuple(policy["required_approvals"].get(key, ()))


def _upstream_context(outcome):
    if outcome is None:
        return "no match_outcomes row found for this bank_txn_id"
    return (
        f"match_outcomes: status={outcome['status']} tier={outcome['tier']} "
        f"relation={outcome['relation']} reason={outcome['reason']}"
    )


def build_lines(ledger, bank_rows, outcomes, period_start, period_end):
    """Build one evaluable line context per bank row, in bank.csv's own order."""
    enriched_bank = []
    for row in bank_rows:
        enriched_bank.append({
            "bank_txn_id": row.bank_txn_id,
            "currency": row.currency,
            "reference_raw": row.reference_raw,
            "value_date": row.value_date,
            "_amount_cents": cents(row.amount),
        })
    matched_ids = frozenset(txn_id for txn_id, o in outcomes.items() if o["status"] == "matched")
    duplicate_map = find_duplicate_releases(enriched_bank, matched_ids)

    lines = []
    for row in bank_rows:
        outcome = outcomes.get(row.bank_txn_id)
        ledger_id = outcome["ledger_id"] if outcome else ""
        ledger_entry = ledger.get(ledger_id) if ledger_id else None
        lines.append({
            "bank_txn_id": row.bank_txn_id,
            "amount": row.amount,
            "currency": row.currency,
            "value_date": parse_instant(row.value_date),
            "value_date_raw": row.value_date,
            "bank_counterparty_tokens": normalize_tokens(row.counterparty_name_raw),
            "ledger_counterparty_tokens": normalize_tokens(ledger_entry.counterparty_name) if ledger_entry else [],
            "counterparty_id": ledger_entry.counterparty_id if ledger_entry else None,
            "ledger_id": ledger_id,
            "duplicate_sibling": duplicate_map.get(row.bank_txn_id),
            "period": (period_start, period_end),
            "upstream_reason": outcome["reason"] if outcome else None,
            "_outcome": outcome,
        })
    return lines


def decide(line, policy):
    """Evaluate one line context. Returns ``(verdict, primary_rule, firing)``."""
    firing = evaluate_line(line, policy)
    verdict, primary_rule = resolve_verdict(firing)
    return verdict, primary_rule, firing


def run(ledger_path, bank_path, outcomes_path, settlements_path, as_of, out_dir,
        policy_path=None, period_start=None, period_end=None):
    """Run the full guardrail pass and write the four §8.2 output files."""
    policy = load_policy(policy_path)
    ledger = csv_io.read_ledger(ledger_path)
    bank_rows = csv_io.read_bank(bank_path)
    outcomes = csv_io.read_outcomes(outcomes_path)
    settlements = csv_io.read_settlements(settlements_path)  # read for the §8's input contract; see PR notes
    resolved_start, resolved_end = resolve_period(as_of, period_start, period_end)

    lines = build_lines(ledger, bank_rows, outcomes, resolved_start, resolved_end)

    decisions, audit, held = [], [], []
    verdict_counts = {"allow": 0, "hold": 0, "block": 0}
    policy_version = policy["policy_version"]

    for line in lines:
        verdict, primary_rule, firing = decide(line, policy)
        verdict_counts[verdict] += 1
        firing_names = [name for name in RULE_ORDER if name in firing]
        reason = firing[primary_rule]["detail"] if primary_rule else "no guardrail rule fired"
        approvals = _approvals_for(primary_rule, verdict, policy)

        decisions.append({
            "bank_txn_id": line["bank_txn_id"],
            "verdict": verdict,
            "primary_rule": primary_rule or "",
            "all_firing_rules": csv_io.render_list(firing_names),
            "reason": reason,
            "upstream_context": _upstream_context(line["_outcome"]),
            "required_approvals": csv_io.render_list(approvals),
            "policy_version": policy_version,
        })

        for name in firing_names:
            audit.append({
                "bank_txn_id": line["bank_txn_id"],
                "rule": name,
                "verdict": firing[name]["verdict"],
                "detail": firing[name]["detail"],
                "policy_version": policy_version,
            })

        if verdict == "hold":
            held.append({
                "bank_txn_id": line["bank_txn_id"],
                "ledger_id": line["ledger_id"],
                "amount": money_str(line["amount"]),
                "currency": line["currency"],
                "value_date": line["value_date_raw"],
                "primary_rule": primary_rule or "",
                "reason": reason,
                "required_approvals": csv_io.render_list(approvals),
                "policy_version": policy_version,
            })

    policy_applied = {
        "policy_version": policy_version,
        "policy_source": policy.get("source_path"),
        "denied_parties": policy["denied_parties"],
        "dual_control_threshold": policy["dual_control_threshold"],
        "required_approvals": policy["required_approvals"],
        "as_of": as_of.isoformat(),
        "period_start": resolved_start.isoformat(),
        "period_end": resolved_end.isoformat(),
        "rule_order": list(RULE_ORDER),
        "settlements_rows_read": len(settlements),
    }

    out_dir = str(out_dir)
    csv_io.write_release_decisions(f"{out_dir}/release_decisions.csv", decisions)
    csv_io.write_audit(f"{out_dir}/guardrail_audit.csv", audit)
    csv_io.write_held_settlements(f"{out_dir}/held_settlements.csv", held)
    csv_io.write_policy_applied(f"{out_dir}/policy_applied.json", policy_applied)

    return GuardrailResult(decisions, audit, held, policy_applied, verdict_counts)


def would_block_or_hold(line: dict, candidate_rule=None, policy=None) -> str:
    """Plain function for a future Agent 3 (learning) to call before promoting a rule.

    ``line`` is a plain dict describing a single candidate release, using
    whichever of these keys it can supply:
      - ``counterparty_name`` / ``counterparty_id``
      - ``amount`` (``Decimal`` or a 2-decimal string)
      - ``currency``
      - ``value_date`` (ISO-8601 string) plus either ``as_of`` or both
        ``period_start``/``period_end`` (also ISO-8601 strings) to evaluate
        ``out_of_period``
      - ``upstream_reason`` (an Agent 1 §5.6 interlock reason, if known) to
        evaluate ``upstream_veto``

    ``candidate_rule`` names the learned rule under consideration; it is not
    yet consulted by any policy rule here (reserved for a future extension
    where a candidate rule's own predicate could sharpen ``upstream_veto`` or
    add rule-specific context) but is accepted now so Agent 3's call site
    doesn't need to change shape later.

    NOTE -- ``duplicate_release`` needs the whole bank-line population's
    fingerprints and cannot be evaluated from a single line, so it is never
    checked here. A learned rule that resolves a whole exception *class* one
    line at a time should still be safe from it in practice: duplicate lines
    are, by construction, escalated by Agent 1 and routed as their own
    ``duplicate`` category by Agent 2, not the kind of clean repeat pattern
    Agent 3 promotes rules for. Documented as a known limitation of this API.
    """
    policy = policy or load_policy()
    amount = to_money(line["amount"]) if "amount" in line else None
    period = None
    if line.get("period_start") and line.get("period_end"):
        period = (parse_instant(line["period_start"]), parse_instant(line["period_end"]))
    elif line.get("as_of"):
        period = resolve_period(parse_instant(line["as_of"]))

    ctx = {
        "amount": amount,
        "currency": line.get("currency", ""),
        "value_date": parse_instant(line["value_date"]) if line.get("value_date") else None,
        "bank_counterparty_tokens": normalize_tokens(line.get("counterparty_name", "")),
        "ledger_counterparty_tokens": [],
        "counterparty_id": line.get("counterparty_id"),
        "duplicate_sibling": None,
        "period": period,
        "upstream_reason": line.get("upstream_reason"),
    }

    firing = {}
    for name in RULE_ORDER:
        if name == "duplicate_release":
            continue
        if name == "dual_control" and amount is None:
            continue
        if name == "out_of_period" and (period is None or ctx["value_date"] is None):
            continue
        result = RULES[name](ctx, policy)
        if result is not None:
            firing[name] = result
    verdict, _ = resolve_verdict(firing)
    return verdict
