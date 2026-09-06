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
protocol -- every offline test in ``tests/test_dodo_source.py`` /
``tests/test_dodo_pairing.py`` passes a fake/mock implementation. The real
transport (``DodoSandboxClient``) is otherwise only ever constructed by the
CLI (``cli.py::main``) once a Dodo pull has actually been requested and
configured. The sole sanctioned exception is
``tests/test_dodo_source.py``'s single ``@pytest.mark.slow`` real-sandbox
test (W16), which is skipped whenever a real ``DODO_API_KEY`` isn't
configured and never runs as part of the default/CI suite.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Protocol

from .dodo_pairing import pair_dataset
from .models import BankTransaction
from .money import from_cents

DODO_SANDBOX_BASE_URL = "https://test.dodopayments.com"

# The 403 W14's live smoke test hit was *not* a wrong auth header, base URL,
# or endpoint path -- all three were already correct (confirmed directly
# against Dodo's real API reference at docs.dodopayments.com/api-reference,
# and against the official `dodopayments-python` SDK source: `Authorization:
# Bearer <key>` against `{base_url}/payments`, `base_url` = this exact
# string for `test_mode`). The real cause: Dodo's sandbox sits behind
# Cloudflare, which rejects `urllib.request`'s default `Python-urllib/x.y`
# User-Agent outright (Cloudflare error 1010, "Access denied") before the
# request ever reaches Dodo. A normal-looking `User-Agent` fixes it -- a
# real sandbox key against this exact URL/path/header returns a real 200.
_USER_AGENT = "ledger-sense-dodo-source/1.0 (+https://github.com/himanshu-thakur-7/Ledger-Sense)"

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
    """Raised by ``DodoSandboxClient`` on a transport-level failure (a
    configured-but-failing key, an HTTP error, exhausted retries, ...).

    ``cli.py::main`` catches this the same way it catches
    ``DodoNotConfiguredError`` -- a clean nonzero exit with a one-line
    stderr message, never a stack trace (law L18). The mocked test suite
    (law L20) only ever raises this via a fake ``DodoClient`` to exercise
    that CLI handling; ``DodoSandboxClient`` itself (the only thing that can
    raise it against a real HTTP response) is exercised only by the
    opt-in ``@pytest.mark.slow`` real-sandbox test.
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


def _describe_http_error(exc: urllib.error.HTTPError) -> str:
    """One-line, bounded description of an HTTP error response.

    Best-effort reads the response body (Dodo/Cloudflare error pages
    identify themselves there, e.g. ``"error code: 1010"``) so a
    ``DodoAPIError`` message is actually diagnostic -- never the API key
    itself, which this never touches.
    """
    body = ""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        body = " ".join(raw.split())[:200]
    except Exception:
        pass
    description = f"HTTP {exc.code} {exc.reason}"
    return f"{description} -- {body}" if body else description


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
        # Dodo sandbox, by a human or tests/test_dodo_source.py's
        # @pytest.mark.slow real-sandbox test, outside of the mocked suite.
        url = f"{self.base_url}/payments"
        if cursor:
            url = f"{url}?cursor={cursor}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="GET",
        )
        last_error: object = None
        payload = None
        attempts = 0
        for attempt in range(self.max_retries):
            attempts = attempt + 1
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                last_error = _describe_http_error(exc)
                if 400 <= exc.code < 500:
                    # A client error (bad/expired key, wrong request shape)
                    # won't fix itself on retry -- fail fast instead of
                    # burning the full bounded-retry budget on it.
                    break
            except Exception as exc:  # transient network failure -- bounded retry (law L22)
                last_error = exc
        if payload is None:
            raise DodoAPIError(
                f"Dodo sandbox list_transactions failed after {attempts} attempt(s): {last_error}"
            )
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


# Default location for a previously captured, labeled snapshot of a real
# Dodo sandbox page -- checked into the repo under tests/fixtures so the
# close desk's "pull" intent has something honest to fall back to when a
# live 401/403 means the real sandbox is unreachable this run (TAPE-1 part
# B). Never invented data: every row here is the exact shape a real
# DodoSandboxClient.list_transactions() response already produces, just
# replayed instead of fetched.
DEFAULT_CACHE_PATH = "tests/fixtures/dodo_sandbox_cache.json"

# Matches the HTTP status code out of DodoAPIError's own message shape
# (`_describe_http_error`'s "HTTP <code> <reason> -- ..."), so a caller can
# tell a 401/403 apart from a transient 5xx/timeout without re-parsing the
# whole message by hand.
_HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})\b")


def auth_failure_status_code(exc: DodoAPIError) -> Optional[int]:
    """The HTTP status code out of ``exc``'s message, if it looks like an
    auth/permission failure (401/403) -- ``None`` for anything else (a
    transient network error, a 5xx, or a message with no HTTP code at all).

    This is the one distinction the close desk's ``pull`` intent needs to
    decide "fall back to the labeled cache" vs. "just report the failure":
    a 401/403 means the configured key itself is bad/expired/unauthorized,
    which a labeled cache genuinely substitutes for; any other failure
    shape is left to the caller to report as-is (retrying against a stale
    cache would misrepresent a transient outage as a real pull).
    """
    match = _HTTP_STATUS_RE.search(str(exc))
    if not match:
        return None
    code = int(match.group(1))
    return code if code in (401, 403) else None


@dataclass
class CachedDodoClient:
    """Replays a previously captured, clearly-labeled Dodo sandbox page from
    disk -- the ``--source dodo-cache`` fallback path (TAPE-1 part B) for
    when a live pull 401/403s. Structurally satisfies :class:`DodoClient`
    exactly like the real transport or a test's fake one; ``build_dodo_dataset``
    and everything downstream needs zero special-casing to consume it.

    Never invents a payment row: ``cache_path`` must already contain a JSON
    object shaped ``{"items": [<DodoRawTransaction field dict>, ...]}`` --
    the exact wire shape :class:`DodoSandboxClient` itself parses into
    :class:`DodoRawTransaction`. One page, no pagination -- a cache is a
    fixed, already-labeled snapshot, not a live paginated feed.
    """

    cache_path: str = DEFAULT_CACHE_PATH

    def list_transactions(self, *, cursor: Optional[str] = None) -> DodoPage:
        path = Path(self.cache_path)
        if not path.is_file():
            raise DodoAPIError(
                f"dodo-cache requested but no labeled cache found at {self.cache_path} -- "
                "never inventing payment rows; run a live pull once to seed one, or use "
                "--source synthetic"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = [DodoRawTransaction(**item) for item in payload.get("items", [])]
        return DodoPage(transactions=tuple(items), next_cursor=None)


def load_cached_dataset(cache_path: str = DEFAULT_CACHE_PATH, *, seed: int = 0) -> "DodoDataset":
    """Build a full :class:`DodoDataset` from the labeled cache instead of a
    live pull -- same pull-then-synthesize pipeline as :func:`build_dodo_dataset`,
    just fed by :class:`CachedDodoClient` instead of :class:`DodoSandboxClient`.
    """
    return build_dodo_dataset(CachedDodoClient(cache_path), seed=seed)


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
