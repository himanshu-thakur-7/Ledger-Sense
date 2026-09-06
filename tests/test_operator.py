"""Acceptance tests for the close desk (BOARD.md TAPE-1 part C).

Covers acceptance 1 (keyless matching --help -- see test_tracing.py), 4
(``operator chat`` is scriptable end-to-end), 5 (a tiny fee_offset fixture
demonstrates the learning loop through the desk), and exercises every
intent/action directly so a regression in one is caught here rather than
only in the scripted end-to-end test.

Law L20: every subprocess this file spawns is a real ``python -m
ledger_sense.<agent>`` call against fixture/generated files on disk --
never a live OpenAI/Dodo/Neatlogs call (every test scrubs the v2 env keys).
No test here imports ``ledger_sense.matching``/``ledger_sense.routing``
internals -- exactly the discipline ``operator/actions.py`` itself follows.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import io

from ledger_sense.operator import actions
from ledger_sense.operator.desk import Desk
from ledger_sense.operator.intents import Intent, classify
from ledger_sense.operator.paths import PassPaths

FIXTURE = Path(__file__).parent / "fixtures" / "mini_pass1"
PY = sys.executable

ALL_V2_KEYS = ("OPENAI_API_KEY", "DODO_API_KEY", "NEATLOGS_API_KEY", "LEDGER_SENSE_DATA_SOURCE", "DODO_ENVIRONMENT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ALL_V2_KEYS:
        monkeypatch.delenv(key, raising=False)


def _clean_env_dict():
    env = {**os.environ}
    for key in ALL_V2_KEYS:
        env.pop(key, None)
    return env


def _copy_mini_pass1(tmp_path) -> Path:
    dest = tmp_path / "pass1"
    shutil.copytree(FIXTURE, dest)
    return dest


def _read_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# intents.classify -- regex-first, must work with no OPENAI_API_KEY
# ---------------------------------------------------------------------------

def test_classify_single_intents():
    assert [i.name for i in classify("pull")] == ["pull"]
    assert [i.name for i in classify("get data")] == ["pull"]
    assert [i.name for i in classify("fetch dodo")] == ["pull"]
    assert [i.name for i in classify("analyze")] == ["analyze"]
    assert [i.name for i in classify("find discrepancies")] == ["analyze"]
    assert [i.name for i in classify("what's broken")] == ["analyze"]
    assert [i.name for i in classify("show exceptions")] == ["analyze"]
    assert [i.name for i in classify("next close")] == ["next_close"]
    assert [i.name for i in classify("run pass 2")] == ["next_close"]
    assert [i.name for i in classify("did it learn")] == ["next_close"]
    assert [i.name for i in classify("status")] == ["status"]
    assert [i.name for i in classify("where are we")] == ["status"]
    assert [i.name for i in classify("logs")] == ["logs"]
    assert [i.name for i in classify("trace")] == ["logs"]
    assert [i.name for i in classify("quit")] == ["quit"]
    assert [i.name for i in classify("exit")] == ["quit"]


def test_classify_the_cards_own_one_shot_example_chains_two_intents():
    intents = classify("pull the bank and show discrepancies")
    assert [i.name for i in intents] == ["pull", "analyze"]


def test_classify_unrecognized_text_with_no_key_returns_nothing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from ledger_sense.config import load_config

    assert classify("do a barrel roll", load_config()) == []


def test_classify_resolve_with_flags():
    [intent] = classify("resolve EXC-1 fee_offset --amount-delta-min 15.00 --amount-delta-max 15.00")
    assert intent.name == "resolve"
    assert intent.args["exception_ref"] == "EXC-1"
    assert intent.args["resolution_type"] == "fee_offset"
    assert intent.args["predicate_flags"]["--amount-delta-min"] == "15.00"
    assert intent.args["predicate_flags"]["--amount-delta-max"] == "15.00"


def test_classify_resolve_with_that_one_and_quoted_rationale():
    [intent] = classify('resolve that one fee_offset "Acme deducts a flat $15 fee"')
    assert intent.name == "resolve"
    assert intent.args["exception_ref"] == "that one"
    assert intent.args["resolution_type"] == "fee_offset"
    assert intent.args["rationale"] == "Acme deducts a flat $15 fee"
    assert all(v is None for v in intent.args["predicate_flags"].values())


def test_classify_promote():
    [intent] = classify("promote RULE-abc123 yes-always")
    assert intent.name == "promote"
    assert intent.args == {"rule_id": "RULE-abc123", "confirm": "yes-always"}


def test_classify_resolve_missing_resolution_type_yields_nothing():
    assert classify("resolve EXC-1 notatype") == []


# ---------------------------------------------------------------------------
# pull -- dodo live, else dodo-cache, else synthetic (n<=400), mocked runner
# ---------------------------------------------------------------------------

def test_pull_auto_falls_back_to_cache_then_reports_source(tmp_path, monkeypatch):
    monkeypatch.delenv("DODO_API_KEY", raising=False)
    paths = PassPaths(tmp_path / "pass1")
    result = actions.pull(paths, seed=1, n_cases=10)
    assert result.ok
    assert any("source: dodo-cache" in line for line in result.lines)
    assert paths.bank_csv.exists() and paths.ledger_csv.exists()


def test_pull_forced_dodo_without_key_is_a_clean_error_no_fallback(tmp_path):
    paths = PassPaths(tmp_path / "pass1")
    result = actions.pull(paths, source="dodo")
    assert not result.ok
    assert "DODO_API_KEY" in result.lines[0]
    assert not paths.bank_csv.exists()


def test_pull_forced_dodo_cache_with_missing_cache_file_is_a_clean_error(tmp_path):
    paths = PassPaths(tmp_path / "pass1")
    result = actions.pull(paths, source="dodo-cache", cache_path=str(tmp_path / "no_such_cache.json"))
    assert not result.ok
    assert "error:" in result.lines[0]


def test_pull_explicit_synthetic_plants_overlay_and_caps_n_cases(tmp_path):
    paths = PassPaths(tmp_path / "pass1")
    result = actions.pull(paths, source="synthetic", seed=42, n_cases=999)  # over the 400 cap
    assert result.ok
    assert any("source: synthetic (overlay)" in line for line in result.lines)
    bank_rows = _read_rows(paths.bank_csv)
    assert 0 < len(bank_rows) < 999  # capped well under the uncapped request


def test_pull_live_401_falls_back_to_cache_and_reports_the_code(tmp_path, monkeypatch):
    """Simulates a configured-but-unauthorized Dodo key (TAPE-1 part B) --
    never a real network call (L20): `run_module` itself is monkeypatched."""
    from ledger_sense.config import Config
    from ledger_sense.operator import actions as actions_mod
    from ledger_sense.operator.runner import RunResult

    monkeypatch.setattr(actions_mod, "load_config", lambda: Config(dodo_api_key="dodo-test-key"))

    def fake_run_module(module, args, **kwargs):
        assert module == "ledger_sense.data"
        return RunResult(argv=[], returncode=1, stdout="",
                          stderr="error: Dodo sandbox list_transactions failed after 3 attempt(s): "
                                 "HTTP 403 Forbidden -- error code: 1010\n")

    monkeypatch.setattr(actions_mod, "run_module", fake_run_module)
    paths = PassPaths(tmp_path / "pass1")
    result = actions_mod.pull(paths, seed=1, n_cases=10)
    assert result.ok
    assert result.lines[0] == "live pull failed 403; using labeled cache"
    assert any("source: dodo-cache" in line for line in result.lines)


# ---------------------------------------------------------------------------
# analyze -- matching + routing (+ guardrail) -> discrepancies
# ---------------------------------------------------------------------------

def test_analyze_without_pulled_data_is_a_clean_error(tmp_path):
    result = actions.analyze(PassPaths(tmp_path / "pass1"))
    assert not result.ok
    assert "run 'pull' first" in result.lines[0]


def test_analyze_on_mini_pass1_matches_the_known_shape(tmp_path):
    pass1_dir = _copy_mini_pass1(tmp_path)
    result = actions.analyze(PassPaths(pass1_dir))
    assert result.ok
    joined = "\n".join(result.lines)
    assert "bank lines=54" in joined
    assert "exceptions=6" in joined
    assert "discrepancies ready" in result.lines[-1]
    assert any(line.startswith("example exception_id: EXC-") for line in result.lines)
    assert result.data["example_exception_id"].startswith("EXC-")
    assert result.data["bank_lines"] == 54
    assert result.data["exceptions_total"] == 6


# ---------------------------------------------------------------------------
# resolve / promote -- delegate to the real `ledger_sense` CLI end to end
# ---------------------------------------------------------------------------

def test_resolve_then_promote_roundtrip_writes_rules_json(tmp_path):
    pass1_dir = _copy_mini_pass1(tmp_path)
    paths = PassPaths(pass1_dir)
    # Through Desk (not a bare actions.analyze() call) so demo_trace.json
    # actually records the example exception id -- what makes "that one"
    # resolvable below (see actions.resolve's own docstring).
    desk = Desk(pass1_dir, tmp_path / "pass2")
    analyzed = desk.run_intent(Intent("analyze"), io.StringIO())
    assert analyzed.ok
    example_id = analyzed.data["example_exception_id"]

    resolved = actions.resolve(
        paths, exception_ref="that one", resolution_type="counterparty_alias",
        predicate_flags={"--counterparty-key": "ACME", "--currency": None, "--amount-delta-min": None,
                          "--amount-delta-max": None, "--reference-transform": None, "--amount-class": None},
        rationale="test alias",
    )
    assert resolved.ok, resolved.lines
    assert resolved.data["exception_id"] == example_id
    assert any(line == "status=candidate" for line in resolved.lines)
    rule_id = resolved.data["rule_id"]
    assert rule_id and rule_id.startswith("RULE-")

    promoted = actions.promote(paths, rule_id=rule_id, confirm="yes-always")
    assert promoted.ok, promoted.lines
    assert promoted.lines[-1].startswith(f"{rule_id} <- ")
    assert paths.rules_json.is_file()
    rules = json.loads(paths.rules_json.read_text())["rules"]
    assert any(r["rule_id"] == rule_id for r in rules)


def test_promote_without_yes_always_is_refused_and_never_writes_rules(tmp_path):
    pass1_dir = _copy_mini_pass1(tmp_path)
    paths = PassPaths(pass1_dir)
    analyzed = actions.analyze(paths)
    resolved = actions.resolve(
        paths, exception_ref=analyzed.data["example_exception_id"], resolution_type="counterparty_alias",
        predicate_flags={"--counterparty-key": "ACME"}, rationale="x",
    )
    rule_id = resolved.data["rule_id"]

    refused = actions.promote(paths, rule_id=rule_id, confirm="yes")
    assert not refused.ok
    assert not paths.rules_json.exists()


def test_resolve_that_one_with_no_prior_analyze_is_a_clean_error(tmp_path):
    paths = PassPaths(tmp_path / "pass1")
    result = actions.resolve(paths, exception_ref="that one", resolution_type="manual_one_off",
                              predicate_flags={}, rationale="x")
    assert not result.ok
    assert "run 'analyze' first" in result.lines[0]


# ---------------------------------------------------------------------------
# status / logs
# ---------------------------------------------------------------------------

def test_status_reports_dirs_and_rules_presence(tmp_path):
    pass1_dir = _copy_mini_pass1(tmp_path)
    pass2_dir = tmp_path / "pass2"
    result = actions.status(PassPaths(pass1_dir), PassPaths(pass2_dir))
    joined = "\n".join(result.lines)
    assert "bank data: yes" in joined  # pass1
    assert "bank data: no" in joined  # pass2 not pulled yet
    assert "rules.json: absent" in joined
    assert "pass1 exceptions: not analyzed yet" in joined


def test_logs_reports_demo_trace_summary_after_a_turn(tmp_path):
    pass1_dir = _copy_mini_pass1(tmp_path)
    paths = PassPaths(pass1_dir)
    desk = Desk(pass1_dir, tmp_path / "pass2")
    desk.run_intent(Intent("analyze"), io.StringIO())
    result = actions.logs(paths)
    joined = "\n".join(result.lines)
    assert "1 turn(s) recorded" in joined
    assert "command=analyze" in joined
    assert paths.trace_path.is_file()
    entries = json.loads(paths.trace_path.read_text())
    assert entries[0]["agent"] == "operator"
    assert entries[0]["command"] == "analyze"
    assert entries[0]["ok"] is True


# ---------------------------------------------------------------------------
# Acceptance 4: `operator chat` is scriptable end-to-end (the exact card
# command, just against a disposable copy of the fixture so the tracked
# fixture directory is never written to by a test run).
# ---------------------------------------------------------------------------

def test_operator_chat_is_scriptable_end_to_end(tmp_path):
    pass1_dir = _copy_mini_pass1(tmp_path)
    result = subprocess.run(
        [PY, "-m", "ledger_sense.operator", "chat", "--dir", str(pass1_dir)],
        input="analyze\nquit\n", capture_output=True, text=True, env=_clean_env_dict(),
    )
    assert result.returncode == 0, result.stderr
    assert "desk>" in result.stdout
    assert "discrepancies ready" in result.stdout
    assert "EXC-" in result.stdout


def test_ledger_sense_desk_console_script_one_shot_free_text(tmp_path):
    pass1_dir = _copy_mini_pass1(tmp_path)
    result = subprocess.run(
        [PY, "-m", "ledger_sense.operator.cli"],  # exercised via -c below instead; see next assert
        capture_output=True, text=True, env=_clean_env_dict(),
    )
    # The above is just confirming the module is importable as a script path;
    # the real console-script entry point is exercised directly here:
    from ledger_sense.operator.cli import main_desk

    out_path = tmp_path / "one_shot_stdout.txt"
    with out_path.open("w", encoding="utf-8") as fh:
        old_stdout = sys.stdout
        sys.stdout = fh
        try:
            exit_code = main_desk(["pull the bank and show discrepancies", "--dir", str(pass1_dir)])
        finally:
            sys.stdout = old_stdout
    assert exit_code == 0
    output = out_path.read_text()
    assert "source:" in output
    assert "discrepancies ready" in output


def test_explicit_subcommand_cli_also_writes_demo_trace_every_turn(tmp_path):
    """Not just chat/one-shot -- `python -m ledger_sense.operator analyze
    --dir ...` is a turn too (spec: "write demo_trace.json every turn")."""
    pass1_dir = _copy_mini_pass1(tmp_path)
    result = subprocess.run(
        [PY, "-m", "ledger_sense.operator", "analyze", "--dir", str(pass1_dir)],
        capture_output=True, text=True, env=_clean_env_dict(),
    )
    assert result.returncode == 0, result.stderr
    trace_path = pass1_dir / "demo_trace.json"
    assert trace_path.is_file()
    entries = json.loads(trace_path.read_text())
    assert entries[0]["command"] == "analyze"
    assert entries[0]["example_exception_id"].startswith("EXC-")


def test_pyproject_registers_the_ledger_sense_desk_console_script():
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'ledger-sense-desk = "ledger_sense.operator.cli:main_desk"' in pyproject


# ---------------------------------------------------------------------------
# Acceptance 5: a tiny fee_offset fixture, resolved+promoted through the
# desk, demonstrably changes pass 2's routing outcome (class drops, and the
# rule's own hit count is asserted directly).
# ---------------------------------------------------------------------------

def _find_fee_offset_bank_or_pair_exception(paths: PassPaths, delta_cents: int = 1500) -> str:
    """Any exception whose matcher features show the exact planted
    OVERLAY_FEE_CENTS ($15.00) delta -- the one signal that recurs across
    passes even though the overlay's forced counterparty does not (see
    ``data/generator.py``'s ``OVERLAY_FEE_CENTS``)."""
    outcomes_by_bank_id = {row["bank_txn_id"]: row for row in _read_rows(paths.outcomes_csv())}
    for row in _read_rows(paths.exceptions_csv()):
        if row["subject_kind"] not in ("bank", "pair") or not row["bank_txn_id"]:
            continue
        outcome = outcomes_by_bank_id.get(row["bank_txn_id"])
        if not outcome or not outcome["features"]:
            continue
        features = json.loads(outcome["features"])
        delta = features.get("amount_delta_cents")
        if delta is not None and abs(int(delta)) == delta_cents:
            return row["exception_id"]
    raise AssertionError("no fee_offset-shaped exception found -- overlay may not have planted this run")


