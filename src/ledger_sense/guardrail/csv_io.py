"""Guardrail's own CSV boundary.

Reads exactly four files -- ``ledger.csv``, ``bank.csv``, ``match_outcomes.csv``,
``ledger_settlements.csv`` -- and never the ground-truth file (law L2; see
``tests/test_guardrail_isolation.py``). The two Agent-1-output column lists
below are guardrail's own copy of spec §5.8's exact header order; they are
hardcoded here rather than imported from ``ledger_sense.matching.io`` so this
package never imports matching internals.
"""

import csv
import json
from pathlib import Path

from ledger_sense.data.io_csv import write_csv
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS, BankTransaction, LedgerEntry
from ledger_sense.data.money import to_money

# Spec §5.8 -- Agent 1's exact output contract. Copied, not imported.
OUTCOME_COLUMNS = [
    "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin", "reason",
    "reason_detail", "matched_amount", "residual_after", "candidates", "features",
    "llm_model", "llm_confidence", "llm_is_stub",
]
SETTLEMENT_COLUMNS = [
    "ledger_id", "ledger_amount", "matched_amount", "residual", "n_parts", "bank_txn_ids",
    "fully_settled", "reason",
]

# This card's own §8.2 output contract.
RELEASE_COLUMNS = [
    "bank_txn_id", "verdict", "primary_rule", "all_firing_rules", "reason",
    "upstream_context", "required_approvals", "policy_version",
]
AUDIT_COLUMNS = ["bank_txn_id", "rule", "verdict", "detail", "policy_version"]
HELD_COLUMNS = [
    "bank_txn_id", "ledger_id", "amount", "currency", "value_date",
    "primary_rule", "reason", "required_approvals", "policy_version",
]

LIST_SEP = "|"


def read_ledger(path):
    """Read ``ledger.csv`` into a ``{ledger_id: LedgerEntry}`` map."""
    return {row.ledger_id: row for row in _read_typed(path, LedgerEntry, LEDGER_COLUMNS)}


def read_bank(path):
    """Read ``bank.csv`` in file order (order is what makes release_decisions.csv deterministic)."""
    return list(_read_typed(path, BankTransaction, BANK_COLUMNS))


def _read_typed(path, model, columns):
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != columns:
            raise ValueError(f"Unexpected {model.__name__} columns in {path}: {reader.fieldnames}")
        for row in reader:
            if any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row in {path}")
            row["amount"] = to_money(row["amount"])
            yield model(**row)


def read_outcomes(path):
    """Read ``match_outcomes.csv`` into a ``{bank_txn_id: row_dict}`` map. Raw strings, no parsing."""
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != OUTCOME_COLUMNS:
            raise ValueError(f"Unexpected match_outcomes.csv columns: {reader.fieldnames}")
        return {row["bank_txn_id"]: row for row in reader}


def read_settlements(path):
    """Read ``ledger_settlements.csv`` into a ``{ledger_id: row_dict}`` map, ``bank_txn_ids`` decoded."""
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != SETTLEMENT_COLUMNS:
            raise ValueError(f"Unexpected ledger_settlements.csv columns: {reader.fieldnames}")
        settlements = {}
        for row in reader:
            row = dict(row)
            row["bank_txn_ids"] = tuple(json.loads(row["bank_txn_ids"])) if row["bank_txn_ids"] else ()
            settlements[row["ledger_id"]] = row
        return settlements


def render_list(values) -> str:
    return LIST_SEP.join(values)


def write_release_decisions(path, rows):
    return write_csv(str(path), RELEASE_COLUMNS, rows)


def write_audit(path, rows):
    return write_csv(str(path), AUDIT_COLUMNS, rows)


def write_held_settlements(path, rows):
    return write_csv(str(path), HELD_COLUMNS, rows)


def write_policy_applied(path, policy_applied: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(policy_applied, fh, indent=2, sort_keys=True)
        fh.write("\n")
