"""Fail on forbidden truth access, including imports hidden behind aliases."""

import ast
import builtins
import io
from pathlib import Path

import pytest

import ledger_sense.matching


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
                assert not {a.name for a in node.names} & {"data", "metrics", "routing", "learning", "guardrail"}
            if module.startswith("ledger_sense.data"):
                assert module in allowed_data
                assert {a.name for a in node.names} <= allowed_data[module]
            assert not any(word in module for word in ("generator", "metrics", "routing", "learning", "guardrail"))
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("ledger_sense.data") for a in node.names)
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            assert name not in {"MatchLink", "MATCH_LINK_COLUMNS", "match_link_rows", "generate", "GeneratedDataset"}
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), "Use Decimal/cents, not float literals"


def test_matching_package_isolation():
    package = Path(ledger_sense.matching.__file__).parent
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
    'outcome.match_link_rows',
])
def test_isolation_check_catches_aliases_and_attributes(source):
    with pytest.raises(AssertionError):
        assert_isolated(source)


def test_runtime_opens_only_the_two_input_files(monkeypatch, tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "mini_pass1"
    allowed = {(fixture / "ledger.csv").resolve(), (fixture / "bank.csv").resolve()}
    reads = []

    def guarded(original):
        def open_file(path, mode="r", *args, **kwargs):
            if "r" in mode:
                resolved = Path(path).resolve()
                assert resolved in allowed, f"Unapproved input: {resolved}"
                reads.append(resolved)
            return original(path, mode, *args, **kwargs)
        return open_file

    monkeypatch.setattr(builtins, "open", guarded(builtins.open))
    monkeypatch.setattr(io, "open", guarded(io.open))
    ledger_sense.matching.run(fixture / "ledger.csv", fixture / "bank.csv", tmp_path)
    assert set(reads) == allowed
