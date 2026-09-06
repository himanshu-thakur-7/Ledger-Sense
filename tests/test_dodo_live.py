"""Tests for the LIVE-1 seeding + corrected live-read client
(``ledger_sense.data.dodo_live``) -- same pattern as ``test_dodo_source.py``:
every test here mocks the transport (law L20, no live network calls) except
one opt-in ``@pytest.mark.slow`` real-sandbox test, skipped whenever a real
``DODO_API_KEY`` isn't configured.
"""

import json
from types import SimpleNamespace

import pytest

_ALL_V2_KEYS = ("OPENAI_API_KEY", "DODO_API_KEY", "NEATLOGS_API_KEY", "LEDGER_SENSE_DATA_SOURCE")


@pytest.fixture(autouse=True)
def _clean_v2_env(monkeypatch):
    for key in _ALL_V2_KEYS:
        monkeypatch.delenv(key, raising=False)


def _real_payment_item(**overrides):
    """One item exactly shaped like Dodo's real, live GET /payments response
    (captured live this card -- see dodo_live.py's module docstring)."""
    base = dict(
        payment_id="pay_0Nn1fsd7Cg7NYXH3cn4j7",
        status="requires_payment_method",
        total_amount=4599,
        currency="USD",
        payment_method=None,
        customer={"customer_id": "cus_0Nn1fsd5ftN07GJodYcgr", "name": "Blue Harbor Freight",
                  "email": "billing+00001@blue-harbor-freight.example", "phone_number": None, "metadata": {}},
        created_at="2026-09-06T16:47:21.557815Z",
        metadata={"ledger_sense_seed": "true", "batch": "phase2", "reference": "INV-LIVE-PHASE2-00001"},
        invoice_id="inv_0Nn1fsd7Cg7NYXHAXlWz0",
        payment_provider="dodo",
    )
    base.update(overrides)
    return base


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- build_seed_specs ------------------------------------------------------


def test_build_seed_specs_is_deterministic():
    from ledger_sense.data.dodo_live import build_seed_specs

    first = build_seed_specs(20, batch="phase2", seed=42)
    second = build_seed_specs(20, batch="phase2", seed=42)
    assert first == second


def test_build_seed_specs_differs_by_seed_and_batch():
    from ledger_sense.data.dodo_live import build_seed_specs

    a = build_seed_specs(20, batch="phase2", seed=42)
    b = build_seed_specs(20, batch="phase2", seed=99)
    assert a != b
    assert all(spec.batch == "phase2" for spec in a)
    c = build_seed_specs(20, batch="phase3", seed=42)
    assert all(spec.batch == "phase3" for spec in c)
    assert all(spec.reference.startswith("INV-LIVE-PHASE3-") for spec in c)


def test_build_seed_specs_amounts_are_plain_cents_never_float():
    from ledger_sense.data.dodo_live import build_seed_specs

    specs = build_seed_specs(50, batch="phase2", seed=1)
    assert all(isinstance(spec.amount_cents, int) for spec in specs)
    assert all(2_000 <= spec.amount_cents <= 95_000 for spec in specs)


# --- RealPaymentsClient._parse_item -----------------------------------------


def test_parse_item_maps_the_real_response_shape_correctly():
    from ledger_sense.data.dodo_live import RealPaymentsClient

    client = RealPaymentsClient(api_key="sk_test", batch="phase2")
    txn = client._parse_item(_real_payment_item())
    assert txn.transaction_id == "pay_0Nn1fsd7Cg7NYXH3cn4j7"
    assert txn.amount_cents == 4599
    assert txn.currency == "USD"
    assert txn.direction == "credit"
    assert txn.customer_name == "Blue Harbor Freight"
    assert txn.reference == "INV-LIVE-PHASE2-00001"
    assert txn.description == ""
    assert txn.created_at == "2026-09-06T16:47:21.557815Z"


def test_parse_item_excludes_a_different_batch():
    from ledger_sense.data.dodo_live import RealPaymentsClient

    client = RealPaymentsClient(api_key="sk_test", batch="phase3")
    assert client._parse_item(_real_payment_item()) is None  # item is batch="phase2"


def test_parse_item_excludes_items_with_no_seed_batch_at_all():
    """Real sandbox activity unrelated to this project (or another batch
    entirely) must never leak into a specific batch's pulled dataset."""
    from ledger_sense.data.dodo_live import RealPaymentsClient

    client = RealPaymentsClient(api_key="sk_test", batch="phase2")
    unrelated = _real_payment_item(metadata={})
    assert client._parse_item(unrelated) is None


# --- RealPaymentsClient.list_transactions (paginated, mocked) --------------


