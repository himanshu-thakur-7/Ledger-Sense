"""Agent 3's entire demo surface (locked BOARD.md Q1 -- no web UI, ever):

    ledger_sense resolve  ...                       # §7.1 structured capture
    ledger_sense promote  <rule_id> --confirm yes-always
    ledger_sense apply-rules ...                     # §7.4 pass-2 insertion

``resolve`` and ``promote`` are the two entrypoints the task brief names
explicitly. ``apply-rules`` is the "thin insert hook that Agent 1 pass-2
calls" (BOARD.md W5 card) -- a third subcommand of the same ``ledger_sense``
CLI rather than a separate script, since it is exactly the same file-spine
pattern as ``resolve``/``promote``.
"""

import argparse
import sys
from collections import Counter
from decimal import DecimalException
from pathlib import Path
from typing import Optional

from ledger_sense.config import load_config
from ledger_sense.data.io_csv import write_csv
from ledger_sense.guardrail import load_policy
from ledger_sense.tracing import traced_run

from . import io as learning_io
from . import llm_rationale
from .apply import RULE_HIT_COLUMNS, apply_rules
from .predicate import build_predicate, evaluate_predicate, render_english
from .resolution import NON_RULE_TYPES, ResolutionError, make_resolution
from .rules import RuleError, candidate_rule, load_candidates, load_rules, promote as promote_rule, save_candidates

DEFAULT_CANDIDATES_PATH = "rule_candidates.json"
DEFAULT_RULES_PATH = "rules.json"


def _support_count(predicate: dict, exceptions_path, outcomes_path) -> int:
    """§7.2/§11: "support count against the current exception pile" -- every
    currently-open ``exceptions.csv`` row (bank/pair subjects only; a
    ledger-only subject carries no matcher feature vector) whose matched
    ``match_outcomes.csv`` features satisfy the predicate."""
    exception_rows = learning_io.read_exceptions(exceptions_path)
    features_by_bank = learning_io.read_outcome_features(outcomes_path)
    count = 0
    for row in exception_rows:
        if row["subject_kind"] not in ("bank", "pair") or not row["bank_txn_id"]:
            continue
        features = features_by_bank.get(row["bank_txn_id"])
        if features is None:
            continue
        if evaluate_predicate(predicate, features):
            count += 1
    return count


def _openai_suggestion(args) -> Optional[dict]:
    """W12: OpenAI resolution-learning rationale assist. Offered only when
    the human gave none of the predicate flags themselves and the
    resolution type can ever carry a predicate at all (law L13 --
    manual_one_off/no_pattern are excluded before any config is even read,
    let alone an LLM call made). Absent ``OPENAI_API_KEY``
    (``config.openai_enabled()`` False) this function does nothing and
    returns ``None`` -- ``resolve`` then behaves byte-identical to v1
    (law L18)."""
    manual_evidence_given = any(
        value is not None
        for value in (
            args.counterparty_key, args.currency, args.amount_delta_min,
            args.amount_delta_max, args.reference_transform, args.amount_class,
        )
    )
    if manual_evidence_given or args.resolution_type in NON_RULE_TYPES:
        return None

    cfg = load_config()
    if not cfg.openai_enabled():
        return None

    client = llm_rationale.build_client(cfg)
    suggestion = llm_rationale.suggest_predicate(
        resolution_type=args.resolution_type, rationale=args.rationale, client=client,
        cache_key=args.exception_id,
    )
    if suggestion:
        print(f"SUGGESTION ({cfg.openai_model}): candidate predicate: {render_english(suggestion)}")
        print("SUGGESTION: edit with --counterparty-key/--currency/... to override; "
              "promote still requires explicit --confirm yes-always")
    return suggestion


def cmd_resolve(args) -> int:
    suggestion = _openai_suggestion(args)
    try:
        predicate = build_predicate(
            counterparty_key=args.counterparty_key,
            currency=args.currency,
            amount_delta_min=args.amount_delta_min,
            amount_delta_max=args.amount_delta_max,
            reference_transform=args.reference_transform,
            amount_class=args.amount_class,
        )
        if not predicate and suggestion:
            predicate = suggestion
        resolution = make_resolution(
            exception_id=args.exception_id,
            resolution_type=args.resolution_type,
            evidence=predicate,
            rationale=args.rationale,
            resolved_by=args.resolved_by,
            resolved_at=args.resolved_at,
        )
    except (ResolutionError, ValueError, DecimalException) as exc:
        print(f"resolve refused: {exc}", file=sys.stderr)
        return 2

    print(f"resolution_id={resolution.resolution_id}")
    print(f"exception_id={resolution.exception_id}")
    print(f"resolution_type={resolution.resolution_type}")

    if resolution.resolution_type in NON_RULE_TYPES:
        print("status=resolved (first-class outcome, law L13 -- no candidate rule, ever)")
        return 0

    support_count = _support_count(predicate, args.exceptions, args.outcomes)
    candidate = candidate_rule(resolution, support_count)
    candidates = [c for c in load_candidates(args.candidates) if c["rule_id"] != candidate["rule_id"]]
    candidates.append(candidate)
    save_candidates(args.candidates, candidates)

    print(f"rule_id={candidate['rule_id']}")
    print(f"candidate predicate: {candidate['plain_english']}")
    print(f"support count against current exception pile: {support_count}")
    print("status=candidate")
    return 0


