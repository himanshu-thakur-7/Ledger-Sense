"""Fail on forbidden truth access, including imports hidden behind aliases.

Mirrors ``tests/test_matching_isolation.py``'s pattern: Agent 2 must never
read ``match_links.csv`` (ground truth) and must never import anything from
``ledger_sense.matching`` (another agent's internals) -- both a source-level
grep/AST sweep and a runtime open()-guard test.
"""

import ast
import builtins
import io
from pathlib import Path

import pytest

import ledger_sense.routing
from ledger_sense.routing.io import run as routing_run


def assert_isolated(source):
    assert "match_links" not in source.lower()
    tree = ast.parse(source)
    allowed_data = {
        "ledger_sense.data.models": {"BANK_COLUMNS", "LEDGER_COLUMNS"},
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
                assert not {a.name for a in node.names} & {"data", "matching", "learning", "guardrail"}
            if module.startswith("ledger_sense.data"):
                assert module in allowed_data
                assert {a.name for a in node.names} <= allowed_data[module]
            assert not any(word in module for word in ("generator", "matching", "learning", "guardrail"))
            assert "matching" not in module
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("ledger_sense.data") or "matching" in a.name for a in node.names)
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            assert name not in {"MatchLink", "MATCH_LINK_COLUMNS", "match_link_rows"}
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), "Use Decimal, not float literals, for money/scores"
        if isinstance(node, ast.Call):
            # L7: never datetime.now()/utcnow(). L8: never Python's hash().
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            assert name != "hash", "routing must assign owners via blake2b, never hash() (L8)"
            assert name not in {"now", "utcnow"}, "routing must use the explicit --as-of, never datetime.now() (L7)"


def test_routing_package_isolation():
    package = Path(ledger_sense.routing.__file__).parent
    for file in package.rglob("*"):
        if file.is_file() and "__pycache__" not in file.parts:
            assert "match_links" not in file.read_text().lower(), file
            if file.suffix == ".py":
                assert_isolated(file.read_text())


@pytest.mark.parametrize("source", [
    'open("match_links.csv")',
    'from ledger_sense.data.models import MatchLink as Innocent',
    'from ledger_sense.matching import engine as innocent',
    'from ledger_sense.matching.scoring import squash as innocent',
    'import ledger_sense.matching as innocent',
    'from ledger_sense import matching as innocent',
    'from ..matching.engine import match as innocent',
    'outcome.match_link_rows',
    'hash(counterparty_key)',
    'datetime.datetime.now()',
    'datetime.datetime.utcnow()',
])
def test_isolation_check_catches_aliases_and_attributes(source):
    with pytest.raises(AssertionError):
        assert_isolated(source)


def test_runtime_opens_only_the_four_input_files(monkeypatch, tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "mini_pass1"
    mini_out = tmp_path / "mini_out"
    mini_out.mkdir()

    # Build Agent 1's outputs the normal way (outside the guard) so this test
    # only measures what *routing* opens, not what matching opens.
    from ledger_sense.matching.io import run as matching_run
    matching_run(fixture / "ledger.csv", fixture / "bank.csv", mini_out)

    allowed = {
        (mini_out / "match_outcomes.csv").resolve(),
        (mini_out / "ledger_settlements.csv").resolve(),
        (fixture / "ledger.csv").resolve(),
        (fixture / "bank.csv").resolve(),
    }
    reads = []

    def guarded(original):
        def open_file(path, mode="r", *args, **kwargs):
            if "r" in mode:
                resolved = Path(path).resolve()
                assert resolved in allowed, f"Unapproved input: {resolved}"
                assert "match_links" not in resolved.name
                reads.append(resolved)
            return original(path, mode, *args, **kwargs)
        return open_file

    monkeypatch.setattr(builtins, "open", guarded(builtins.open))
    monkeypatch.setattr(io, "open", guarded(io.open))
    out_dir = tmp_path / "routed"
    out_dir.mkdir()
    routing_run(mini_out / "match_outcomes.csv", mini_out / "ledger_settlements.csv",
                fixture / "ledger.csv", fixture / "bank.csv", "2026-06-01T00:00:00Z", out_dir)
    assert set(reads) == allowed
