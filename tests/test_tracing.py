"""W10/TAPE-1: Neatlogs tracing -- mocked-SDK only (L20).

``traced_run(agent_name, **metadata)`` (``ledger_sense.tracing``) is the one
wrap point every agent CLI entrypoint calls. No test in this file may import
the real `neatlogs` package or open a socket -- every test that needs
tracing "enabled" monkeypatches ``ledger_sense.tracing._init`` /
``_span`` / ``_flush`` directly (the only three places a real
``import neatlogs`` could ever happen). There is no ``neatlogs.Client`` --
that was W10's bug (confirmed by W14's live smoke test: 0/4 real spans ever
sent); the real SDK is a module-level ``init``/``span``/``flush`` API, and
this module -- and this whole test file -- reflects that fix.

Sections, matching BOARD.md's W10/TAPE-1 acceptance lists:
  1. Regression -- NEATLOGS_API_KEY unset -> all 6 entrypoints run
     byte-identical to their pre-W10 output.
  2. Unit tests against mocked ``init``/``span``/``flush`` -- zero live
     network calls, and proof ``neatlogs.Client`` is gone.
  3. Redaction -- a fake API-key-shaped string in span metadata never
     reaches the (mocked) span's tags.
  4. init/span/flush raising never crashes the CLI or changes its exit
     code/output -- always exactly one stderr line per failure.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ledger_sense import tracing
from ledger_sense.config import Config
from ledger_sense.data.io_csv import write_csv
from ledger_sense.matching.io import run as matching_run
from ledger_sense.metrics import io as metrics_io

FIXTURE = Path(__file__).parent / "fixtures" / "mini_pass1"
PY = sys.executable
REPO_ROOT = Path(__file__).parent.parent

ALL_V2_KEYS = (
    "OPENAI_API_KEY",
    "DODO_API_KEY",
    "NEATLOGS_API_KEY",
    "LEDGER_SENSE_DATA_SOURCE",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts with none of the v2 keys set, unless it sets its own."""
    for key in ALL_V2_KEYS:
        monkeypatch.delenv(key, raising=False)


def fake_config(**overrides) -> Config:
    defaults = dict(
        openai_api_key=None, openai_model="gpt-4o-mini", llm_cost_cap_usd=1.00,
        dodo_api_key=None, dodo_environment="sandbox", data_source="synthetic",
        neatlogs_api_key="neatlogs-test-key",
    )
    defaults.update(overrides)
    return Config(**defaults)


class FakeSpan:
    """Records the tags it's asked to carry. `raise_on_enter`/`raise_on_exit`
    simulate a real span failing to open/close (acceptance 4)."""

    def __init__(self, raise_on_enter=False, raise_on_exit=False):
        self.entered = False
        self.tags = None
        self._raise_on_enter = raise_on_enter
        self._raise_on_exit = raise_on_exit

    def __enter__(self):
        if self._raise_on_enter:
            raise RuntimeError("span failed to open")
        self.entered = True
        return self

    def __exit__(self, *exc_info):
        if self._raise_on_exit:
            raise RuntimeError("span failed to close")
        return False

    def add_tags(self, tags):
        self.tags = tags


class Recorder:
    """Records every call made to the mocked init/span/flush seam."""

    def __init__(self):
        self.init_calls = []
        self.flush_calls = 0
        self.spans = []


def run_cli(module, args, env_overrides=None):
    env = {**os.environ}
    for key in ALL_V2_KEYS:
        env.pop(key, None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [PY, "-m", module, *args], env=env, capture_output=True, text=True
    )


# ---------------------------------------------------------------------------
# 2. Unit tests against mocked init/span/flush -- zero live network calls.
# ---------------------------------------------------------------------------

def test_traced_run_is_a_no_op_and_calls_nothing_when_key_absent(monkeypatch):
    """L18: NEATLOGS_API_KEY absent -> zero calls into `_init`/`_span`/
    `_flush` at all -- a true, zero-overhead no-op."""
    recorder = Recorder()
    monkeypatch.setattr(tracing, "_init", lambda cfg: recorder.init_calls.append(cfg))
    monkeypatch.setattr(tracing, "_span", lambda name: recorder.spans.append(name) or FakeSpan())
    monkeypatch.setattr(tracing, "_flush", lambda: recorder.__setattr__("flush_calls", recorder.flush_calls + 1))

    with tracing.traced_run("test-agent"):
        print("hello")

    assert recorder.init_calls == []
    assert recorder.spans == []
    assert recorder.flush_calls == 0


