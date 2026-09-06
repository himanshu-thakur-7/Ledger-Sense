"""Acceptance tests for the W11 Dodo/ledger pairing module (BOARD.md W11 card).

Covers acceptance 2 (schema parity: synthesized LedgerEntry/MatchLink rows use
the exact spec columns) plus the "same defect-mix proportions/logic, reused
and documented" requirement, applied to the reduced defect subset
`dodo_pairing.py` documents (see its module docstring for the full rationale).
"""

from decimal import Decimal

from ledger_sense.data.dodo_source import normalize_bank_transaction, DodoRawTransaction
from ledger_sense.data.models import LEDGER_COLUMNS, MATCH_LINK_COLUMNS, LedgerEntry, MatchLink


def _bank(**overrides):
    raw = dict(
        transaction_id="txn_001",
        amount_cents=25000,
        currency="USD",
        direction="credit",
        customer_name="Acme Corp",
        reference="INV-2026-1000042",
        description="Payment for invoice",
        created_at="2026-03-01T12:00:00Z",
        status="succeeded",
    )
    raw.update(overrides)
    return normalize_bank_transaction(DodoRawTransaction(**raw))


# ---------------------------------------------------------------------------
# Schema parity (acceptance 2)
# ---------------------------------------------------------------------------


def test_synthesize_ledger_entry_returns_exact_schema():
    from ledger_sense.data.dodo_pairing import synthesize_ledger_entry

    bank = _bank()
    entry, link, defect = synthesize_ledger_entry(bank, "clean", __import__("random").Random(1), seq=0)
    assert isinstance(entry, LedgerEntry)
    assert isinstance(link, MatchLink)
    assert list(entry.to_row().keys()) == LEDGER_COLUMNS
    assert list(link.to_row().keys()) == MATCH_LINK_COLUMNS


def test_pair_dataset_row_shape_matches_generator_columns():
    from ledger_sense.data.dodo_pairing import pair_dataset

    banks = [_bank(transaction_id=f"txn_{i:03d}") for i in range(20)]
    ledger_entries, match_links, histogram = pair_dataset(banks, seed=42)
    assert len(ledger_entries) == 20
    assert len(match_links) == 20
    for entry in ledger_entries:
        assert list(entry.to_row().keys()) == LEDGER_COLUMNS
    for link in match_links:
        assert list(link.to_row().keys()) == MATCH_LINK_COLUMNS


def test_amounts_are_decimal_never_float():
    from ledger_sense.data.dodo_pairing import pair_dataset

    banks = [_bank(transaction_id=f"txn_{i:03d}") for i in range(10)]
    ledger_entries, _, _ = pair_dataset(banks, seed=1)
    for entry in ledger_entries:
        assert isinstance(entry.amount, Decimal)


# ---------------------------------------------------------------------------
# Pairing links each synthesized ledger row back to its source bank row
# ---------------------------------------------------------------------------


def test_each_match_link_references_its_own_bank_and_ledger_row():
    from ledger_sense.data.dodo_pairing import pair_dataset

    banks = [_bank(transaction_id=f"txn_{i:03d}") for i in range(15)]
    ledger_entries, match_links, _ = pair_dataset(banks, seed=7)
    ledger_ids = {e.ledger_id for e in ledger_entries}
    bank_ids = {b.bank_txn_id for b in banks}
    for link in match_links:
        assert link.ledger_id in ledger_ids
        assert link.bank_txn_id in bank_ids
    # 1:1 pairing -- every pulled bank row gets exactly one paired ledger row.
    assert {l.bank_txn_id for l in match_links} == bank_ids
    assert len({l.ledger_id for l in match_links}) == len(banks)


# ---------------------------------------------------------------------------
# Defect-mix reuse: sampled from the documented, reduced §4.2 subset
# ---------------------------------------------------------------------------


def test_pairing_defects_are_a_documented_subset_of_generator_taxonomy():
    from ledger_sense.data.defects import DEFECT_RATES
    from ledger_sense.data.dodo_pairing import PAIRING_DEFECTS

    assert set(PAIRING_DEFECTS) <= set(DEFECT_RATES)
    assert set(PAIRING_DEFECTS) == {
        "clean", "wrong_reference", "fx_rounding", "negative_amount", "zero_amount",
    }


def test_pairing_rates_reuse_generator_proportions_renormalized():
    from ledger_sense.data.dodo_pairing import pairing_rates

    rates = pairing_rates()
    assert sum(rates.values()) == Decimal("100")
    # clean is still by far the dominant class, mirroring §4.2's shape.
    assert rates["clean"] > rates["wrong_reference"]
    assert rates["clean"] > rates["fx_rounding"]


