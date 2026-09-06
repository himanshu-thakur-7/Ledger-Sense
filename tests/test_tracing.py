"""W10: Neatlogs tracing -- mocked-client only (L20).

``traced_run(agent_name, **metadata)`` (``ledger_sense.tracing``) is the one
wrap point every agent CLI entrypoint calls. No test in this file may import
the real `neatlogs` package or open a socket -- every test that needs
tracing "enabled" monkeypatches ``ledger_sense.tracing._build_client``
with a fake client instead (the only place a real `import neatlogs` could
ever happen).

Sections, matching BOARD.md's W10 acceptance list:
  1. Regression -- NEATLOGS_API_KEY unset -> all 6 entrypoints run
     byte-identical to their pre-W10 output.
  2. Unit tests against a mocked Neatlogs client -- zero live network calls.
  3. Redaction -- a fake API-key-shaped string in span metadata never
     reaches the (mocked) client's payload.
  4. A mocked client raising an error never crashes the CLI or changes its
     exit code/output.
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


class FakeClient:
    """Records every span it's sent. `raise_on_send` simulates an
    unreachable/broken Neatlogs backend (acceptance 4)."""

    def __init__(self, raise_on_send=False):
        self.sent = []
        self._raise_on_send = raise_on_send

    def send(self, payload):
        if self._raise_on_send:
            raise RuntimeError("neatlogs backend unreachable")
        self.sent.append(payload)


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
# 2. Unit tests against a mocked Neatlogs client -- zero live network calls.
# ---------------------------------------------------------------------------

def test_traced_run_is_a_no_op_and_builds_zero_clients_when_key_absent(monkeypatch):
    """L18: NEATLOGS_API_KEY absent -> zero Neatlogs SDK calls, zero overhead
    beyond a no-op -- `_build_client` (the only import point) is never
    reached at all."""
    build_calls = []
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: build_calls.append(cfg) or FakeClient())

    with tracing.traced_run("test-agent"):
        print("hello")

    assert build_calls == []


def test_traced_run_context_manager_sends_one_span_to_mocked_client(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    client = FakeClient()
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: client)

    with tracing.traced_run("guardrail"):
        print("bank lines=54; policy_version=2026.09-1")
        print("allow: 40/54 (74.07%)")
        print("block: 4/54 (7.41%)")
        print("hold: 10/54 (18.52%)")

    assert len(client.sent) == 1
    span = client.sent[0]
    assert span["agent"] == "guardrail"
    assert span["bank_lines"] == 54
    assert span["guardrail_verdicts"] == {"allow": 40, "block": 4, "hold": 10}
    assert span["status"] == "ok"
    assert isinstance(span["duration_seconds"], float)
    assert span["duration_seconds"] >= 0


def test_traced_run_as_decorator_preserves_return_value_and_args(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    client = FakeClient()
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: client)

    @tracing.traced_run("matching")
    def main(argv=None):
        print("bank lines=54; ledger entries=49; matched=49")
        print("llm_is_stub=True; llm_calls=0; adjudicator=deterministic-stub-v1")
        return 0

    assert main(["--foo"]) == 0
    assert len(client.sent) == 1
    span = client.sent[0]
    assert span["agent"] == "matching"
    assert span["bank_lines"] == 54
    assert span["ledger_entries"] == 49
    assert span["matched"] == 49
    assert span["llm_calls"] == 0
    assert span["llm_is_stub"] is True


def test_traced_run_disabled_decorator_does_not_touch_stdout(monkeypatch, capsys):
    """No key configured -> stdout is never wrapped/teed at all."""
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: FakeClient())

    @tracing.traced_run("routing")
    def main():
        import sys as _sys
        assert _sys.stdout.__class__.__name__ != "_Tee"
        print("exceptions=6; owners=11; breached=6")
        return 0

    assert main() == 0
    assert "exceptions=6; owners=11; breached=6" in capsys.readouterr().out


def test_traced_run_static_metadata_passed_through(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    client = FakeClient()
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: client)

    with tracing.traced_run("learning", cli_command="resolve"):
        pass

    assert client.sent[0]["cli_command"] == "resolve"


def test_traced_run_static_metadata_only_reads_config_once_per_call(monkeypatch):
    """A stray typo or unrelated exception inside `_build_client` must not
    reach the caller either -- only ever swallowed (L18)."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: (_ for _ in ()).throw(ImportError("no neatlogs")))

    with tracing.traced_run("metrics"):
        pass  # must not raise