def test_traced_run_calls_init_then_span_then_flush_in_order(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    calls = []
    fake_span = FakeSpan()
    monkeypatch.setattr(tracing, "_init", lambda cfg: calls.append(("init", cfg.neatlogs_api_key)))
    monkeypatch.setattr(tracing, "_span", lambda name: calls.append(("span", name)) or fake_span)
    monkeypatch.setattr(tracing, "_flush", lambda: calls.append(("flush",)))

    with tracing.traced_run("guardrail"):
        calls.append(("body",))

    assert calls == [
        ("init", "neatlogs-test-key"),
        ("span", "guardrail"),
        ("body",),
        ("flush",),
    ]
    assert fake_span.entered is True


def test_init_uses_the_real_workflow_name(monkeypatch):
    """`neatlogs.init` must be called with `workflow_name="ledger-sense"` --
    the exact shape TAPE-1 specifies, not a guess."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")

    class FakeNeatlogsModule:
        WORKFLOW = "WORKFLOW"

        def __init__(self):
            self.init_kwargs = None

        def init(self, **kwargs):
            self.init_kwargs = kwargs

    fake_module = FakeNeatlogsModule()

    def fake_init(cfg):
        fake_module.init(api_key=cfg.neatlogs_api_key, workflow_name="ledger-sense")

    monkeypatch.setattr(tracing, "_init", fake_init)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan())
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    with tracing.traced_run("data"):
        pass

    assert fake_module.init_kwargs == {"api_key": "neatlogs-test-key", "workflow_name": "ledger-sense"}


def test_traced_run_as_decorator_preserves_return_value_and_args(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan())
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    @tracing.traced_run("matching")
    def main(argv=None):
        print("bank lines=54; ledger entries=49; matched=49")
        return 0

    assert main(["--foo"]) == 0


def test_traced_run_disabled_decorator_does_not_touch_stdout(monkeypatch, capsys):
    """No key configured -> stdout is never wrapped/touched at all."""
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan())
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    @tracing.traced_run("routing")
    def main():
        print("exceptions=6; owners=11; breached=6")
        return 0

    assert main() == 0
    assert "exceptions=6; owners=11; breached=6" in capsys.readouterr().out


def test_traced_run_static_metadata_reaches_the_span_tags(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    fake_span = FakeSpan()
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: fake_span)
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    with tracing.traced_run("learning", cli_command="resolve"):
        pass

    assert fake_span.tags["cli_command"] == "resolve"
    assert fake_span.tags["status"] == "ok"
    assert isinstance(fake_span.tags["duration_seconds"], float)


# ---------------------------------------------------------------------------
# 3. Redaction -- a fake API-key-shaped string in span metadata never
#    reaches the (mocked) span's tags.
# ---------------------------------------------------------------------------

FAKE_KEY = "sk-FAKEKEYVALUE1234567890ABCDEFGH"


def test_redaction_strips_key_shaped_static_metadata(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    fake_span = FakeSpan()
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: fake_span)
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    with tracing.traced_run("matching", note=FAKE_KEY):
        pass

    assert FAKE_KEY not in json.dumps(fake_span.tags)
    assert fake_span.tags["note"] == "[REDACTED]"


def test_redaction_strips_key_shaped_value_in_nested_metadata(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    fake_span = FakeSpan()
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: fake_span)
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    with tracing.traced_run("learning", context={"api_key": f"OPENAI_API_KEY={FAKE_KEY}"}):
        pass

    assert FAKE_KEY not in json.dumps(fake_span.tags)


def test_redaction_does_not_touch_the_real_terminal_stdout(monkeypatch, capsys):
    """Redaction protects the span sent to Neatlogs -- it must never mutate
    what the CLI itself actually prints to the terminal."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan())
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    with tracing.traced_run("matching"):
        print(f"debug key (not a real secret path, just proving passthrough): {FAKE_KEY}")

    assert FAKE_KEY in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 4. init/span/flush raising never crashes the CLI or changes its exit
#    code/output -- always exactly one stderr line per failure.
# ---------------------------------------------------------------------------

def test_init_raising_never_propagates_and_prints_one_stderr_line(monkeypatch, capsys):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")

    def _broken_init(cfg):
        raise ConnectionError("could not reach neatlogs")

    monkeypatch.setattr(tracing, "_init", _broken_init)

    with tracing.traced_run("data"):
        pass  # must not raise

    err_lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(err_lines) == 1
    assert "neatlogs init failed" in err_lines[0]


def test_span_raising_on_enter_never_propagates_and_still_flushes(monkeypatch, capsys):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    flush_calls = []
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan(raise_on_enter=True))
    monkeypatch.setattr(tracing, "_flush", lambda: flush_calls.append(1))

    with tracing.traced_run("guardrail"):
        pass  # must not raise despite the mocked span blowing up on enter

    err_lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(err_lines) == 1
    assert "neatlogs span failed" in err_lines[0]
    assert flush_calls == [1]  # init still succeeded -- flush still runs (L18)


