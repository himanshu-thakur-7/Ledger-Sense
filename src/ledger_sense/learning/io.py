"""Strict, read-only CSV boundary onto Agent 1's and Agent 2's own output
schemas (§5.8 / §6.7 / §4.1).

Column lists are duplicated here exactly, the same way
``ledger_sense.routing.io`` duplicates Agent 1's schema instead of importing
it -- a header mismatch fails loudly instead of this package silently
misreading a shifted column. Nothing in this module imports
``ledger_sense.matching`` or ``ledger_sense.routing`` (law L1); it only
knows their published column names (task brief: "Read only ... Agent 1/2
output column names").
"""

import csv
import json
from pathlib import Path

OUTCOME_COLUMNS = [
    "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin", "reason",
    "reason_detail", "matched_amount", "residual_after", "candidates", "features",
    "llm_model", "llm_confidence", "llm_is_stub",
]
SETTLEMENT_COLUMNS = [
    "ledger_id", "ledger_amount", "matched_amount", "residual", "n_parts", "bank_txn_ids",
    "fully_settled", "reason",
]
EXCEPTION_COLUMNS = [
    "exception_id", "pass_id", "subject_kind", "bank_txn_id", "ledger_id",
    "category", "classification_detail", "match_status", "match_reason",
    "settlement_reason", "counterparty_key", "counterparty_label",
    "amount", "currency", "severity", "owner_id", "owner_name", "owner_team",
    "assignment_basis", "opened_at", "sla_hours", "due_at",
    "hours_remaining", "sla_state", "sla_display", "queue_position",
    "age_days", "evidence",
]
LEDGER_COLUMNS = [
    "ledger_id", "booked_at", "amount", "currency", "entry_type", "counterparty_id",
    "counterparty_name", "reference", "memo", "account_code", "source_system",
]
BANK_COLUMNS = [
    "bank_txn_id", "value_date", "amount", "currency", "counterparty_name_raw",
    "reference_raw", "description", "bank_account", "statement_id", "direction",
]


def _rows(path, columns) -> list:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != columns:
            raise ValueError(f"Unexpected columns in {path}: {reader.fieldnames}")
        return list(reader)


def read_outcomes(path) -> list:
    """Raw ``match_outcomes.csv`` rows, as plain dicts (strings, un-parsed)."""
    return _rows(path, OUTCOME_COLUMNS)


def read_outcome_features(path) -> dict:
    """``bank_txn_id -> parsed features dict``, for predicate evaluation."""
    return {
        row["bank_txn_id"]: (json.loads(row["features"]) if row["features"] else {})
        for row in _rows(path, OUTCOME_COLUMNS)
    }


def read_settlements(path) -> list:
    return _rows(path, SETTLEMENT_COLUMNS)


def read_exceptions(path) -> list:
    return _rows(path, EXCEPTION_COLUMNS)


def read_ledger(path) -> dict:
    return {row["ledger_id"]: row for row in _rows(path, LEDGER_COLUMNS)}


def read_bank(path) -> dict:
    return {row["bank_txn_id"]: row for row in _rows(path, BANK_COLUMNS)}
