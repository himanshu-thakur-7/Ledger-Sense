"""Pass-2 rule application (spec §7.4): the file-level hand-off that inserts
learned rules between Agent 1's cheap tier and Agent 2's routing escalate.

This module never edits ``ledger_sense.matching`` or ``ledger_sense.routing``.
It reads their exact §5.8/§8's-input schemas (``ledger_sense.learning.io``),
evaluates each escalated bank line against ``rules.json`` with this
package's own predicate evaluator (``predicate.py``), and -- for a line that
both matches a rule's predicate AND clears the guardrail veto
(``guardrail_check.veto``) -- writes back an outcome row that routing's own
``build_subjects`` treats exactly like a cheap-tier match (``status ==
"matched"``), plus an updated ledger-settlement row, using the exact §5.8
schemas unmodified so routing's strict column check still passes. Every
touched row is *also* recorded, alongside the rule/resolution ids that
produced it, in a separate ``rule_hits.csv`` this package owns -- the trace
from an auto-resolve back to the human resolution that taught it survives
even though routing's own two files have no room for a new column.

Only *escalated* lines are ever offered to a rule (spec: "lines that would
have escalated are checked against rules.json first"); a line already
``rejected`` (score < 45 / no candidate) never had a clean-enough signal for
Agent 1 itself to ask a question about, and is out of scope here. A line
whose ``relation`` is already ``duplicate`` is skipped outright: Agent 4's
``would_block_or_hold`` cannot evaluate ``duplicate_release`` from one line
(documented limitation), so a learned rule must never be the thing that
decides a duplicate is safe to release.
"""

import json
from dataclasses import dataclass
from decimal import Decimal

from ledger_sense.data.money import cents, from_cents, money_str

from .guardrail_check import veto
from .rules import matching_rule

RULE_HIT_COLUMNS = [
    "bank_txn_id", "ledger_id", "rule_id", "resolution_id", "resolution_type",
    "applied_cents", "guardrail_verdict", "predicate",
]


@dataclass
class ApplyResult:
    outcomes: list
    settlements: list
    hits: list
    considered: int = 0
    vetoed: int = 0
    no_capacity: int = 0
    rules_loaded: int = 0
    escalated_seen: int = 0


def _settlement_state(row: dict) -> dict:
    return {
        "ledger_amount": row["ledger_amount"],
        "matched_amount": row["matched_amount"],
        "residual": row["residual"],
        "n_parts": int(row["n_parts"]),
        "bank_txn_ids": json.loads(row["bank_txn_ids"]),
        "fully_settled": row["fully_settled"] == "True",
        "reason": row["reason"],
    }


def _settlement_row(ledger_id: str, state: dict) -> dict:
    return {
        "ledger_id": ledger_id,
        "ledger_amount": state["ledger_amount"],
        "matched_amount": state["matched_amount"],
        "residual": state["residual"],
        "n_parts": str(state["n_parts"]),
        "bank_txn_ids": json.dumps(state["bank_txn_ids"]),
        "fully_settled": str(state["fully_settled"]),
        "reason": state["reason"],
    }


def apply_rules(
    outcome_rows: list,
    settlement_rows: list,
    rule_list: list,
    ledger_rows: dict,
    bank_rows: dict,
    as_of: str,
    policy=None,
    period_start: str = None,
    period_end: str = None,
) -> ApplyResult:
    """Pure function: no file I/O (that lives in ``cli.py``), so it is easy
    to exercise directly in tests and to re-run against a "no rules" control
    for the same input (acceptance #6)."""
    settlements = {row["ledger_id"]: _settlement_state(row) for row in settlement_rows}
    outcomes_by_id = {row["bank_txn_id"]: dict(row) for row in outcome_rows}
    order = [row["bank_txn_id"] for row in outcome_rows]

    hits = []
    considered = vetoed = no_capacity = escalated_seen = 0

    for bank_txn_id in order:
        row = outcomes_by_id[bank_txn_id]
        if row["status"] != "escalated" or row["relation"] == "duplicate" or not row["ledger_id"]:
            continue
        escalated_seen += 1
        features = json.loads(row["features"]) if row["features"] else {}
        rule = matching_rule(rule_list, features)
        if rule is None:
            continue
        considered += 1

        ledger_id = row["ledger_id"]
        state = settlements.get(ledger_id)
        residual_cents = abs(cents(Decimal(state["residual"]))) if state else 0
        if state is None or state["fully_settled"] or residual_cents == 0:
            no_capacity += 1
            continue

        ledger_row = ledger_rows.get(ledger_id, {})
        bank_row = bank_rows.get(bank_txn_id, {})
        verdict = veto(outcome_row=row, ledger_row=ledger_row, bank_row=bank_row, as_of=as_of, policy=policy,
                       period_start=period_start, period_end=period_end)
        if verdict != "allow":
            vetoed += 1
            continue

        sign = -1 if cents(Decimal(state["ledger_amount"])) < 0 else 1
        applied_cents = residual_cents  # a learned rule fully closes the book on the explained gap
        new_matched_cents = cents(Decimal(state["matched_amount"])) + sign * applied_cents
        state["matched_amount"] = money_str(from_cents(new_matched_cents))
        state["residual"] = money_str(from_cents(0))
        state["n_parts"] += 1
        state["bank_txn_ids"] = state["bank_txn_ids"] + [bank_txn_id]
        state["fully_settled"] = True
        state["reason"] = "fully_settled"

        row["status"] = "matched"
        row["relation"] = "partial" if row["relation"] == "partial" else "exact"
        row["reason"] = "resolved_by_rule"
        row["reason_detail"] = f"resolved_by_rule={rule['rule_id']};resolution_id={rule['resolution_id']}"
        row["residual_after"] = state["residual"]

        hits.append(
            {
                "bank_txn_id": bank_txn_id,
                "ledger_id": ledger_id,
                "rule_id": rule["rule_id"],
                "resolution_id": rule["resolution_id"],
                "resolution_type": rule["resolution_type"],
                "applied_cents": str(applied_cents),
                "guardrail_verdict": verdict,
                "predicate": json.dumps(rule["predicate"], sort_keys=True),
            }
        )

    outcomes = [outcomes_by_id[bank_txn_id] for bank_txn_id in order]
    settlement_out = [_settlement_row(row["ledger_id"], settlements[row["ledger_id"]]) for row in settlement_rows]
    return ApplyResult(
        outcomes=outcomes,
        settlements=settlement_out,
        hits=hits,
        considered=considered,
        vetoed=vetoed,
        no_capacity=no_capacity,
        rules_loaded=len(rule_list),
        escalated_seen=escalated_seen,
    )
