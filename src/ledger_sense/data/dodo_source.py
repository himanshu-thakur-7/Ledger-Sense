"""Dodo Payments sandbox source (spec: LEDGER-SENSE-v2-PRD.md, W11).

Locked pairing decision (PRD "Locked decisions" #1): **pull-then-synthesize**.
This module pulls existing transactions from Dodo's *sandbox* API first
(read-only, paginated ``list_transactions`` -- never a live/non-sandbox call,
never a payment-creation call), normalizes them into the exact
``BankTransaction`` shape v1's synthetic generator (``data/generator.py``)
already produces, and dedups by Dodo's own transaction id so re-running the
pull is idempotent. Synthesizing a paired ``LedgerEntry`` around each pulled
row (mirroring ``generator.py``'s defect-injection logic) is
``dodo_pairing.py``'s job -- see that module's docstring for the reduced
defect taxonomy this pull-fixed/ledger-synthesized direction supports.

Config gate (law L18): ``LEDGER_SENSE_DATA_SOURCE=dodo`` with no
``DODO_API_KEY`` must never attempt a call -- ``ensure_dodo_configured()``
raises ``DodoNotConfiguredError``, which the CLI (``cli.py``) turns into a
clean nonzero exit with a one-line message, never a stack trace.

Zero live network calls in tests (law L20): ``DodoClient`` is a structural
protocol -- every test in ``tests/test_dodo_source.py`` /
``tests/test_dodo_pairing.py`` passes a fake/mock implementation. The real
transport (``DodoSandboxClient``) is only ever constructed by the CLI
(``cli.py::main``) once a Dodo pull has actually been requested and
configured -- nothing in ``tests/`` imports it with real network IO.
"""

from __future__ import annotations

import json
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Protocol

from .dodo_pairing import pair_dataset
from .models import BankTransaction
from .money import from_cents

DODO_SANDBOX_BASE_URL = "https://test.dodopayments.com"

# Defensive bound against a runaway/misbehaving pagination loop (mirrors the
# bounded-retry discipline law L22 requires of every v2 external-API client).
MAX_PAGES = 1000

# Mirrors data/generator.py's BANK_ACCOUNTS_BY_CURRENCY table shape, namespaced
# to Dodo so a Dodo-sourced bank_account can never collide with a synthetic one.
BANK_ACCOUNTS_BY_CURRENCY = {
    "USD": "ACCT-DODO-USD-01",
    "EUR": "ACCT-DODO-EUR-01",
    "GBP": "ACCT-DODO-GBP-01",
}
DEFAULT_BANK_ACCOUNT = "ACCT-DODO-USD-01"

# Arbitrary but fixed anchor, mirroring generator.py's own EPOCH -- only used
# to compute a human-readable, deterministic statement_id week number.
_STATEMENT_EPOCH = datetime(2026, 1, 5, tzinfo=timezone.utc)


class DodoNotConfiguredError(Exception):
    """Raised when ``--source dodo`` is requested but no ``DODO_API_KEY`` is set.

    ``cli.py::main`` catches this specifically and exits cleanly (nonzero, a
    one-line stderr message) -- it must never surface as an uncaught
    stack trace (acceptance 5).
    """


class DodoAPIError(Exception):
    """Raised by ``DodoSandboxClient`` on a transport-level failure.

    Never raised by anything a test constructs -- the real transport is not
    exercised by this repo's test suite (law L20).
    """


@dataclass(frozen=True)
class DodoRawTransaction:
    """One transaction as Dodo's sandbox "list transactions" endpoint returns it.

    Field names/shapes approximate a real Dodo Payments API payment object
    closely enough to normalize correctly; the exact wire schema doesn't
    matter to this module's contract as long as a ``DodoClient`` returns
    these.
    """

    transaction_id: str
    amount_cents: int  # non-negative magnitude, minor units, never float (law L3)
    currency: str
    direction: str  # "credit" | "debit"
    customer_name: str
    reference: str  # "" when Dodo has no reference/metadata for this transaction
    description: str
    created_at: str  # ISO-8601, e.g. "2026-03-01T12:00:00Z"
    status: str = "succeeded"


@dataclass(frozen=True)
class DodoPage:
    transactions: tuple
    next_cursor: Optional[str] = None


