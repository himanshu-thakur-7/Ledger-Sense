"""W8: config/secrets foundation — enabled/disabled gates and .env parsing.

L18 coverage: every *_enabled() gate must be False whenever its key is
absent, and True only when the key is actually present, regardless of any
other var. L20: no network, no real filesystem outside pytest's tmp_path.
"""

from pathlib import Path

import pytest

from ledger_sense.config import Config, find_dotenv, load_config


ALL_KEYS = (
    "OPENAI_API_KEY",
    "LEDGER_SENSE_OPENAI_MODEL",
    "LEDGER_SENSE_LLM_COST_CAP_USD",
    "DODO_API_KEY",
    "DODO_ENVIRONMENT",
    "LEDGER_SENSE_DATA_SOURCE",
    "NEATLOGS_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts with none of the v2 vars set, unless it sets its own."""
    for key in ALL_KEYS:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# openai_enabled()
# ---------------------------------------------------------------------------

def test_openai_disabled_when_key_absent():
    assert load_config(dotenv_path=Path("/nonexistent/.env")).openai_enabled() is False


def test_openai_enabled_when_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    assert load_config(dotenv_path=Path("/nonexistent/.env")).openai_enabled() is True


def test_openai_disabled_when_key_is_blank_string(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert load_config(dotenv_path=Path("/nonexistent/.env")).openai_enabled() is False


# ---------------------------------------------------------------------------
# dodo_enabled()
# ---------------------------------------------------------------------------

def test_dodo_disabled_when_key_absent():
    assert load_config(dotenv_path=Path("/nonexistent/.env")).dodo_enabled() is False


def test_dodo_enabled_when_key_present(monkeypatch):
    monkeypatch.setenv("DODO_API_KEY", "dodo-test-key")
    assert load_config(dotenv_path=Path("/nonexistent/.env")).dodo_enabled() is True


# ---------------------------------------------------------------------------
# tracing_enabled()
# ---------------------------------------------------------------------------

def test_tracing_disabled_when_key_absent():
    assert load_config(dotenv_path=Path("/nonexistent/.env")).tracing_enabled() is False


def test_tracing_enabled_when_key_present(monkeypatch):
    monkeypatch.setenv("NEATLOGS_API_KEY", "neatlogs-test-key")
    assert load_config(dotenv_path=Path("/nonexistent/.env")).tracing_enabled() is True


# ---------------------------------------------------------------------------
# Gates are independent of each other
# ---------------------------------------------------------------------------

def test_gates_are_independent(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.openai_enabled() is True
    assert cfg.dodo_enabled() is False
    assert cfg.tracing_enabled() is False


def test_all_disabled_by_default():
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.openai_enabled() is False
    assert cfg.dodo_enabled() is False
    assert cfg.tracing_enabled() is False


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def test_defaults_match_locked_decisions():
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.openai_model == "gpt-4o-mini"
    assert cfg.llm_cost_cap_usd == 1.00
    assert cfg.dodo_environment == "sandbox"
    assert cfg.data_source == "synthetic"


def test_overrides_from_env(monkeypatch):
    monkeypatch.setenv("LEDGER_SENSE_OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("LEDGER_SENSE_LLM_COST_CAP_USD", "5.50")
    monkeypatch.setenv("DODO_ENVIRONMENT", "sandbox2")
    monkeypatch.setenv("LEDGER_SENSE_DATA_SOURCE", "dodo")
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.openai_model == "gpt-4o"
    assert cfg.llm_cost_cap_usd == 5.50
    assert cfg.dodo_environment == "sandbox2"
    assert cfg.data_source == "dodo"


def test_malformed_cost_cap_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LEDGER_SENSE_LLM_COST_CAP_USD", "not-a-number")
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.llm_cost_cap_usd == 1.00


# ---------------------------------------------------------------------------
# using_dodo_source(): opt-in AND key present, else graceful fallback (L18)
# ---------------------------------------------------------------------------

def test_using_dodo_source_false_without_key(monkeypatch):
    monkeypatch.setenv("LEDGER_SENSE_DATA_SOURCE", "dodo")
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.dodo_enabled() is False
    assert cfg.using_dodo_source() is False


def test_using_dodo_source_true_with_key_and_opt_in(monkeypatch):
    monkeypatch.setenv("LEDGER_SENSE_DATA_SOURCE", "dodo")
    monkeypatch.setenv("DODO_API_KEY", "dodo-test-key")
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.using_dodo_source() is True


def test_using_dodo_source_false_when_key_present_but_not_opted_in(monkeypatch):
    monkeypatch.setenv("DODO_API_KEY", "dodo-test-key")
    cfg = load_config(dotenv_path=Path("/nonexistent/.env"))
    assert cfg.using_dodo_source() is False


# ---------------------------------------------------------------------------
# .env file parsing
# ---------------------------------------------------------------------------

def test_dotenv_file_populates_config(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "OPENAI_API_KEY=sk-from-dotenv\n"
        "# a comment line\n"
        "\n"
        "LEDGER_SENSE_OPENAI_MODEL='gpt-4o'\n"
        'DODO_API_KEY="dodo-from-dotenv"\n'
    )
    cfg = load_config(dotenv_path=dotenv)
    assert cfg.openai_api_key == "sk-from-dotenv"
    assert cfg.openai_model == "gpt-4o"
    assert cfg.dodo_api_key == "dodo-from-dotenv"
    assert cfg.openai_enabled() is True
    assert cfg.dodo_enabled() is True


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=sk-from-dotenv\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-real-env")
    cfg = load_config(dotenv_path=dotenv)
    assert cfg.openai_api_key == "sk-from-real-env"


def test_missing_dotenv_file_is_not_an_error(tmp_path):
    cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
    assert cfg.openai_enabled() is False


def test_find_dotenv_walks_up_directories(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-found\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    found = find_dotenv(start=nested)
    assert found == tmp_path / ".env"


def test_find_dotenv_returns_none_when_absent(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_dotenv(start=nested) is None


# ---------------------------------------------------------------------------
# Config is a plain, immutable dataclass — no hidden os.environ access
# ---------------------------------------------------------------------------

def test_config_is_frozen_dataclass():
    cfg = Config()
    with pytest.raises(Exception):
        cfg.openai_api_key = "mutated"  # type: ignore[misc]


def test_config_direct_construction_defaults_disabled():
    cfg = Config()
    assert cfg.openai_enabled() is False
    assert cfg.dodo_enabled() is False
    assert cfg.tracing_enabled() is False
