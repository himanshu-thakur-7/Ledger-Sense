"""Filesystem conventions for one close-desk working pass directory (spec:
BOARD.md TAPE-1 part C). Every path here is derived purely from the
human-given ``--dir`` -- nothing is ever guessed beyond the two documented
defaults (``data/demo/pass1``/``pass2``).

``rules.json``/``rule_candidates.json`` live at the *pass-1* directory's top
level (the durable, human-inspectable record of what the desk has learned);
every other agent's own intermediate output (``match_outcomes.csv``,
``exceptions.csv``, ...) lives under a ``.desk/`` working subdirectory so a
human's real bank/ledger CSVs are never mixed up with derived files.
``demo_trace.json`` lives at the pass directory's top level too -- it is
meant to be looked at, not hidden (see the ``logs``/``trace`` intent).
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_PASS1_DIR = "data/demo/pass1"
DEFAULT_PASS2_DIR = "data/demo/pass2"

WORK_SUBDIR = ".desk"


class PassPaths:
    """Every path the desk ever reads or writes for one pass directory."""

    def __init__(self, dir_path) -> None:
        self.dir = Path(dir_path)
        self.work_dir = self.dir / WORK_SUBDIR

        # Bank-side input (written by `pull`, read by everything else).
        self.ledger_csv = self.dir / "ledger.csv"
        self.bank_csv = self.dir / "bank.csv"
        self.match_links_csv = self.dir / "match_links.csv"

        # Durable, human-visible learning state -- written only by the real
        # `ledger_sense resolve`/`promote` CLIs, never by this module.
        self.rules_json = self.dir / "rules.json"
        self.candidates_json = self.dir / "rule_candidates.json"

        # Derived/working output from matching/guardrail/routing -- always
        # under .desk/, never mixed in with the human-facing files above.
        self.matching_out = self.work_dir / "matching_out"
        self.guardrail_out = self.work_dir / "guardrail_out"
        self.routing_out = self.work_dir / "routing_out"
        self.routing_off_out = self.work_dir / "routing_off_out"
        self.routing_on_out = self.work_dir / "routing_on_out"
        self.applied_out = self.work_dir / "applied_out"

        # Visible, top-level -- see tracing.py's docstring on why this is
        # never hidden.
        self.trace_path = self.dir / "demo_trace.json"

    def has_bank_data(self) -> bool:
        return self.ledger_csv.exists() and self.bank_csv.exists()

    def outcomes_csv(self) -> Path:
        return self.matching_out / "match_outcomes.csv"

    def settlements_csv(self) -> Path:
        return self.matching_out / "ledger_settlements.csv"

    def exceptions_csv(self) -> Path:
        return self.routing_out / "exceptions.csv"
