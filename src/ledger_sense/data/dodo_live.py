"""Real Dodo Payments *sandbox* seeding + a corrected live-read client (LIVE-1).

``dodo_source.py`` is locked read-only for this card (W11's "does not: any
payment creation") -- this is a **new, separate** module. It does two
things ``dodo_source.py`` structurally cannot and must not:

1. **Seeds** real sandbox test transactions (:class:`DodoSeeder`) via
   ``POST /products`` + ``POST /payments`` against
   ``dodo_source.DODO_SANDBOX_BASE_URL`` only -- never a live/production
   endpoint, never anything but a plain test-mode payment object (no card
   is ever entered; Dodo's real API creates the payment object itself on
   ``POST /payments`` regardless of whether it is ever completed with a
   card, and ``dodo_source.py``'s own downstream pipeline never reads a
   payment's ``status`` at all -- see module-level note below).

2. Supplies a **corrected** ``DodoClient``-protocol implementation
   (:class:`RealPaymentsClient`) that ``dodo_source.py``'s own,
   otherwise-unmodified ``build_dodo_dataset()`` / ``pull_bank_transactions()``
   pipeline can consume via ``ledger_sense.data.cli.main()``'s existing
   ``client=`` injection parameter (already public, already there for
   exactly this kind of substitution -- see that module's own docstring:
   "``client`` ... injectable ... so tests never touch the real
   environment").

**Why a second client at all -- a real, verified bug, not a style choice:**
``dodo_source.DodoSandboxClient.list_transactions()`` parses field names
(``transaction_id``, ``amount_cents``, ``direction``, ``customer_name``,
``reference``, ``description``) that do not match Dodo's actual real
``GET /payments`` response shape. Confirmed live, this card, against the
real sandbox API:

    {"items": [{"payment_id": "pay_...", "status": "requires_payment_method",
                "total_amount": 100, "currency": "USD", "payment_method": null,
                "customer": {"customer_id": "...", "name": "...", "email": "...",
                             "phone_number": null, "metadata": {}},
                "created_at": "2026-...Z", "metadata": {...}, ...}]}

-- no ``transaction_id``, no ``amount_cents``, no ``direction``, no flat
``customer_name``, no ``reference``, no ``description`` anywhere. A real
non-empty page raises ``KeyError: 'transaction_id'`` inside
``dodo_source.py``'s own, unmodified ``list_transactions()`` (reproduced
live this card). That bug was invisible before now because every prior
live smoke test (W14, W16) hit a genuinely empty sandbox -- ``items: []``
never iterates, so the broken parsing line never actually ran. It is a
real, previously-undiscovered integration gap, disclosed here (and in
``LIVE_RUN.md``) exactly like this project's other real disclosed gaps
(Neatlogs' W10 ``Client`` bug, Dodo's own W14 403) rather than silently
patched inside the locked file.

Pagination: the real ``GET /payments`` uses ``page_size``/``page_number``
query parameters, not a cursor token (confirmed against the real API
reference, since Dodo's list response carries no ``next_cursor`` field at
all) -- :class:`RealPaymentsClient` encodes ``dodo_source.DodoPage``'s
``next_cursor`` as a decimal page-number string internally so it plugs
into ``dodo_source.list_all_transactions()``'s existing cursor-loop
unchanged.

**Reference field:** Dodo's real payment object has no native
"reference"/PO-number field, so :class:`DodoSeeder` stores one in the
payment's own ``metadata`` (an arbitrary key-value bag every real Dodo
payment already supports) at creation time, and :class:`RealPaymentsClient`
reads it back from there -- a real, if project-chosen, usage of a real
API surface, not an invented field.

**Batch isolation:** every seeded payment also carries a ``metadata.batch``
tag (e.g. ``"phase2"``/``"phase3"``). :class:`RealPaymentsClient` is
constructed with exactly one ``batch`` to read back, so two separate live
pulls against the *same* sandbox account genuinely return disjoint,
non-overlapping transaction sets -- not a re-pull of the same rows filtered
after the fact.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from .dodo_source import DODO_SANDBOX_BASE_URL, DodoAPIError, DodoPage, DodoRawTransaction

_USER_AGENT = "ledger-sense-dodo-live/1.0 (+https://github.com/himanshu-thakur-7/Ledger-Sense)"

# Bounded retries on a transient transport failure (law L22), mirroring
# dodo_source.py's own DodoSandboxClient discipline exactly (small,
# deliberate duplication -- this module intentionally never imports
# dodo_source.py's private helpers, per that module's own "structural
# Protocol, not a shared base class" design).
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_PAGE_SIZE = 100  # Dodo's own documented max page_size

# A small, fixed vendor pool so seeded payments have recurring counterparties
# (mirrors data/generator.py's own "many transactions per counterparty"
# shape) rather than 1 vendor per transaction, which would give routing/
# matching nothing to generalize over.
VENDOR_POOL = (
    "Anchor Robotics Ltd", "Blue Harbor Freight", "Cascade Timber Co",
    "Delta Analytics Group", "Everside Logistics", "Fenwick & Vale LLP",
    "Granite Peak Foods", "Harborlight Media", "Ionix Components",
    "Juniper Cloud Systems", "Kestrel Payments Inc", "Lumen Field Services",
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-")


def _describe_http_error(exc: urllib.error.HTTPError) -> str:
    """One-line, bounded HTTP-error description -- mirrors
    dodo_source.py's own ``_describe_http_error`` exactly (duplicated
    rather than imported; that helper is private to that module)."""
    body = ""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        body = " ".join(raw.split())[:200]
    except Exception:
        pass
    description = f"HTTP {exc.code} {exc.reason}"
    return f"{description} -- {body}" if body else description


@dataclass(frozen=True)
class SeedSpec:
    """One transaction to create -- deterministic given the RNG seed that
    produced it (see :func:`build_seed_specs`), never wall-clock."""

    customer_name: str
    customer_email: str
    amount_cents: int
    reference: str
    batch: str


def build_seed_specs(n: int, *, batch: str, seed: int) -> List[SeedSpec]:
    """``n`` deterministic seed specs for ``batch`` -- vendor, amount, and
    reference are all drawn from a seeded ``random.Random`` (never
    wall-clock/OS entropy, matching this codebase's determinism discipline
    elsewhere), so the *specification* is reproducible even though the
    real Dodo-side ids/timestamps this produces are necessarily real and
    unique. Amounts are plain USD cents (law L3 -- never a float).
    """
    import random

    rng = random.Random(seed)
    specs: List[SeedSpec] = []
    for seq in range(n):
        vendor = VENDOR_POOL[rng.randrange(len(VENDOR_POOL))]
        amount_cents = rng.randint(2_000, 95_000)  # $20.00 - $950.00
        email = f"billing+{seq:05d}@{_slug(vendor)}.example"
        reference = f"INV-LIVE-{batch.upper()}-{seq:05d}"
        specs.append(SeedSpec(vendor, email, amount_cents, reference, batch))
    return specs


class DodoSeedError(Exception):
    """Raised by :class:`DodoSeeder` on a transport-level failure -- mirrors
    ``dodo_source.DodoAPIError``'s own contract (a clean, bounded message,
    never a raw urllib exception escaping)."""


@dataclass
class DodoSeeder:
    """Real transport: ``POST /products`` (once) + ``POST /payments`` (per
    :class:`SeedSpec`) against Dodo's *sandbox* API only. Never instantiated
    by any offline test (mirrors ``dodo_source.DodoSandboxClient``'s own
    "never mocked, only referenced" discipline) -- ``tests/test_dodo_live.py``
    exercises the request-building/response-parsing helpers directly, plus
    one opt-in ``@pytest.mark.slow`` real-sandbox test.
    """

    api_key: str
    base_url: str = DODO_SANDBOX_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=self._headers(), method="POST",
        )
        last_error: object = None
        for _ in range(self.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = _describe_http_error(exc)
                if 400 <= exc.code < 500:
                    break  # a client error won't fix itself on retry (law L22)
            except Exception as exc:  # transient network failure -- bounded retry
                last_error = exc
        raise DodoSeedError(f"Dodo sandbox {path} failed: {last_error}")

    def ensure_product(self, *, name: str, pay_what_you_want: bool = True) -> str:
        """Creates one sandbox product and returns its ``product_id``.
        ``pay_what_you_want=True`` is what makes ``create_payment``'s own
        ``amount`` override actually take effect (confirmed live -- a
        fixed-price product silently ignores a ``product_cart[].amount``
        override; a ``pay_what_you_want`` one honors it)."""
        payload = {
            "name": name,
            "price": {
                "type": "one_time_price", "currency": "USD", "price": 100,
                "discount": 0, "pay_what_you_want": pay_what_you_want,
            },
            "tax_category": "digital_products",
        }
        result = self._post("/products", payload)
        return result["product_id"]

    def create_payment(self, *, product_id: str, spec: SeedSpec) -> str:
        """Creates one real sandbox payment for ``spec`` and returns its
        real ``payment_id``. Never completes a checkout / enters a card --
        the payment object itself (status ``requires_payment_method``) is
        all this project's downstream pipeline ever reads (see module
        docstring)."""
        payload = {
            "product_cart": [{"product_id": product_id, "quantity": 1, "amount": spec.amount_cents}],
            "customer": {"email": spec.customer_email, "name": spec.customer_name},
            "billing": {"country": "US"},
            "metadata": {
                "ledger_sense_seed": "true",
                "batch": spec.batch,
                "reference": spec.reference,
            },
        }
        result = self._post("/payments", payload)
        return result["payment_id"]

    def seed(self, specs: List[SeedSpec], *, product_id: str) -> List[str]:
        """Creates one payment per ``spec``, in order; returns the real
        ``payment_id`` for each. Stops and raises on the first failure
        (never partially-silent -- a caller gets an exact count of what
        actually landed by catching :class:`DodoSeedError` and inspecting
        how many ids were returned before it, or by wrapping this call)."""
        return [self.create_payment(product_id=product_id, spec=spec) for spec in specs]


@dataclass
class RealPaymentsClient:
    """The corrected read client -- see module docstring for exactly why
    ``dodo_source.DodoSandboxClient`` cannot be used as-is. Structurally
    satisfies ``dodo_source.DodoClient`` (``list_transactions(cursor=...)
    -> DodoPage``), so ``dodo_source.build_dodo_dataset()`` /
    ``dodo_source.pull_bank_transactions()`` need zero changes to consume
    it -- only ``ledger_sense.data.cli.main(..., client=RealPaymentsClient(...))``
    is different from the normal ``--source dodo`` path.

    ``batch`` selects exactly one seeded batch (see module docstring's
    "Batch isolation") -- a payment whose ``metadata.batch`` doesn't match
    is silently excluded, the same way a page of unrelated real sandbox
    activity would be if this account ever had any.
    """

    api_key: str
    batch: str
    base_url: str = DODO_SANDBOX_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    page_size: int = DEFAULT_PAGE_SIZE

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }

    def _get_page(self, page_number: int) -> dict:
        url = f"{self.base_url}/payments?page_size={self.page_size}&page_number={page_number}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        last_error: object = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = _describe_http_error(exc)
                if 400 <= exc.code < 500:
                    break
            except Exception as exc:
                last_error = exc
        raise DodoAPIError(f"Dodo sandbox live payments page {page_number} failed: {last_error}")

    def _parse_item(self, item: dict) -> Optional[DodoRawTransaction]:
        metadata = item.get("metadata") or {}
        if metadata.get("batch") != self.batch:
            return None
        customer = item.get("customer") or {}
        return DodoRawTransaction(
            transaction_id=item["payment_id"],
            amount_cents=int(item["total_amount"]),
            currency=item["currency"],
            direction="credit",  # every Dodo payment is inbound to the merchant -- there is no other shape
            customer_name=customer.get("name") or "",
            reference=metadata.get("reference", ""),
            description="",  # real Dodo payments carry no description field
            created_at=item["created_at"],
            status=item.get("status", "requires_payment_method"),
        )

    def list_transactions(self, *, cursor: Optional[str] = None) -> DodoPage:  # pragma: no cover
        # Real network IO -- exercised only by the opt-in real-sandbox test
        # and by an actual live run, never the default offline suite (L20,
        # mirroring dodo_source.DodoSandboxClient's own discipline).
        page_number = int(cursor) if cursor else 0
        payload = self._get_page(page_number)
        raw_items = payload.get("items", [])
        transactions = tuple(
            txn for txn in (self._parse_item(item) for item in raw_items) if txn is not None
        )
        is_last_page = len(raw_items) < self.page_size
        next_cursor = None if is_last_page else str(page_number + 1)
        return DodoPage(transactions=transactions, next_cursor=next_cursor)
