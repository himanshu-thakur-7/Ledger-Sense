"""Agent 5's demo surface (spec §9 / BOARD.md W6, locked Q1 -- terminal only,
no web UI, ever)::

    ledger_sense-scoreboard scoreboard --pass1-dir data/pass1 --pass2-dir data/pass2 [--rules rules.json]

Console-script naming note: the task brief and BOARD.md both write this as
``ledger_sense scoreboard``, mirroring Agent 3's own ``ledger_sense
resolve``/``promote``/``apply-rules`` (a single script name with argparse
subcommands). That script name is already registered to
``ledger_sense.learning.cli:main`` (W5) -- a second ``[project.scripts]``
entry under the same key is a TOML duplicate-key error, and making the two
share one dispatcher would mean this package importing
``ledger_sense.learning``'s argparse wiring, which law L1 (no agent imports
another agent's internals) forbids. So this agent gets its own script name,
``ledger_sense-scoreboard``, the same separate-binary pattern §4's own
``ledger-sense-generate`` already uses instead of hanging off ``ledger_sense``
-- with a ``scoreboard`` subcommand kept (rather than flattening the args
onto the top level) so the invocation the brief describes still reads,
word for word, as ``... scoreboard --pass1-dir ...``.

Reads only files already on disk (spec: "computed only from files already on
disk") -- never runs Agents 1-4 itself. Refuses (nonzero exit, no
``scoreboard.json`` written) rather than print a fabricated number when a
required input is missing or internally inconsistent.
"""

import argparse
import json
import sys
from pathlib import Path

from ..tracing import traced_run
from . import io as metrics_io
from .report import render_report
from .scoreboard import ScoreboardError, build_scoreboard

REQUIRED_PASS_FILES = (
    "match_outcomes.csv", "ledger_settlements.csv", "exceptions.csv",
    "owner_queues.csv", "release_decisions.csv", "guardrail_audit.csv", "match_links.csv",
)


def _load_pass(pass_dir: str) -> dict:
    d = Path(pass_dir)
    if not d.is_dir():
        raise metrics_io.MetricsInputError(f"pass directory not found: {pass_dir}")
    for filename in REQUIRED_PASS_FILES:
        if not (d / filename).exists():
            raise metrics_io.MetricsInputError(f"required file not found: {d / filename}")
    outcomes_path = d / "match_outcomes.csv"
    return {
        "outcomes": metrics_io.read_outcomes(outcomes_path),
        "features": metrics_io.read_outcome_features(outcomes_path),
        "settlements": metrics_io.read_settlements(d / "ledger_settlements.csv"),
        "exceptions": metrics_io.read_exceptions(d / "exceptions.csv"),
        "queues": metrics_io.read_queues(d / "owner_queues.csv"),
        "release_decisions": metrics_io.read_release_decisions(d / "release_decisions.csv"),
        "guardrail_audit": metrics_io.read_guardrail_audit(d / "guardrail_audit.csv"),
        "match_links": metrics_io.read_match_links(d / "match_links.csv"),
    }


def cmd_scoreboard(args) -> int:
    try:
        pass1 = _load_pass(args.pass1_dir)
        pass2 = _load_pass(args.pass2_dir)
        rules = metrics_io.read_rules(args.rules)
        rule_hits_path = Path(args.pass2_dir) / "rule_hits.csv"
        if not rule_hits_path.exists():
            raise metrics_io.MetricsInputError(
                f"required file not found: {rule_hits_path} (pass 2 must run learning's "
                "apply-rules CLI before routing -- spec §7.4)"
            )
        rule_hits = metrics_io.read_rule_hits(rule_hits_path)
        scoreboard = build_scoreboard(
            pass1=pass1, pass2=pass2, rules=rules, rule_hits=rule_hits,
            pass1_dir=args.pass1_dir, pass2_dir=args.pass2_dir, rules_path=args.rules,
        )
    except (metrics_io.MetricsInputError, ScoreboardError) as exc:
        print(f"scoreboard refused: {exc}", file=sys.stderr)
        return 2

    report = render_report(scoreboard)
    print(report, end="")

    out_path = Path(args.out)
    out_path.write_text(json.dumps(scoreboard, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger_sense-scoreboard", description="Agent 5 -- Metrics Orchestrator (spec §9)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sb = sub.add_parser("scoreboard", help="Pass-1-vs-pass-2 side-by-side comparison (§9.1)")
    sb.add_argument("--pass1-dir", required=True, dest="pass1_dir")
    sb.add_argument("--pass2-dir", required=True, dest="pass2_dir")
    sb.add_argument("--rules", default="rules.json", help="rules.json to trace pass-2 auto-resolves against")
    sb.add_argument("--out", default="scoreboard.json", help="where to write the scoreboard JSON")
    sb.set_defaults(func=cmd_scoreboard)

    return parser


@traced_run("metrics")
def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