class DodoClient(Protocol):
    """Structural protocol every real or fake Dodo transport satisfies.

    Read-only listing only -- there is deliberately no method here (or
    anywhere in this module) that could create, update, or refund a payment.
    """

    def list_transactions(self, *, cursor: Optional[str] = None) -> DodoPage: ...


@dataclass
class DodoSandboxClient:
    """Real transport: read-only ``GET /payments`` against Dodo's *sandbox* API.

    Never instantiated by any test in this repo (law L20) -- only by
    ``cli.py::main``, and only after ``ensure_dodo_configured()`` has already
    confirmed a key exists. Points at ``DODO_SANDBOX_BASE_URL`` unconditionally
    -- this module has no code path that can reach a live/production Dodo
    endpoint.
    """

    api_key: str
    base_url: str = DODO_SANDBOX_BASE_URL
    timeout_seconds: float = 10.0
    max_retries: int = 3  # bounded retries on a transient transport failure (law L22)

    def list_transactions(self, *, cursor: Optional[str] = None) -> DodoPage:  # pragma: no cover
        # Real network IO -- deliberately excluded from coverage of the
        # offline test suite (law L20). Exercised only against the actual
        # Dodo sandbox, by a human, outside of `pytest`.
        url = f"{self.base_url}/payments"
        if cursor:
            url = f"{url}?cursor={cursor}"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.api_key}"}, method="GET"
        )
        last_error: Optional[Exception] = None
        payload = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:  # bounded retry -- never an unbounded loop
                last_error = exc
        if payload is None:
            raise DodoAPIError(
                f"Dodo sandbox list_transactions failed after {self.max_retries} attempts: {last_error}"
            ) from last_error
        items = [
            DodoRawTransaction(
                transaction_id=item["transaction_id"],
                amount_cents=int(item["amount_cents"]),
                currency=item["currency"],
                direction=item["direction"],
                customer_name=item.get("customer_name", ""),
                reference=item.get("reference", ""),
                description=item.get("description", ""),
                created_at=item["created_at"],
                status=item.get("status", "succeeded"),
            )
            for item in payload.get("items", [])
        ]
        return DodoPage(transactions=tuple(items), next_cursor=payload.get("next_cursor"))


def ensure_dodo_configured(config) -> None:
    """Raise ``DodoNotConfiguredError`` unless a Dodo sandbox key is present.

    ``config`` is a ``ledger_sense.config.Config`` -- this is the single L18
    gate every dodo-path caller (the CLI) must check before constructing a
    client or making any call.
    """
    if not config.dodo_enabled():
        raise DodoNotConfiguredError(
            "DODO_API_KEY is not set -- cannot use --source dodo. "
            "Set DODO_API_KEY (and DODO_ENVIRONMENT=sandbox) to pull real "
            "Dodo sandbox data, or omit --source / use --source synthetic "
            "for the default synthetic generator."
        )


def list_all_transactions(client: DodoClient, *, max_pages: int = MAX_PAGES) -> List[DodoRawTransaction]:
    """Paginate through every page ``client`` has, bounded by ``max_pages``."""
    all_txns: List[DodoRawTransaction] = []
    cursor = None
    for _ in range(max_pages):
        page = client.list_transactions(cursor=cursor)
        all_txns.extend(page.transactions)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    else:
        raise DodoAPIError(f"Dodo sandbox pagination exceeded {max_pages} pages -- aborting")
    return all_txns


