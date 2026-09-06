"""v2 config/secrets foundation (spec: LEDGER-SENSE-v2-PRD.md, W8).

Single seam onto environment/`.env` for every v2 external integration. No other
module may read ``os.environ`` directly for these values — everything reads
this module's ``Config`` (or the module-level ``config`` instance) and its
``*_enabled()`` gates instead.

Graceful degradation (L18): an absent API key means the corresponding
``*_enabled()`` gate returns ``False`` and every downstream v2 module must
skip the real call and fall back to its v1 stub/synthetic/no-tracing
behavior. Nothing here ever raises for a missing key -- a missing key is the
expected, supported "v1 mode" state, not an error.

No secrets are logged (L19): this module never prints or logs the values it
reads; callers needing to log request/response data around a secret should
use ``llm_client.redact`` first.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a `.env`-style file into a dict, without external dependencies.

    Supports `KEY=value` lines, blank lines, `#`-comments, and optionally
    single/double-quoted values. Existing process environment variables
    always take precedence over values found here (see `_read_env`).
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _read_env(name: str, default: str | None = None, *, dotenv: dict[str, str] | None = None) -> str | None:
    """Read `name` from the real environment first, then a parsed `.env`, then `default`."""
    if name in os.environ:
        return os.environ[name]
    if dotenv is not None and name in dotenv:
        return dotenv[name]
    return default


@dataclass(frozen=True)
class Config:
    """Immutable snapshot of v2 config, read once at construction time.

    Construct via `load_config()` (or the module-level `config`), not
    directly, so `.env` discovery stays in one place.
    """

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    llm_cost_cap_usd: float = 1.00

    dodo_api_key: str | None = None
    dodo_environment: str = "sandbox"

    data_source: str = "synthetic"

    neatlogs_api_key: str | None = None

    def openai_enabled(self) -> bool:
        """True only when an OpenAI key is configured (L18: absent key -> stub mode)."""
        return bool(self.openai_api_key)

    def dodo_enabled(self) -> bool:
        """True only when a Dodo key is configured (L18: absent key -> synthetic data)."""
        return bool(self.dodo_api_key)

    def tracing_enabled(self) -> bool:
        """True only when a Neatlogs key is configured (L18: absent key -> no tracing)."""
        return bool(self.neatlogs_api_key)

    def using_dodo_source(self) -> bool:
        """True only when both explicitly opted into `dodo` *and* a key is present.

        A `LEDGER_SENSE_DATA_SOURCE=dodo` with no `DODO_API_KEY` still degrades
        to synthetic (L18) rather than crash or silently attempt a keyless call.
        """
        return self.data_source == "dodo" and self.dodo_enabled()


def find_dotenv(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default: cwd) looking for a `.env` file."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_config(dotenv_path: Path | str | None = None) -> Config:
    """Build a `Config` from the real environment plus an optional `.env` file.

    Real environment variables always win over `.env` file values, matching
    the usual "shell overrides file" convention. Passing `dotenv_path=False`-ish
    (any falsy non-None won't happen here; pass an explicit missing path) or a
    path to a nonexistent file simply yields no `.env` contribution.
    """
    if dotenv_path is None:
        resolved = find_dotenv()
    else:
        resolved = Path(dotenv_path)
    dotenv = _load_dotenv(resolved) if resolved is not None else {}

    def env(name: str, default: str | None = None) -> str | None:
        return _read_env(name, default, dotenv=dotenv)

    cost_cap_raw = env("LEDGER_SENSE_LLM_COST_CAP_USD", "1.00")
    try:
        cost_cap = float(cost_cap_raw)
    except (TypeError, ValueError):
        cost_cap = 1.00

    return Config(
        # Each key also accepts a LEDGER_SENSE_-prefixed alias, checked only
        # when the bare name is unset -- useful on a machine where the bare
        # name (OPENAI_API_KEY, etc.) is already claimed by another tool's
        # own environment (TAPE-1: close-desk env aliases). The bare name
        # always wins when both are set, matching every third-party SDK's
        # own convention for that variable.
        openai_api_key=env("OPENAI_API_KEY") or env("LEDGER_SENSE_OPENAI_API_KEY") or None,
        openai_model=env("LEDGER_SENSE_OPENAI_MODEL", "gpt-4o-mini"),
        llm_cost_cap_usd=cost_cap,
        dodo_api_key=env("DODO_API_KEY") or env("LEDGER_SENSE_DODO_API_KEY") or None,
        dodo_environment=env("DODO_ENVIRONMENT", "sandbox"),
        data_source=env("LEDGER_SENSE_DATA_SOURCE", "synthetic"),
        neatlogs_api_key=env("NEATLOGS_API_KEY") or env("LEDGER_SENSE_NEATLOGS_API_KEY") or None,
    )


# Module-level singleton for convenient `from ledger_sense.config import config`
# style imports. Callers that need a fresh read (e.g. tests mutating
# `os.environ`) should call `load_config()` directly instead.
config = load_config()