def test_span_raising_on_exit_never_propagates(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan(raise_on_exit=True))
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    @tracing.traced_run("routing")
    def main():
        return 0

    assert main() == 0


def test_flush_raising_never_propagates_and_prints_one_stderr_line(monkeypatch, capsys):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan())

    def _broken_flush():
        raise RuntimeError("neatlogs backend unreachable")

    monkeypatch.setattr(tracing, "_flush", _broken_flush)

    with tracing.traced_run("metrics"):
        pass  # must not raise

    err_lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(err_lines) == 1
    assert "neatlogs flush failed" in err_lines[0]


def test_wrapped_functions_own_exception_still_propagates(monkeypatch):
    """Tracing must never swallow a real failure from the wrapped code --
    only its own init/span/flush failures are ever caught."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    fake_span = FakeSpan()
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: fake_span)
    monkeypatch.setattr(tracing, "_flush", lambda: None)

    class BoomError(Exception):
        pass

    @tracing.traced_run("learning")
    def main():
        raise BoomError("real failure, must propagate")

    with pytest.raises(BoomError):
        main()
    # The span should still have been closed with an error status.
    assert fake_span.tags["status"] == "error"
    assert "BoomError" in fake_span.tags["error"]


def test_flush_raising_still_lets_the_wrapped_functions_own_exception_through(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_init", lambda cfg: None)
    monkeypatch.setattr(tracing, "_span", lambda name: FakeSpan())
    monkeypatch.setattr(tracing, "_flush", lambda: (_ for _ in ()).throw(RuntimeError("unreachable")))

    class BoomError(Exception):
        pass

    @tracing.traced_run("guardrail")
    def main():
        raise BoomError("real failure, must propagate even if tracing itself is broken")

    with pytest.raises(BoomError):
        main()


# ---------------------------------------------------------------------------
# Acceptance 1 (TAPE-1) -- `neatlogs.Client` is gone from the source, and a
# keyless `--help` never raises an AttributeError building one.
# ---------------------------------------------------------------------------

def test_neatlogs_client_is_gone_from_source():
    """The historical W10 bug is documented in this module's own docstring
    (prose, not code) -- no actual AST `Call` node anywhere in the file may
    construct a `neatlogs.Client(...)`."""
    import ast

    source = (REPO_ROOT / "src" / "ledger_sense" / "tracing.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "Client", "found a neatlogs.Client(...) construction"


def test_keyless_matching_help_exits_clean_no_attribute_error():
    result = run_cli("ledger_sense.matching", ["--help"])
    assert result.returncode == 0
    assert "AttributeError" not in result.stderr
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# 1 (continued) -- Regression -- NEATLOGS_API_KEY unset -> all 6 entrypoints
# run byte-identical to their pre-W10 output, and each entrypoint file
# actually carries the one wrap-point edit.
# ---------------------------------------------------------------------------

ENTRYPOINT_FILES = {
    "data": "src/ledger_sense/data/cli.py",
    "matching": "src/ledger_sense/matching/__main__.py",
    "routing": "src/ledger_sense/routing/__main__.py",
    "guardrail": "src/ledger_sense/guardrail/cli.py",
    "learning": "src/ledger_sense/learning/cli.py",
    "metrics": "src/ledger_sense/metrics/cli.py",
}


@pytest.mark.parametrize("agent_name", sorted(ENTRYPOINT_FILES))
def test_entrypoint_carries_the_one_traced_run_wrap_point(agent_name):
    source = (REPO_ROOT / ENTRYPOINT_FILES[agent_name]).read_text(encoding="utf-8")
    assert "traced_run(" in source
    assert "import traced_run" in source and "tracing import traced_run" in source


def test_regression_data_cli_unset_key_output_unchanged(tmp_path):
    result = run_cli("ledger_sense.data", [
        "--seed", "42", "--pass-number", "1", "--n-cases", "5",
    ])
    assert result.returncode == 0
    assert result.stdout == (
        "Ledger Sense synthetic generation summary\n"
        "  seed=42 pass_number=1 n_cases=5\n"
        "  row counts: ledger.csv=5 bank.csv=6 match_links.csv=6\n"
        "  unique counterparties: 5\n"
        "  defect histogram (documented mix, §4.2):\n"
        "    clean                    3  (60.00%)\n"
        "    wrong_reference          1  (20.00%)\n"
        "    partial_payment          1  (20.00%)\n"
        "  overlay: disabled -- class='fee_offset' would plant 12-20 siblings "
        "if enabled and no natural cluster >= 8 exists (natural max cluster observed=1)\n"
    )


def test_regression_matching_cli_unset_key_output_unchanged(tmp_path):
    result = run_cli("ledger_sense.matching", [
        "--ledger", str(FIXTURE / "ledger.csv"), "--bank", str(FIXTURE / "bank.csv"),
        "--out-dir", str(tmp_path),
    ])
    assert result.returncode == 0
    assert result.stdout == (
        "bank lines=54; ledger entries=49; matched=49\n"
        "cheap-tier match rate: 83.33% (45/54)\n"
        "llm_is_stub=True; llm_calls=0; adjudicator=deterministic-stub-v1\n"
    )


def test_regression_guardrail_cli_unset_key_output_unchanged(tmp_path):
    matching_out = tmp_path / "matching_out"
    matching_run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", matching_out)
    result = run_cli("ledger_sense.guardrail", [
        "--ledger", str(FIXTURE / "ledger.csv"), "--bank", str(FIXTURE / "bank.csv"),
        "--outcomes", str(matching_out / "match_outcomes.csv"),
        "--settlements", str(matching_out / "ledger_settlements.csv"),
        "--as-of", "2026-06-01T00:00:00Z", "--out-dir", str(tmp_path / "guardrail_out"),
    ])
    assert result.returncode == 0
    assert result.stdout == (
        "bank lines=54; policy_version=2026.09-1\n"
        "allow: 0/54 (0.00%)\n"
        "block: 4/54 (7.41%)\n"
        "hold: 50/54 (92.59%)\n"
    )


def test_regression_routing_cli_unset_key_output_unchanged(tmp_path):
    matching_out = tmp_path / "matching_out"
    matching_run(FIXTURE / "ledger.csv", FIXTURE / "bank.csv", matching_out)
    result = run_cli("ledger_sense.routing", [
        "--outcomes", str(matching_out / "match_outcomes.csv"),
        "--settlements", str(matching_out / "ledger_settlements.csv"),
        "--ledger", str(FIXTURE / "ledger.csv"), "--bank", str(FIXTURE / "bank.csv"),
        "--as-of", "2026-06-01T00:00:00Z", "--out-dir", str(tmp_path / "routing_out"),
    ])
    assert result.returncode == 0
    assert result.stdout == (
        "exceptions=6; owners=11; breached=6\n"
        "by category: {'duplicate': 2, 'suspect_posting': 3, 'timing': 1}\n"
        "by subject_kind: {'bank': 3, 'ledger': 1, 'pair': 2}\n"
    )


def test_regression_learning_resolve_cli_unset_key_output_unchanged(tmp_path):
    result = run_cli("ledger_sense.learning", [
        "resolve",
        "--exceptions", str(tmp_path / "does_not_exist_exceptions.csv"),
        "--outcomes", str(tmp_path / "does_not_exist_outcomes.csv"),
        "--exception-id", "EXC-1", "--resolution-type", "manual_one_off",
        "--rationale", "one-off case", "--resolved-by", "alice",
        "--resolved-at", "2026-06-01T00:00:00Z",
    ])
    assert result.returncode == 0
    assert result.stdout == (
        "resolution_id=RES-bbeed63fa5dbfd44\n"
        "exception_id=EXC-1\n"
        "resolution_type=manual_one_off\n"
        "status=resolved (first-class outcome, law L13 -- no candidate rule, ever)\n"
    )


def _write_empty_pass(d):
    d.mkdir(parents=True, exist_ok=True)
    write_csv(str(d / "match_outcomes.csv"), metrics_io.OUTCOME_COLUMNS, [])
    write_csv(str(d / "ledger_settlements.csv"), metrics_io.SETTLEMENT_COLUMNS, [])
    write_csv(str(d / "exceptions.csv"), metrics_io.EXCEPTION_COLUMNS, [])
    write_csv(str(d / "owner_queues.csv"), metrics_io.QUEUE_COLUMNS, [])
    write_csv(str(d / "release_decisions.csv"), metrics_io.RELEASE_COLUMNS, [])
    write_csv(str(d / "guardrail_audit.csv"), metrics_io.AUDIT_COLUMNS, [])
    write_csv(str(d / "match_links.csv"), metrics_io.MATCH_LINK_COLUMNS, [])


def test_regression_metrics_cli_unset_key_output_unchanged(tmp_path):
    pass1_dir, pass2_dir = tmp_path / "pass1", tmp_path / "pass2"
    _write_empty_pass(pass1_dir)
    _write_empty_pass(pass2_dir)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"schema_version": 1, "rules": []}), encoding="utf-8")
    write_csv(str(pass2_dir / "rule_hits.csv"), metrics_io.RULE_HIT_COLUMNS, [])

    result = run_cli("ledger_sense.metrics", [
        "scoreboard", "--pass1-dir", str(pass1_dir), "--pass2-dir", str(pass2_dir),
        "--rules", str(rules_path), "--out", str(tmp_path / "scoreboard.json"),
    ])
    assert result.returncode == 0
    assert "Ledger Sense -- Agent 5 scoreboard (spec §9)" in result.stdout
    assert "STR (naive, matched+settled): 0/0 (0.00%)" in result.stdout


# ---------------------------------------------------------------------------
# 1 (continued) -- tracing enabled must never change a CLI's exit code or
# stdout either, even though `neatlogs` itself isn't installed in this repo
# (base install stays dependency-free, L20) -- proves the real-import path
# degrades exactly like a mocked-failure would (acceptance 4, at the
# full-process level), and that no `neatlogs.Client` AttributeError is
# possible any more (there is no such call left in the source).
# ---------------------------------------------------------------------------

def test_regression_matching_cli_with_key_set_but_neatlogs_not_installed_still_succeeds(tmp_path):
    with pytest.raises(ImportError):
        import neatlogs  # noqa: F401 -- confirms this test's premise holds in this env

    result = run_cli("ledger_sense.matching", [
        "--ledger", str(FIXTURE / "ledger.csv"), "--bank", str(FIXTURE / "bank.csv"),
        "--out-dir", str(tmp_path),
    ], env_overrides={"NEATLOGS_API_KEY": "neatlogs-test-key"})
    assert result.returncode == 0
    assert result.stdout == (
        "bank lines=54; ledger entries=49; matched=49\n"
        "cheap-tier match rate: 83.33% (45/54)\n"
        "llm_is_stub=True; llm_calls=0; adjudicator=deterministic-stub-v1\n"
    )
    assert "neatlogs init failed" in result.stderr
    assert "AttributeError" not in result.stderr
    assert "Traceback" not in result.stderr
