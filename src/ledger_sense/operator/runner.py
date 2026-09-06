"""Thin subprocess wrapper for shelling out to another agent's own
published CLI (spec: BOARD.md TAPE-1, "Must not: ... import matching/routing
internals directly (subprocess or published CLIs only)"). The close desk
only ever talks to another agent through a file or one of these CLI calls --
never a Python import of matching/routing/guardrail/learning logic.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


@dataclass
class RunResult:
    argv: list
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_module(module: str, args: list, *, env: dict | None = None) -> RunResult:
    """Runs ``python -m <module> <args>`` as a real subprocess and captures
    both streams as text. Never raises -- a transport-level failure to even
    launch the interpreter (should never happen in practice) is the one
    thing this deliberately lets propagate, since it means the environment
    itself is broken, not a normal "this pull failed" case any caller here
    already handles via ``returncode``."""
    argv = [sys.executable, "-m", module, *args]
    completed = subprocess.run(argv, capture_output=True, text=True, env=env)
    return RunResult(argv=argv, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
