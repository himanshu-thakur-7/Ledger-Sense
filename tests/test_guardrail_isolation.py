"""Fail on forbidden truth access (law L2) and float money (law L3).

Mirrors ``tests/test_matching_isolation.py`` -- same technique (grep + AST),
adapted to guardrail's allowed import surface and its four permitted input
files.
"""

import ast
import builtins
import io
from pathlib import Path

import pytest

import ledger_sense.guardrail
from ledger_sense.data.models import BankTransaction, LedgerEntry
from ledger_sense.data.money import to_money
from ledger_sense.guardrail import run
from tests.test_guardrail import write_bank, write_ledger, write_outcomes, write_settlements

FIXTURE = Path(__file__).parent / "fixtures" / "mini_pass1"


def assert_isolated(source):
    assert "match_links" not in source.lower()
    tree = ast.parse(source)
    allowed_data = {
        "ledger_sense.data.models": {"BankTransaction", "LedgerEntry", "BANK_COLUMNS", "LEDGER_COLUMNS"},
        "ledger_sense.data.money": {"cents", "from_cents", "money_str", "to_money"},
        "ledger_sense.data.io_csv": {"write_csv"},
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not {a.name for a in node.names} & {"MatchLink", "MATCH_LINK_COLUMNS", "generate", "GeneratedDataset"}
            if module == "data" or module.startswith("data."):
                module = "ledger_sense." + module
            if module == "ledger_sense":
                assert not {a.name for a in node.names} & {"data", "metrics", "routing", "learning", "matching"}
            if module.startswith("ledger_sense.data"):
                assert module in allowed_data
                assert {a.name for a in node.names} <= allowed_data[module]
            assert not any(word in module for word in ("generator", "metrics", "routing", "learning", "matching"))
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("ledger_sense.data") for a in node.names)
            assert not any(a.name.startswith("ledger_sense.matching") for a in node.names)
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            assert name not in {"MatchLink", "MATCH_LINK_COLUMNS", "match_link_rows", "generate", "GeneratedDataset"}
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), "Use Decimal/cents, not float literals (law L3)"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "float", "float() is banned in guardrail (law L3)"


def test_guardrail_package_isolation():
    package = Path(ledger_sense.guardrail.__file__).parent
    for file in package.rglob("*"):
        if file.is_file() and "__pycache__" not in file.parts:
            assert "match_links" not in file.read_text().lower(), file
            if file.suffix == ".py":
                assert_isolated(file.read_text())


@pytest.mark.parametrize("source", [
    'open("match_links.csv")',
    'from ledger_sense.data.models import MatchLink as Innocent',
    'import ledger_sense.data.generator as innocent',
    'from ledger_sense.data import generate as innocent',
    'from ..data.models import MatchLink as Innocent',
    'from ledger_sense import data as innocent',
    'import ledger_sense.matching as innocent',
    'from ledger_sense.matching import io as innocent',
    'outcome.match_link_rows',
    'x = 0.5',
    'float("1.5")',
])
def test_isolation_check_catches_aliases_floats_and_matching_import(source):
    with pytest.raises(AssertionError):
        assert_isolated(source)


def test_runtime_opens_only_the_four_input_files(monkeypatch, tmp_path):
    ledger_path = tmp_path / "ledger.csv"
    bank_path = tmp_path / "bank.csv"
    outcomes_path = tmp_path / "outcomes.csv"
    settlements_path = tmp_path / "settlements.csv"

    write_ledger(ledger_path, [LedgerEntry("LG-1", "2026-03-10T00:00:00Z", to_money("100.00"), "USD",
                                            "invoice_payment", "CP-1", "Acme Logistics", "INV-1", "", "1200", "billing")])
    write_bank(bank_path, [BankTransaction("BK-1", "2026-03-10T00:00:00Z", to_money("100.00"), "USD",
                                            "Acme Logistics", "INV-1", "", "ACCT-USD-01", "STMT-1", "credit")])
    write_outcomes(outcomes_path, [{
        "bank_txn_id": "BK-1", "status": "matched", "relation": "exact", "ledger_id": "LG-1", "tier": "cheap",
        "score": "100.00", "margin": "100.00", "reason": "high_confidence", "reason_detail": "", "matched_amount": "100.00",
        "residual_after": "0.00", "candidates": "[]", "features": "{}", "llm_model": "", "llm_confidence": "", "llm_is_stub": "True",
    }])
    write_settlements(settlements_path, [{
        "ledger_id": "LG-1", "ledger_amount": "100.00", "matched_amount": "100.00", "residual": "0.00", "n_parts": "1",
        "bank_txn_ids": '["BK-1"]', "fully_settled": "True", "reason": "fully_settled",
    }])

    from ledger_sense.guardrail.policy import DEFAULT_POLICY_PATH
    allowed = {p.resolve() for p in (ledger_path, bank_path, outcomes_path, settlements_path, DEFAULT_POLICY_PATH)}
    reads = []

    def guarded(original):
        def open_file(path, mode="r", *args, **kwargs):
            if "r" in mode and "w" not in mode:
                resolved = Path(path).resolve()
                assert resolved in allowed, f"Unapproved input: {resolved}"
                reads.append(resolved)
            return original(path, mode, *args, **kwargs)
        return open_file

    monkeypatch.setattr(builtins, "open", guarded(builtins.open))
    monkeypatch.setattr(io, "open", guarded(io.open))
    from datetime import datetime, timezone
    run(ledger_path, bank_path, outcomes_path, settlements_path, datetime(2026, 3, 31, tzinfo=timezone.utc), tmp_path / "out")
    assert set(reads) == allowed
