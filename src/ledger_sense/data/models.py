"""Row schemas for the three §4.1 tables.

Columns are listed in the exact order the spec's tables give them; ``*_COLUMNS``
constants below are the single source of truth for CSV header order.
"""

from dataclasses import dataclass, fields
from decimal import Decimal

from .money import money_str


@dataclass(frozen=True)
class LedgerEntry:
    ledger_id: str
    booked_at: str
    amount: Decimal
    currency: str
    entry_type: str
    counterparty_id: str
    counterparty_name: str
    reference: str
    memo: str
    account_code: str
    source_system: str

    def to_row(self):
        return {
            "ledger_id": self.ledger_id,
            "booked_at": self.booked_at,
            "amount": money_str(self.amount),
            "currency": self.currency,
            "entry_type": self.entry_type,
            "counterparty_id": self.counterparty_id,
            "counterparty_name": self.counterparty_name,
            "reference": self.reference,
            "memo": self.memo,
            "account_code": self.account_code,
            "source_system": self.source_system,
        }


@dataclass(frozen=True)
class BankTransaction:
    bank_txn_id: str
    value_date: str
    amount: Decimal
    currency: str
    counterparty_name_raw: str
    reference_raw: str
    description: str
    bank_account: str
    statement_id: str
    direction: str

    def to_row(self):
        return {
            "bank_txn_id": self.bank_txn_id,
            "value_date": self.value_date,
            "amount": money_str(self.amount),
            "currency": self.currency,
            "counterparty_name_raw": self.counterparty_name_raw,
            "reference_raw": self.reference_raw,
            "description": self.description,
            "bank_account": self.bank_account,
            "statement_id": self.statement_id,
            "direction": self.direction,
        }


@dataclass(frozen=True)
class MatchLink:
    ledger_id: str
    bank_txn_id: str
    relation: str
    defect: str
    case_id: str
    note: str

    def to_row(self):
        return {
            "ledger_id": self.ledger_id,
            "bank_txn_id": self.bank_txn_id,
            "relation": self.relation,
            "defect": self.defect,
            "case_id": self.case_id,
            "note": self.note,
        }


LEDGER_COLUMNS = [f.name for f in fields(LedgerEntry)]
BANK_COLUMNS = [f.name for f in fields(BankTransaction)]
MATCH_LINK_COLUMNS = [f.name for f in fields(MatchLink)]

OVERLAY_NOTE_PREFIX = "overlay:"
