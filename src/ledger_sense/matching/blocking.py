"""Bounded §5.2 blocking: never fall back to a full-ledger search."""

from collections import defaultdict
from typing import Iterable

from ledger_sense.data.models import BankTransaction, LedgerEntry
from ledger_sense.data.money import cents

from .scoring import plausible_partial, squash


class CandidateIndex:
    def __init__(self, entries: Iterable[LedgerEntry]):
        self.entries = {}
        self.amounts = {}
        self.by_ref = defaultdict(set)
        self.by_key4 = defaultdict(set)
        self.by_bucket = defaultdict(set)
        for entry in entries:
            id = entry.ledger_id
            if id in self.entries:
                raise ValueError(f"Duplicate ledger_id: {id}")
            self.entries[id] = entry
            self.amounts[id] = cents(entry.amount)
            ref, key = squash(entry.reference), squash(entry.counterparty_name)[:4]
            if ref:
                self.by_ref[ref].add(id)
            if key:
                self.by_key4[key].add(id)
            self.by_bucket[abs(self.amounts[id]) // 100].add(id)

    def candidates(self, bank: BankTransaction) -> list[LedgerEntry]:
        amount = cents(bank.amount)
        key_ids = self.by_key4.get(squash(bank.counterparty_name_raw)[:4], set())
        ids = set(self.by_ref.get(squash(bank.reference_raw), ()))
        bucket = abs(amount) // 100
        for value in range(bucket - 4, bucket + 5):
            ids.update(key_ids.intersection(self.by_bucket.get(value, ())))
        if not ids:
            ids.update(id for id in key_ids if plausible_partial(self.amounts[id], amount))
        closest = sorted(ids, key=lambda id: (abs(abs(self.amounts[id]) - abs(amount)), id))[:40]
        return [self.entries[id] for id in closest]
