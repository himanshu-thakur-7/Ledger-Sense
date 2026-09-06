"""The close desk's two entry modes (spec: BOARD.md TAPE-1 part C):

  * ``python -m ledger_sense.operator <subcommand> [flags]`` -- explicit,
    non-NLP dispatch straight to one action (``main``, below).
  * ``ledger-sense-desk "<free-text order>"`` / ``ledger-sense-desk --chat``
    -- the same intents, parsed from natural language (``main_desk``).

Both ultimately call the exact same :mod:`actions` functions through
:class:`~ledger_sense.operator.desk.Desk`, so a human gets identical
behavior from either door.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from ..data.dodo_source import DEFAULT_CACHE_PATH
from ..tracing import traced_run
from . import actions, trace
from .desk import Desk
from .paths import DEFAULT_PASS1_DIR, DEFAULT_PASS2_DIR


def _print_result(result: actions.ActionResult, out=sys.stdout) -> int:
    for line in result.lines:
        print(line, file=out)
    return 0 if result.ok else 1


def _common_dir_args(parser: argparse.ArgumentParser, *, pass2: bool = False) -> None:
    parser.add_argument("--dir", default=DEFAULT_PASS1_DIR, help=f"pass-1 working directory (default {DEFAULT_PASS1_DIR!r})")
    if pass2:
        parser.add_argument("--pass2-dir", dest="pass2_dir", default=DEFAULT_PASS2_DIR,
                             help=f"pass-2 working directory (default {DEFAULT_PASS2_DIR!r})")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ledger_sense.operator",
        description="Ledger Sense close desk -- a CFO-office terminal shell over the existing agents (TAPE-1).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pull_p = sub.add_parser("pull", help="Bring in bank-side data: dodo live, else dodo-cache, else synthetic")
    _common_dir_args(pull_p)
    pull_p.add_argument("--source", choices=("dodo", "dodo-cache", "synthetic"), default=None,
                         help="force one source; omit for the desk's own live->cache->synthetic fallback")
    pull_p.add_argument("--seed", type=int, default=actions.DEFAULT_SEED)
    pull_p.add_argument("--n-cases", type=int, dest="n_cases", default=actions.DEFAULT_N_CASES)
    pull_p.add_argument("--cache-path", dest="cache_path", default=DEFAULT_CACHE_PATH)
    pull_p.set_defaults(run=lambda a: actions.pull(
        _pass1(a), source=a.source, seed=a.seed, n_cases=a.n_cases, cache_path=a.cache_path))

    analyze_p = sub.add_parser("analyze", help="Match + route (+ guardrail) -> discrepancies")
    _common_dir_args(analyze_p)
    analyze_p.add_argument("--as-of", dest="as_of", default=None,
                            help="ISO-8601 instant; omit to infer from the pulled data's own date range")
    analyze_p.set_defaults(run=lambda a: actions.analyze(_pass1(a), as_of=a.as_of))

    resolve_p = sub.add_parser("resolve", help="Capture one structured resolution (delegates to `ledger_sense resolve`)")
    _common_dir_args(resolve_p)
    resolve_p.add_argument("--exception-id", dest="exception_id", required=True,
                            help="a real exception_id, or the literal 'that one' (last analyze's example)")
    resolve_p.add_argument("--resolution-type", dest="resolution_type", required=True,
                            choices=("fee_offset", "reference_transform", "counterparty_alias",
                                     "timing_tolerance", "manual_one_off", "no_pattern"))
    resolve_p.add_argument("--rationale", default="")
    resolve_p.add_argument("--counterparty-key", dest="counterparty_key", default=None)
    resolve_p.add_argument("--currency", default=None)
    resolve_p.add_argument("--amount-delta-min", dest="amount_delta_min", default=None)
    resolve_p.add_argument("--amount-delta-max", dest="amount_delta_max", default=None)
    resolve_p.add_argument("--reference-transform", dest="reference_transform", default=None,
                            choices=("exact", "fuzzy", "wrong", "missing"))
    resolve_p.add_argument("--amount-class", dest="amount_class", default=None,
                            choices=("exact", "fx", "partial", "conflict"))
    resolve_p.add_argument("--resolved-by", dest="resolved_by", default="desk-operator")
    resolve_p.add_argument("--resolved-at", dest="resolved_at", default=None)
    resolve_p.add_argument("--as-of", dest="as_of", default=actions.DEFAULT_AS_OF)
    resolve_p.set_defaults(run=lambda a: actions.resolve(
        _pass1(a), exception_ref=a.exception_id, resolution_type=a.resolution_type,
        predicate_flags={
            "--counterparty-key": a.counterparty_key, "--currency": a.currency,
            "--amount-delta-min": a.amount_delta_min, "--amount-delta-max": a.amount_delta_max,
            "--reference-transform": a.reference_transform, "--amount-class": a.amount_class,
        },
        rationale=a.rationale, resolved_by=a.resolved_by, resolved_at=a.resolved_at, as_of=a.as_of,
    ))

    promote_p = sub.add_parser("promote", help="Promote a candidate rule into rules.json (delegates to `ledger_sense promote`)")
    _common_dir_args(promote_p)
    promote_p.add_argument("rule_id")
    promote_p.add_argument("--confirm", required=True, help="must be exactly 'yes-always'")
    promote_p.add_argument("--promoted-by", dest="promoted_by", default="desk-operator")
    promote_p.add_argument("--promoted-at", dest="promoted_at", default=None)
    promote_p.add_argument("--as-of", dest="as_of", default=actions.DEFAULT_AS_OF)
    promote_p.set_defaults(run=lambda a: actions.promote(
        _pass1(a), rule_id=a.rule_id, confirm=a.confirm,
        promoted_by=a.promoted_by, promoted_at=a.promoted_at, as_of=a.as_of,
    ))

    next_close_p = sub.add_parser("next-close", help="Generate/run pass 2, rules off vs on -- did it learn?")
    _common_dir_args(next_close_p, pass2=True)
    next_close_p.add_argument("--seed", type=int, default=actions.DEMO_SEED)
    next_close_p.add_argument("--n-cases", type=int, dest="n_cases", default=actions.DEMO_N_CASES)
    next_close_p.add_argument("--as-of", dest="as_of", default=None,
                               help="ISO-8601 instant; omit to infer from pass 2's own date range")
    next_close_p.set_defaults(run=lambda a: actions.next_close(
        _pass1(a), _pass2(a), seed=a.seed, n_cases=a.n_cases, as_of=a.as_of))

    status_p = sub.add_parser("status", help="Where are we -- dirs, rules.json, exception counts")
    _common_dir_args(status_p, pass2=True)
    status_p.set_defaults(run=lambda a: actions.status(_pass1(a), _pass2(a)))

    logs_p = sub.add_parser("logs", help="Summarize demo_trace.json for this pass directory")
    _common_dir_args(logs_p)
    logs_p.set_defaults(run=lambda a: actions.logs(_pass1(a)))

    chat_p = sub.add_parser("chat", help="Interactive desk> loop (the camera -- see BOARD.md TAPE-1)")
    _common_dir_args(chat_p, pass2=True)
    chat_p.set_defaults(run=None)  # handled specially in main()

    return parser


def _pass1(args):
    from .paths import PassPaths

    return PassPaths(args.dir)


def _pass2(args):
    from .paths import PassPaths

    return PassPaths(args.pass2_dir)


@traced_run("operator")
def main(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "chat":
        return Desk(args.dir, args.pass2_dir).chat()

    # Every explicit subcommand is a "turn" too (spec: "write demo_trace.json
    # every turn") -- not only chat/one-shot free-text ones, which go
    # through Desk.run_intent instead and already do this themselves.
    start = time.monotonic()
    result = args.run(args)
    duration = time.monotonic() - start
    trace.append_entry(
        _pass1(args).trace_path, command=args.command, files=result.data.get("files", []),
        duration_seconds=duration, example_exception_id=result.data.get("example_exception_id"),
        ok=result.ok,
    )
    return _print_result(result)


def build_desk_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger-sense-desk",
        description="Ledger Sense close desk -- one-shot free-text order, or --chat for the interactive desk>.",
    )
    parser.add_argument("order", nargs="*", help="a free-text order, e.g. 'pull the bank and show discrepancies'")
    parser.add_argument("--chat", action="store_true", help="interactive desk> loop instead of a one-shot order")
    parser.add_argument("--dir", default=DEFAULT_PASS1_DIR)
    parser.add_argument("--pass2-dir", dest="pass2_dir", default=DEFAULT_PASS2_DIR)
    return parser


@traced_run("operator")
def main_desk(argv: Optional[list] = None) -> int:
    parser = build_desk_arg_parser()
    args = parser.parse_args(argv)
    desk = Desk(args.dir, args.pass2_dir)
    if args.chat:
        return desk.chat()
    text = " ".join(args.order).strip()
    if not text:
        parser.error("give a free-text order, or pass --chat for the interactive desk>")
    desk.dispatch_line(text, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