def test_pairing_defect_histogram_matches_documented_mix_at_scale():
    from ledger_sense.data.dodo_pairing import pair_dataset, pairing_defect_counts

    n = 2000
    banks = [_bank(transaction_id=f"txn_{i:04d}") for i in range(n)]
    _, _, histogram = pair_dataset(banks, seed=99)
    expected = pairing_defect_counts(n)
    # Every bank row has a non-blank reference in this fixture, so no row is
    # forced to `missing_reference` -- the sampled histogram must match the
    # exact stratified counts.
    assert dict(histogram) == dict(expected)


def test_blank_reference_forces_missing_reference_regardless_of_sample():
    from ledger_sense.data.dodo_pairing import pair_dataset

    banks = [_bank(transaction_id="txn_blank", reference="")]
    _, match_links, histogram = pair_dataset(banks, seed=3)
    assert match_links[0].defect == "missing_reference"
    assert histogram == {"missing_reference": 1}


# ---------------------------------------------------------------------------
# Guardrail-bait shapes (negative_amount / zero_amount)
# ---------------------------------------------------------------------------


def test_negative_amount_defect_flips_sign_vs_bank():
    from ledger_sense.data.dodo_pairing import synthesize_ledger_entry
    import random

    bank = _bank(amount_cents=10000)  # +100.00
    entry, link, defect = synthesize_ledger_entry(bank, "negative_amount", random.Random(5), seq=0)
    assert defect == "negative_amount"
    assert entry.amount == -bank.amount


def test_zero_amount_defect_zeroes_the_ledger_amount():
    from ledger_sense.data.dodo_pairing import synthesize_ledger_entry
    import random

    bank = _bank(amount_cents=10000)
    entry, link, defect = synthesize_ledger_entry(bank, "zero_amount", random.Random(5), seq=0)
    assert defect == "zero_amount"
    assert entry.amount == Decimal("0.00")


def test_clean_defect_mirrors_bank_amount_and_reference_exactly():
    from ledger_sense.data.dodo_pairing import synthesize_ledger_entry
    import random

    bank = _bank(amount_cents=42500, reference="INV-2026-1000042")
    entry, link, defect = synthesize_ledger_entry(bank, "clean", random.Random(2), seq=0)
    assert defect == "clean"
    assert entry.amount == bank.amount
    assert entry.reference == bank.reference_raw


def test_wrong_reference_defect_produces_a_different_reference():
    from ledger_sense.data.dodo_pairing import synthesize_ledger_entry
    import random

    bank = _bank(reference="INV-2026-1000042")
    entry, link, defect = synthesize_ledger_entry(bank, "wrong_reference", random.Random(2), seq=0)
    assert defect == "wrong_reference"
    assert entry.reference != bank.reference_raw


def test_fx_rounding_defect_produces_a_small_amount_delta():
    from ledger_sense.data.dodo_pairing import FX_DELTA_MAX_CENTS, synthesize_ledger_entry
    from ledger_sense.data.money import cents
    import random

    bank = _bank(amount_cents=100000)
    entry, link, defect = synthesize_ledger_entry(bank, "fx_rounding", random.Random(9), seq=0)
    assert defect == "fx_rounding"
    delta = abs(cents(entry.amount) - cents(bank.amount))
    assert 0 < delta <= FX_DELTA_MAX_CENTS


# ---------------------------------------------------------------------------
# Determinism (law L4-in-spirit): same inputs + seed -> byte-identical output
# ---------------------------------------------------------------------------


def test_pair_dataset_is_deterministic_given_the_same_seed():
    from ledger_sense.data.dodo_pairing import pair_dataset

    banks = [_bank(transaction_id=f"txn_{i:03d}") for i in range(30)]
    a_entries, a_links, a_hist = pair_dataset(banks, seed=123)
    b_entries, b_links, b_hist = pair_dataset(banks, seed=123)
    assert [e.to_row() for e in a_entries] == [e.to_row() for e in b_entries]
    assert [l.to_row() for l in a_links] == [l.to_row() for l in b_links]
    assert dict(a_hist) == dict(b_hist)


def test_pair_dataset_differs_with_a_different_seed():
    from ledger_sense.data.dodo_pairing import pair_dataset

    banks = [_bank(transaction_id=f"txn_{i:03d}") for i in range(30)]
    a_entries, _, _ = pair_dataset(banks, seed=1)
    b_entries, _, _ = pair_dataset(banks, seed=2)
    assert [e.to_row() for e in a_entries] != [e.to_row() for e in b_entries]
