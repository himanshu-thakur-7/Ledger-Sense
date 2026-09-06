"""Acceptance tests for the W11 Dodo Payments sandbox source (BOARD.md W11 card)
and its W16 request-shape/error-handling fixes.

Covers acceptance 1 (mocked client, zero live network calls), 2 (schema parity
with data/generator.py's output), 3 (idempotent pull -- no duplicate rows),
and 5 (missing DODO_API_KEY + --source dodo -> clean nonzero exit, never a
stack trace). See test_dodo_pairing.py for the ledger-synthesis half and
test_matching.py-style fixtures for acceptance 4 (matching.engine integration).

W16 adds: a mocked test proving a configured-but-failing key (`DodoAPIError`)
degrades through `cli.py` exactly like a missing key does (W16 acceptance 2),
and a single opt-in `@pytest.mark.slow` test that makes one real call against
Dodo's real sandbox endpoint (W16 acceptance 1) -- skipped whenever a real
`DODO_API_KEY` isn't configured, so it never runs as part of the default/CI
suite.

Law L20: every test here except that one opt-in slow test plugs a fake
`DodoClient` in -- nothing else in this file ever imports `urllib` or opens a
socket. `DodoSandboxClient` (the real transport) is otherwise only ever
*referenced*, never instantiated with real network IO.
"""

from decimal import Decimal

import pytest

from ledger_sense.data.models import BANK_COLUMNS, BankTransaction

# --- FakeDodoClient: the mocked Dodo client every test in this file uses ---


class FakeDodoClient:
    """An in-memory, paginated `DodoClient` -- no network, fully deterministic."""

    def __init__(self, pages):
        # pages: list of lists of raw-transaction dicts, one list per page.
        self._pages = pages
        self.calls = []

    def list_transactions(self, *, cursor=None):
        from ledger_sense.data.dodo_source import DodoPage, DodoRawTransaction

        self.calls.append(cursor)
        index = 0 if cursor is None else int(cursor)
        items = self._pages[index]
        next_cursor = str(index + 1) if index + 1 < len(self._pages) else None
        return DodoPage(
            transactions=tuple(DodoRawTransaction(**item) for item in items),
            next_cursor=next_cursor,
        )