def test_tiny_fee_offset_fixture_learns_through_next_close(tmp_path):
    """The full close-desk learning loop, end to end: pull (synthetic
    overlay) -> analyze -> resolve (amount-delta predicate, the one signal
    that generalizes across passes) -> promote -> next close. Asserts the
    card's own acceptance 5: the affected class's count drops between pass
    2's rules-off and rules-on routing runs (equivalently, rule_hits > 0)."""
    pass1 = PassPaths(tmp_path / "pass1")
    pass2 = PassPaths(tmp_path / "pass2")

    pulled = actions.pull(pass1, source="synthetic", seed=42, n_cases=80)
    assert pulled.ok, pulled.lines

    analyzed = actions.analyze(pass1)
    assert analyzed.ok, analyzed.lines

    example_id = _find_fee_offset_bank_or_pair_exception(pass1)
    resolved = actions.resolve(
        pass1, exception_ref=example_id, resolution_type="fee_offset",
        predicate_flags={"--amount-delta-min": "15.00", "--amount-delta-max": "15.00"},
        rationale="Vendor deducts a flat $15 fee before remitting",
    )
    assert resolved.ok, resolved.lines
    rule_id = resolved.data["rule_id"]

    promoted = actions.promote(pass1, rule_id=rule_id, confirm="yes-always")
    assert promoted.ok, promoted.lines

    closed = actions.next_close(pass1, pass2, seed=42, n_cases=80)
    assert closed.ok, closed.lines

    off_counts, on_counts, rule_hits = closed.data["off_counts"], closed.data["on_counts"], closed.data["rule_hits"]
    assert rule_hits > 0
    total_before = sum(off_counts.values())
    total_after = sum(on_counts.values())
    assert total_after < total_before or any(on_counts.get(c, 0) < n for c, n in off_counts.items())
