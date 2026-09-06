"""Strict four-input CSV boundary + the exact §6.7 output column order.

Routing reads Agent 1's own output files (``match_outcomes.csv``,
``ledger_settlements.csv``) plus the two original inputs (``ledger.csv``,
``bank.csv``) for the raw counterparty/date/direction facts that Agent 1's
row-per-bank-line schema doesn't carry for the book side. It never reads
the ground-truth MatchLink table and never imports anything from
``ledger_sense.matching`` -- see ``tests/test_routing_isolation.py``.
"""

import csv
import json
from pathlib import Path

from ledger_sense.data.io_csv import write_csv
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS
from ledger_sense.data.money import to_money

from .clock import parse_iso
from .engine import EXCEPTION_COLUMNS, QUEUE_COLUMNS, build_subjects, route

# Agent 1's §5.8 output contracts, duplicated here (not imported -- that
# would be reading ``ledger_sense.matching`` internals) so a header mismatch
# fails loudly instead of routing silently misreading a shifted column.
OUTCOME_COLUMNS = [
    "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin", "reason",
    "reason_detail", "matched_amount", "residual_after", "candidates", "features",
    "llm_model", "llm_confidence", "llm_is_stub",
]
SETTLEMENT_COLUMNS = [
    "ledger_id", "ledger_amount", "matched_amount", "residual", "n_parts", "bank_txn_ids",
    "fully_settled", "reason",
]


def _read_rows(path, columns):
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != columns:
            raise ValueError(f"Unexpected columns in {path}: {reader.fieldnames}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row in {path}")
            yield row


def read_outcomes(path) -> list:
    outcomes = []
    for row in _read_rows(path, OUTCOME_COLUMNS):
        outcomes.append({
            "bank_txn_id": row["bank_txn_id"],
            "status": row["status"],
            "relation": row["relation"],
            "ledger_id": row["ledger_id"],
            "reason": row["reason"],
            "features": json.loads(row["features"]) if row["features"] else {},
        })
    return outcomes


def read_settlements(path) -> dict:
    settlements = {}
    for row in _read_rows(path, SETTLEMENT_COLUMNS):
        settlements[row["ledger_id"]] = {
            "ledger_amount": to_money(row["ledger_amount"]),
            "residual": to_money(row["residual"]),
            "n_parts": int(row["n_parts"]),
            "reason": row["reason"],
        }
    return settlements


def read_ledger(path) -> dict:
    entries = {}
    for row in _read_rows(path, LEDGER_COLUMNS):
        entries[row["ledger_id"]] = {
            "booked_at": row["booked_at"],
            "amount": to_money(row["amount"]),
            "currency": row["currency"],
            "counterparty_name": row["counterparty_name"],
        }
    return entries


def read_bank(path) -> dict:
    transactions = {}
    for row in _read_rows(path, BANK_COLUMNS):
        transactions[row["bank_txn_id"]] = {
            "value_date": row["value_date"],
            "amount": to_money(row["amount"]),
            "currency": row["currency"],
            "counterparty_name_raw": row["counterparty_name_raw"],
            "direction": row["direction"],
        }
    return transactions


def run(outcomes_path, settlements_path, ledger_path, bank_path, as_of: str, out_dir) -> tuple:
    bank_outcomes = read_outcomes(outcomes_path)
    ledger_settlements = read_settlements(settlements_path)
    ledger_rows = read_ledger(ledger_path)
    bank_rows = read_bank(bank_path)

    subjects = build_subjects(bank_outcomes, ledger_settlements, ledger_rows, bank_rows)
    exception_rows, queue_rows = route(subjects, parse_iso(as_of))

    output = Path(out_dir)
    write_csv(str(output / "exceptions.csv"), EXCEPTION_COLUMNS, exception_rows)
    write_csv(str(output / "owner_queues.csv"), QUEUE_COLUMNS, queue_rows)
    return exception_rows, queue_rows