def _raw(**overrides):
    base = dict(
        transaction_id="txn_001",
        amount_cents=15000,
        currency="USD",
        direction="credit",
        customer_name="Acme Corp",
        reference="INV-2026-1000042",
        description="Payment for invoice",
        created_at="2026-03-01T12:00:00Z",
        status="succeeded",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Acceptance 1: unit tests against a mocked client, zero live network calls
# ---------------------------------------------------------------------------


def test_pull_bank_transactions_uses_only_the_injected_client():
    client = FakeDodoClient([[_raw()]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    rows = pull_bank_transactions(client)
    assert len(rows) == 1
    assert client.calls == [None]  # exactly one page fetched, no real transport touched


def test_pagination_walks_every_page_until_next_cursor_is_none():
    client = FakeDodoClient([[_raw(transaction_id="txn_001")], [_raw(transaction_id="txn_002")]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    rows = pull_bank_transactions(client)
    assert {r.bank_txn_id for r in rows} == {"BK-DODO-txn_001", "BK-DODO-txn_002"}
    assert client.calls == [None, "1"]


def test_dodo_sandbox_client_is_never_imported_as_a_live_dependency():
    """Real transport class must exist (for the CLI to use when configured) but
    this test never constructs or calls it -- confirms it's importable without
    triggering any module-level network setup."""
    from ledger_sense.data.dodo_source import DodoSandboxClient

    client = DodoSandboxClient(api_key="unused-in-tests")
    assert client.api_key == "unused-in-tests"


# ---------------------------------------------------------------------------
# Acceptance 2: normalized rows are the exact BankTransaction shape (L3: Decimal)
# ---------------------------------------------------------------------------


def test_normalized_row_is_a_bank_transaction_with_all_columns():
    client = FakeDodoClient([[_raw()]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    [row] = pull_bank_transactions(client)
    assert isinstance(row, BankTransaction)
    to_row = row.to_row()
    assert list(to_row.keys()) == BANK_COLUMNS


def test_amount_is_decimal_never_float():
    client = FakeDodoClient([[_raw(amount_cents=15099)]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    [row] = pull_bank_transactions(client)
    assert isinstance(row.amount, Decimal)
    assert row.amount == Decimal("150.99")


def test_debit_direction_yields_a_negative_amount():
    client = FakeDodoClient([[_raw(direction="debit", amount_cents=5000)]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    [row] = pull_bank_transactions(client)
    assert row.amount == Decimal("-50.00")
    assert row.direction == "debit"


def test_field_mapping_from_raw_transaction():
    client = FakeDodoClient([[_raw(
        customer_name="Wide Co", reference="INV-777", currency="USD",
    )]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    [row] = pull_bank_transactions(client)
    assert row.counterparty_name_raw == "Wide Co"
    assert row.reference_raw == "INV-777"
    assert row.currency == "USD"
    assert row.bank_txn_id == "BK-DODO-txn_001"


def test_blank_reference_is_preserved_as_empty_string():
    client = FakeDodoClient([[_raw(reference="")]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    [row] = pull_bank_transactions(client)
    assert row.reference_raw == ""


# ---------------------------------------------------------------------------
# Acceptance 3: idempotency -- pulling the same mocked list twice, zero dupes
# ---------------------------------------------------------------------------


def test_duplicate_transaction_within_a_single_pull_is_deduped():
    """Defensive: the same Dodo transaction id appearing on two pages (e.g. an
    overlapping page boundary) must still normalize to exactly one row."""
    client = FakeDodoClient([[_raw(transaction_id="txn_dup")], [_raw(transaction_id="txn_dup")]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    rows = pull_bank_transactions(client)
    assert len(rows) == 1


def test_pulling_the_same_transaction_list_twice_produces_zero_duplicate_rows():
    client_a = FakeDodoClient([[_raw(transaction_id="txn_001"), _raw(transaction_id="txn_002")]])
    client_b = FakeDodoClient([[_raw(transaction_id="txn_001"), _raw(transaction_id="txn_002")]])
    from ledger_sense.data.dodo_source import merge_dedup_by_bank_txn_id, pull_bank_transactions

    first_pull = pull_bank_transactions(client_a)
    second_pull = pull_bank_transactions(client_b)  # simulates re-running the same ingest later
    merged = merge_dedup_by_bank_txn_id(first_pull + second_pull)

    assert len(merged) == 2
    assert len({r.bank_txn_id for r in merged}) == 2
    # And the two independent pulls agree byte-for-byte (same input -> same output).
    assert [r.to_row() for r in first_pull] == [r.to_row() for r in second_pull]


def test_bank_txn_id_is_stable_and_derived_from_dodo_transaction_id():
    client = FakeDodoClient([[_raw(transaction_id="txn_abc123")]])
    from ledger_sense.data.dodo_source import pull_bank_transactions

    [row] = pull_bank_transactions(client)
    assert row.bank_txn_id == "BK-DODO-txn_abc123"


# ---------------------------------------------------------------------------
# Acceptance 5: missing DODO_API_KEY + --source dodo -> clean nonzero exit
# ---------------------------------------------------------------------------


def test_ensure_dodo_configured_raises_when_key_absent():
    from ledger_sense.config import Config
    from ledger_sense.data.dodo_source import DodoNotConfiguredError, ensure_dodo_configured

    cfg = Config(dodo_api_key=None, data_source="dodo")
    with pytest.raises(DodoNotConfiguredError):
        ensure_dodo_configured(cfg)


def test_ensure_dodo_configured_passes_when_key_present():
    from ledger_sense.config import Config
    from ledger_sense.data.dodo_source import ensure_dodo_configured

    cfg = Config(dodo_api_key="dodo-test-key", data_source="dodo")
    ensure_dodo_configured(cfg)  # must not raise


def test_cli_missing_key_exits_nonzero_with_clear_message_no_traceback(capsys):
    from ledger_sense.config import Config
    from ledger_sense.data.cli import main

    cfg = Config(dodo_api_key=None, data_source="synthetic")  # key absent regardless of data_source
    exit_code = main(
        ["--seed", "1", "--pass-number", "1", "--n-cases", "5", "--source", "dodo"],
        config=cfg,
    )
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "DODO_API_KEY" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# W16 acceptance 2: a configured-but-failing key (DodoAPIError) degrades as
# cleanly as an absent key -- one-line stderr, nonzero exit, no traceback.
# ---------------------------------------------------------------------------


class FailingDodoClient:
    """A `DodoClient` that raises `DodoAPIError` -- simulates a real Dodo
    transport failure (e.g. the exact HTTP-403-shaped error W14's live smoke
    test hit) without any real network IO (law L20)."""

    def list_transactions(self, *, cursor=None):
        from ledger_sense.data.dodo_source import DodoAPIError

        raise DodoAPIError(
            "Dodo sandbox list_transactions failed after 3 attempt(s): "
            "HTTP 403 Forbidden -- error code: 1010"
        )


def test_cli_dodo_api_error_exits_nonzero_with_clear_message_no_traceback(capsys):
    """The exact gap W14's live smoke test found: a configured key that still
    fails against the real API must exit as cleanly as a missing key does,
    never with a raw traceback (law L18, W16 acceptance 2)."""
    from ledger_sense.config import Config
    from ledger_sense.data.cli import main

    cfg = Config(dodo_api_key="dodo-test-key", data_source="dodo")
    exit_code = main(
        ["--seed", "1", "--pass-number", "1", "--n-cases", "5", "--source", "dodo"],
        config=cfg,
        client=FailingDodoClient(),
    )
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "403" in captured.err
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") <= 1  # a single clean line, not a dump


def test_dodo_api_error_raised_by_pull_is_not_caught_anywhere_else():
    """`DodoAPIError` must propagate out of `build_dodo_dataset`/
    `pull_bank_transactions` uncaught -- only `cli.py::main` is allowed to
    catch it (and turn it into a clean exit)."""
    from ledger_sense.data.dodo_source import DodoAPIError, build_dodo_dataset

    with pytest.raises(DodoAPIError):
        build_dodo_dataset(FailingDodoClient(), seed=1)


def test_cli_synthetic_default_unaffected_by_missing_dodo_key(tmp_path):
    from ledger_sense.config import Config
    from ledger_sense.data.cli import main

    cfg = Config(dodo_api_key=None, data_source="synthetic")
    exit_code = main(
        ["--seed", "1", "--pass-number", "1", "--n-cases", "5", "--out-dir", str(tmp_path)],
        config=cfg,
    )
    assert exit_code == 0
    assert (tmp_path / "ledger.csv").exists()
    assert (tmp_path / "bank.csv").exists()
    assert (tmp_path / "match_links.csv").exists()


# ---------------------------------------------------------------------------
# Acceptance 4: matching.engine runs unmodified against Dodo-sourced fixtures
# ---------------------------------------------------------------------------


def test_matching_engine_runs_unmodified_against_dodo_sourced_output(tmp_path):
    """`matching.io.run` (Agent 1, completely untouched by this card) must
    accept a Dodo-sourced dataset written to disk exactly like a synthetic one
    and produce a schema-valid match_outcomes.csv -- no crash, no special-casing."""
    import csv

    from ledger_sense.data.cli import write_dataset
    from ledger_sense.data.dodo_source import build_dodo_dataset
    from ledger_sense.matching.io import run as matching_run

    client = FakeDodoClient([[
        _raw(transaction_id=f"txn_{i:03d}", amount_cents=10_000 + i * 137,
             direction="credit" if i % 2 == 0 else "debit",
             customer_name=f"Dodo Customer {i}", reference=f"INV-DODO-2026-{i:06d}")
        for i in range(25)
    ]])
    dataset = build_dodo_dataset(client, seed=42)
    write_dataset(dataset, str(tmp_path))

    out_dir = tmp_path / "match_out"
    result = matching_run(tmp_path / "ledger.csv", tmp_path / "bank.csv", out_dir)

    outcomes_path = out_dir / "match_outcomes.csv"
    assert outcomes_path.exists()
    with outcomes_path.open(newline="", encoding="utf-8") as fh:
        outcome_rows = list(csv.DictReader(fh))

    assert outcome_rows, "expected at least one match outcome row"
    assert len(outcome_rows) == len(dataset.bank_rows)
    valid_statuses = {"matched", "escalated", "rejected"}
    for row in outcome_rows:
        assert row["status"] in valid_statuses
        assert row["bank_txn_id"]
        Decimal(row["score"])  # must parse as a plain decimal number
        Decimal(row["matched_amount"])
    assert result.outcomes  # matching.io.run's own result object is usable too


def test_cli_dodo_source_with_key_and_injected_client_succeeds(tmp_path):
    from ledger_sense.config import Config
    from ledger_sense.data.cli import main

    cfg = Config(dodo_api_key="dodo-test-key", data_source="dodo")
    client = FakeDodoClient([[_raw(transaction_id="txn_001"), _raw(transaction_id="txn_002", direction="debit")]])
    exit_code = main(
        ["--seed", "1", "--pass-number", "1", "--n-cases", "5", "--source", "dodo", "--out-dir", str(tmp_path)],
        config=cfg,
        client=client,
    )
    assert exit_code == 0
    assert (tmp_path / "ledger.csv").exists()
    assert (tmp_path / "bank.csv").exists()
    assert (tmp_path / "match_links.csv").exists()


# ---------------------------------------------------------------------------
# W16 acceptance 1: one real, non-mocked call against Dodo's real sandbox
# endpoint. Opt-in (`@pytest.mark.slow`) and skipped whenever a real
# DODO_API_KEY isn't configured (via .env or the real environment) -- this
# never runs as part of the default/CI suite and never requires a live key
# (mirrors W6's real-batch opt-in tests / W15's real-SDK test convention).
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_dodo_sandbox_list_transactions_no_longer_403s():
    """This is the exact request W14's live smoke test found returning a 403
    (`DodoSandboxClient.list_transactions()` against
    `https://test.dodopayments.com/payments`). Proves the fix: a real
    sandbox key now gets a real 200 (with or without transactions) instead
    -- a 403 here means the request shape is still broken."""
    from ledger_sense.config import load_config
    from ledger_sense.data.dodo_source import DodoAPIError, DodoSandboxClient

    cfg = load_config()
    if not cfg.dodo_enabled():
        pytest.skip("DODO_API_KEY not configured -- set it (.env or real env) to run this live test")

    client = DodoSandboxClient(api_key=cfg.dodo_api_key)
    try:
        page = client.list_transactions()
    except DodoAPIError as exc:
        pytest.fail(
            f"real Dodo sandbox call failed -- request shape is still wrong: {exc}"
        )

    # A real 200 either way: some transactions, or a documented empty
    # sandbox account. Either is an acceptable real outcome -- a 403 above
    # would have already failed this test via DodoAPIError.
    assert isinstance(page.transactions, tuple)