def _statement_id(value_date: datetime) -> str:
    # Weeks since an arbitrary but fixed epoch, mirroring generator.py's own
    # `_statement_id` shape (`STMT-P{n}-{week}`) but namespaced to Dodo so the
    # two sources can never collide on statement_id.
    week = max((value_date - _STATEMENT_EPOCH).days // 7, 0)
    return f"STMT-DODO-{week:03d}"


def normalize_bank_transaction(raw: DodoRawTransaction) -> BankTransaction:
    """Map one ``DodoRawTransaction`` into the exact ``BankTransaction`` shape.

    Amounts always go through :func:`ledger_sense.data.money.from_cents` --
    never a float at any point (law L3).
    """
    signed_cents = raw.amount_cents if raw.direction == "credit" else -raw.amount_cents
    amount = from_cents(signed_cents)
    value_date_dt = datetime.fromisoformat(raw.created_at.replace("Z", "+00:00"))
    bank_account = BANK_ACCOUNTS_BY_CURRENCY.get(raw.currency.strip().upper(), DEFAULT_BANK_ACCOUNT)
    description = raw.description or f"DODO {raw.direction.upper()} {raw.customer_name}"
    return BankTransaction(
        bank_txn_id=f"BK-DODO-{raw.transaction_id}",
        value_date=value_date_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        amount=amount,
        currency=raw.currency,
        counterparty_name_raw=raw.customer_name,
        reference_raw=raw.reference,
        description=description,
        bank_account=bank_account,
        statement_id=_statement_id(value_date_dt),
        direction=raw.direction,
    )


def dedupe_by_transaction_id(raws: Iterable[DodoRawTransaction]) -> List[DodoRawTransaction]:
    """First-seen-wins dedup by Dodo's own transaction id (idempotent ingestion)."""
    seen = set()
    out: List[DodoRawTransaction] = []
    for raw in raws:
        if raw.transaction_id in seen:
            continue
        seen.add(raw.transaction_id)
        out.append(raw)
    return out


def merge_dedup_by_bank_txn_id(rows: Iterable[BankTransaction]) -> List[BankTransaction]:
    """First-seen-wins dedup of already-normalized rows, by ``bank_txn_id``.

    Since :func:`normalize_bank_transaction` derives ``bank_txn_id``
    deterministically from the Dodo transaction id, this is what makes
    *repeated* pulls (e.g. a second run against unchanged sandbox data)
    idempotent when merged into a previously-written dataset -- acceptance 3.
    """
    seen = set()
    out: List[BankTransaction] = []
    for row in rows:
        if row.bank_txn_id in seen:
            continue
        seen.add(row.bank_txn_id)
        out.append(row)
    return out


def pull_bank_transactions(client: DodoClient) -> List[BankTransaction]:
    """Pull every sandbox transaction, dedup, normalize.

    Pure and idempotent: calling this twice against the same underlying Dodo
    sandbox data always yields the same rows in the same order, with no
    duplicate ``bank_txn_id`` (acceptance 3).
    """
    raws = dedupe_by_transaction_id(list_all_transactions(client))
    return [normalize_bank_transaction(raw) for raw in raws]


@dataclass
class DodoDataset:
    """Output of the full pull-then-synthesize pipeline.

    ``ledger_rows``/``bank_rows``/``match_link_rows`` are lists of dicts (each
    produced by a model's own ``to_row()``) -- byte-for-byte the same shape as
    ``data/generator.py``'s ``GeneratedDataset``, so ``cli.py::write_dataset``
    (and every downstream matching/routing/guardrail/learning/metrics reader)
    needs zero changes to consume either source.
    """

    ledger_rows: List[dict]
    bank_rows: List[dict]
    match_link_rows: List[dict]
    pulled_transaction_count: int
    defect_histogram: "OrderedDict[str, int]"

    def format(self) -> str:
        lines = [
            "Ledger Sense Dodo-sourced generation summary",
            f"  pulled Dodo sandbox transactions: {self.pulled_transaction_count}",
            f"  row counts: ledger.csv={len(self.ledger_rows)} bank.csv={len(self.bank_rows)} "
            f"match_links.csv={len(self.match_link_rows)}",
            "  pairing defect histogram (dodo_pairing.py, reduced §4.2 subset):",
        ]
        for name, count in self.defect_histogram.items():
            lines.append(f"    {name:<18} {count:>7}")
        return "\n".join(lines)


def build_dodo_dataset(client: DodoClient, *, seed: int = 0) -> DodoDataset:
    """Full pull-then-synthesize pipeline: pull -> normalize -> dedupe -> pair.

    ``seed`` controls only the ledger-synthesis half (``dodo_pairing.py``) --
    the pulled bank rows themselves come entirely from ``client`` and are
    never randomized.
    """
    bank_transactions = pull_bank_transactions(client)
    ledger_entries, match_links, histogram = pair_dataset(bank_transactions, seed=seed)
    return DodoDataset(
        ledger_rows=[e.to_row() for e in ledger_entries],
        bank_rows=[b.to_row() for b in bank_transactions],
        match_link_rows=[m.to_row() for m in match_links],
        pulled_transaction_count=len(bank_transactions),
        defect_histogram=histogram,
    )