def cmd_promote(args) -> int:
    candidates = load_candidates(args.candidates)
    candidate = next((c for c in candidates if c["rule_id"] == args.rule_id), None)
    if candidate is None:
        print(
            f"promote refused: no candidate rule {args.rule_id!r} in {args.candidates} "
            "(run 'ledger_sense resolve' first)",
            file=sys.stderr,
        )
        return 2
    try:
        record = promote_rule(
            candidate,
            promoted_by=args.promoted_by,
            promoted_at=args.promoted_at,
            confirm=args.confirm,
            rules_path=args.rules,
            candidates_path=args.candidates,
            candidates=candidates,
        )
    except RuleError as exc:
        print(f"promote refused: {exc}", file=sys.stderr)
        return 2
    print(f"{record['rule_id']} <- {record['resolution_id']}")
    return 0


def cmd_apply_rules(args) -> int:
    rule_list = load_rules(args.rules)
    outcome_rows = learning_io.read_outcomes(args.outcomes)
    settlement_rows = learning_io.read_settlements(args.settlements)
    ledger_rows = learning_io.read_ledger(args.ledger)
    bank_rows = learning_io.read_bank(args.bank)
    policy = load_policy(args.policy_book) if args.policy_book else None

    result = apply_rules(outcome_rows, settlement_rows, rule_list, ledger_rows, bank_rows, args.as_of, policy=policy,
                          period_start=args.period_start, period_end=args.period_end)

    out_dir = Path(args.out_dir)
    write_csv(str(out_dir / "match_outcomes.csv"), learning_io.OUTCOME_COLUMNS, result.outcomes)
    write_csv(str(out_dir / "ledger_settlements.csv"), learning_io.SETTLEMENT_COLUMNS, result.settlements)
    write_csv(str(out_dir / "rule_hits.csv"), RULE_HIT_COLUMNS, result.hits)

    print(f"rules loaded: {result.rules_loaded}")
    print(f"escalated lines seen: {result.escalated_seen}")
    print(f"escalated lines matching a rule's predicate: {result.considered}")
    print(f"vetoed by guardrail (would_block_or_hold != allow): {result.vetoed}")
    print(f"predicate hit but no ledger capacity remained: {result.no_capacity}")
    print(f"resolved by rule: {len(result.hits)}")
    by_rule = Counter(hit["rule_id"] for hit in result.hits)
    for rule_id, count in sorted(by_rule.items()):
        print(f"  {rule_id}: {count} lines resolved")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger_sense", description="Agent 3 -- Resolution-Learning (spec §7)")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve_p = sub.add_parser("resolve", help="Capture one structured resolution (§7.1)")
    resolve_p.add_argument("--exceptions", required=True, help="current pass's exceptions.csv")
    resolve_p.add_argument("--outcomes", required=True, help="current pass's match_outcomes.csv")
    resolve_p.add_argument("--exception-id", required=True, dest="exception_id")
    resolve_p.add_argument(
        "--resolution-type",
        required=True,
        dest="resolution_type",
        choices=(
            "fee_offset", "reference_transform", "counterparty_alias",
            "timing_tolerance", "manual_one_off", "no_pattern",
        ),
    )
    resolve_p.add_argument("--rationale", required=True, help="free text, audit/demo narration only")
    resolve_p.add_argument("--resolved-by", required=True, dest="resolved_by")
    resolve_p.add_argument("--resolved-at", required=True, dest="resolved_at", help="ISO-8601, never wall-clock")
    resolve_p.add_argument("--counterparty-key", dest="counterparty_key", default=None)
    resolve_p.add_argument("--currency", default=None)
    resolve_p.add_argument("--amount-delta-min", dest="amount_delta_min", default=None,
                            help="dollars, e.g. 0.00 -- lower bound on |amount_delta|")
    resolve_p.add_argument("--amount-delta-max", dest="amount_delta_max", default=None,
                            help="dollars, e.g. 15.00 -- upper bound on |amount_delta|")
    resolve_p.add_argument("--reference-transform", dest="reference_transform", default=None,
                            choices=("exact", "fuzzy", "wrong", "missing"))
    resolve_p.add_argument("--amount-class", dest="amount_class", default=None,
                            choices=("exact", "fx", "partial", "conflict"))
    resolve_p.add_argument("--candidates", default=DEFAULT_CANDIDATES_PATH, help="candidate-rule store path")
    resolve_p.set_defaults(func=cmd_resolve)

    promote_p = sub.add_parser("promote", help="Promote a candidate rule into rules.json (§7.3)")
    promote_p.add_argument("rule_id")
    promote_p.add_argument("--confirm", required=True, help="must be exactly 'yes-always'")
    promote_p.add_argument("--promoted-by", required=True, dest="promoted_by")
    promote_p.add_argument("--promoted-at", required=True, dest="promoted_at")
    promote_p.add_argument("--rules", default=DEFAULT_RULES_PATH)
    promote_p.add_argument("--candidates", default=DEFAULT_CANDIDATES_PATH)
    promote_p.set_defaults(func=cmd_promote)

    apply_p = sub.add_parser("apply-rules", help="Pass-2 insertion: rules.json before routing sees the residual (§7.4)")
    apply_p.add_argument("--outcomes", required=True, help="pass-2 match_outcomes.csv (Agent 1's own CLI output)")
    apply_p.add_argument("--settlements", required=True, help="pass-2 ledger_settlements.csv")
    apply_p.add_argument("--ledger", required=True)
    apply_p.add_argument("--bank", required=True)
    apply_p.add_argument("--rules", default=DEFAULT_RULES_PATH)
    apply_p.add_argument("--as-of", required=True, dest="as_of")
    apply_p.add_argument("--period-start", dest="period_start", default=None,
                          help="out_of_period window start (with --period-end); default: calendar month of --as-of")
    apply_p.add_argument("--period-end", dest="period_end", default=None)
    apply_p.add_argument("--policy-book", dest="policy_book", default=None)
    apply_p.add_argument("--out-dir", required=True, dest="out_dir")
    apply_p.set_defaults(func=cmd_apply_rules)

    return parser


@traced_run("learning")
def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