def test_list_transactions_paginates_and_filters_by_batch(monkeypatch):
    from ledger_sense.data import dodo_live
    from ledger_sense.data.dodo_source import list_all_transactions

    page0 = [_real_payment_item(payment_id=f"pay_{i:03d}") for i in range(100)]
    page1 = [_real_payment_item(payment_id="pay_100")]
    pages = {0: page0, 1: page1}
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        page_number = int(request.full_url.rsplit("page_number=", 1)[1])
        return _FakeHTTPResponse({"items": pages.get(page_number, [])})

    monkeypatch.setattr(dodo_live.urllib.request, "urlopen", fake_urlopen)

    client = dodo_live.RealPaymentsClient(api_key="sk_test", batch="phase2", page_size=100)
    all_txns = list_all_transactions(client)

    assert len(all_txns) == 101  # 100 + 1, two real pages, correctly stitched
    assert len(calls) == 2  # stopped as soon as a short (< page_size) page arrived


def test_list_transactions_raises_dodo_api_error_on_http_failure(monkeypatch):
    import urllib.error

    from ledger_sense.data import dodo_live

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(dodo_live.urllib.request, "urlopen", fake_urlopen)
    client = dodo_live.RealPaymentsClient(api_key="bad_key", batch="phase2")
    with pytest.raises(dodo_live.DodoAPIError):
        client.list_transactions()


# --- DodoSeeder (mocked POSTs) ----------------------------------------------


def test_ensure_product_returns_the_product_id(monkeypatch):
    from ledger_sense.data import dodo_live

    def fake_urlopen(request, timeout):
        assert request.get_method() == "POST"
        body = json.loads(request.data.decode("utf-8"))
        assert body["price"]["pay_what_you_want"] is True
        return _FakeHTTPResponse({"product_id": "pdt_test_001", "name": body["name"]})

    monkeypatch.setattr(dodo_live.urllib.request, "urlopen", fake_urlopen)
    seeder = dodo_live.DodoSeeder(api_key="sk_test")
    assert seeder.ensure_product(name="Ledger Sense Seed Invoice") == "pdt_test_001"


def test_seed_creates_one_payment_per_spec_in_order(monkeypatch):
    from ledger_sense.data import dodo_live

    created = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        created.append(body["metadata"]["reference"])
        return _FakeHTTPResponse({"payment_id": f"pay_{len(created):03d}"})

    monkeypatch.setattr(dodo_live.urllib.request, "urlopen", fake_urlopen)
    seeder = dodo_live.DodoSeeder(api_key="sk_test")
    specs = dodo_live.build_seed_specs(5, batch="phase2", seed=1)
    ids = seeder.seed(specs, product_id="pdt_test_001")
    assert ids == ["pay_001", "pay_002", "pay_003", "pay_004", "pay_005"]
    assert created == [spec.reference for spec in specs]


def test_seeder_does_not_retry_a_4xx_client_error(monkeypatch):
    import urllib.error

    from ledger_sense.data import dodo_live

    attempts = SimpleNamespace(count=0)

    def fake_urlopen(request, timeout):
        attempts.count += 1
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(dodo_live.urllib.request, "urlopen", fake_urlopen)
    seeder = dodo_live.DodoSeeder(api_key="sk_test", max_retries=3)
    with pytest.raises(dodo_live.DodoSeedError):
        seeder.ensure_product(name="x")
    assert attempts.count == 1  # a 4xx fails fast, never burns the retry budget


# ---------------------------------------------------------------------------
# One real, non-mocked round trip against Dodo's real sandbox API: creates
# exactly one product + one payment (clearly tagged batch="pytest-slow-probe"
# so it is trivially distinguishable from any real seeded card/live-1 data),
# then confirms list_transactions() pulls it back correctly through the
# *corrected* parsing this module adds. Skipped whenever a real
# DODO_API_KEY isn't configured -- never runs as part of the default/CI
# suite (mirrors test_dodo_source.py's own opt-in real-sandbox test).
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_dodo_sandbox_seed_and_pull_round_trip():
    import os

    from ledger_sense.data import dodo_live
    from ledger_sense.data.dodo_source import list_all_transactions

    api_key = os.environ.get("DODO_API_KEY")
    if not api_key:
        pytest.skip("DODO_API_KEY not configured -- opt-in real-sandbox test only")

    seeder = dodo_live.DodoSeeder(api_key=api_key)
    product_id = seeder.ensure_product(name="Ledger Sense Test Probe (pytest)")
    spec = dodo_live.build_seed_specs(1, batch="pytest-slow-probe", seed=1)[0]
    payment_id = seeder.create_payment(product_id=product_id, spec=spec)
    assert payment_id.startswith("pay_")

    client = dodo_live.RealPaymentsClient(api_key=api_key, batch="pytest-slow-probe")
    all_txns = list_all_transactions(client)
    ids = [txn.transaction_id for txn in all_txns]
    assert payment_id in ids