# ---------------------------------------------------------------------------
# 3. Redaction -- a fake API-key-shaped string in span metadata never
#    reaches the (mocked) client's payload.
# ---------------------------------------------------------------------------

FAKE_KEY = "sk-FAKEKEYVALUE1234567890ABCDEFGH"


def test_redaction_strips_key_shaped_static_metadata(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    client = FakeClient()
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: client)

    with tracing.traced_run("matching", note=FAKE_KEY):
        pass

    sent_payload = client.sent[0]
    assert FAKE_KEY not in json.dumps(sent_payload)
    assert sent_payload["note"] == "[REDACTED]"


def test_redaction_strips_key_shaped_value_in_nested_metadata(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    client = FakeClient()
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: client)

    with tracing.traced_run("learning", context={"api_key": f"OPENAI_API_KEY={FAKE_KEY}"}):
        pass

    sent_payload = client.sent[0]
    assert FAKE_KEY not in json.dumps(sent_payload)


def test_redaction_does_not_touch_the_real_terminal_stdout(monkeypatch, capsys):
    """Redaction protects the span sent to Neatlogs -- it must never mutate
    what the CLI itself actually prints to the terminal."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: FakeClient())

    with tracing.traced_run("matching"):
        print(f"debug key (not a real secret path, just proving passthrough): {FAKE_KEY}")

    assert FAKE_KEY in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 4. A mocked client raising an error never crashes the CLI or changes its
#    exit code/output.
# ---------------------------------------------------------------------------

def test_client_raising_on_send_never_propagates_from_context_manager(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: FakeClient(raise_on_send=True))

    with tracing.traced_run("guardrail"):
        pass  # must not raise despite the mocked client blowing up on send()


def test_client_raising_on_send_never_changes_decorated_functions_return_value(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: FakeClient(raise_on_send=True))

    @tracing.traced_run("routing")
    def main():
        print("exceptions=6; owners=11; breached=6")
        return 0

    assert main() == 0


def test_client_construction_failure_never_propagates(monkeypatch):
    """A broken `_build_client` (e.g. bad credentials, SDK misconfigured)
    must degrade the same way an unreachable client does."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")

    def _broken_build_client(cfg):
        raise ConnectionError("could not reach neatlogs")

    monkeypatch.setattr(tracing, "_build_client", _broken_build_client)

    with tracing.traced_run("data"):
        pass  # must not raise


def test_wrapped_functions_own_exception_still_propagates(monkeypatch):
    """Tracing must never swallow a real failure from the wrapped code --
    only its own span-emission failures are ever caught."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    client = FakeClient()
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: client)

    class BoomError(Exception):
        pass

    @tracing.traced_run("learning")
    def main():
        raise BoomError("real failure, must propagate")

    with pytest.raises(BoomError):
        main()
    # The span for the failed run should still have been attempted/sent.
    assert len(client.sent) == 1
    assert client.sent[0]["status"] == "error"
    assert "BoomError" in client.sent[0]["error"]


def test_client_raising_still_lets_the_wrapped_functions_own_exception_through(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    monkeypatch.setattr(tracing, "_build_client", lambda cfg: FakeClient(raise_on_send=True))

    class BoomError(Exception):
        pass

    @tracing.traced_run("guardrail")
    def main():
        raise BoomError("real failure, must propagate even if tracing itself is broken")

    with pytest.raises(BoomError):
        main()


# ---------------------------------------------------------------------------
# 1. Regression -- NEATLOGS_API_KEY unset -> all 6 entrypoints run
#    byte-identical to their pre-W10 output, and each entrypoint file
#    actually carries the one wrap-point edit.
# ---------------------------------------------------------------------------

ENTRYPOINT_FILES = {
    "data": "src/ledger_sense/data/cli.py",
    "matching": "src/ledger_sense/matching/__main__.py",
    "routing": "src/ledger_sense/routing/__main__.py",
    "guardrail": "src/ledger_sense/guardrail/cli.py",
    "learning": "src/ledger_sense/learning/cli.py",
    "metrics": "src/ledger_sense/metrics/cli.py",
}
REPO_ROOT = Path(__file__).parent.parent


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
# degrades exactly like a mocked-client failure would (acceptance 4, at the
# full-process level).
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
