"""Strict two-input CSV boundary and the exact §5.8 output column order."""

import csv
from pathlib import Path

from ledger_sense.data.io_csv import write_csv
from ledger_sense.data.models import BANK_COLUMNS, LEDGER_COLUMNS, BankTransaction, LedgerEntry
from ledger_sense.data.money import to_money

from .engine import match

OUTCOME_COLUMNS = [
    "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin", "reason",
    "reason_detail", "matched_amount", "residual_after", "candidates", "features",
    "llm_model", "llm_confidence", "llm_is_stub",
]
SETTLEMENT_COLUMNS = [
    "ledger_id", "ledger_amount", "matched_amount", "residual", "n_parts", "bank_txn_ids",
    "fully_settled", "reason",
]


def read_records(path, model, columns):
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != columns:
            raise ValueError(f"Unexpected {model.__name__} columns in {path}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row in {path}")
            row["amount"] = to_money(row["amount"])
            if not row["amount"].is_finite():
                raise ValueError("Amounts must be finite")
            yield model(**row)


def run(ledger, bank, out_dir, adjudicator=None):
    result = match(read_records(ledger, LedgerEntry, LEDGER_COLUMNS),
                   read_records(bank, BankTransaction, BANK_COLUMNS), adjudicator)
    output = Path(out_dir)
    write_csv(str(output / "match_outcomes.csv"), OUTCOME_COLUMNS, result.outcomes)
    write_csv(str(output / "ledger_settlements.csv"), SETTLEMENT_COLUMNS, result.settlements)
    return result
