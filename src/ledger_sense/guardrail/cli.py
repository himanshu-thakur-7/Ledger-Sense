"""Run Agent 4: python -m ledger_sense.guardrail --help."""

import argparse
from decimal import Decimal

from .engine import run
from .period import parse_instant


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent 4 deterministic release-decision guardrail (spec §8)")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--outcomes", required=True, help="Agent 1's match_outcomes.csv")
    parser.add_argument("--settlements", required=True, help="Agent 1's ledger_settlements.csv")
    parser.add_argument("--as-of", required=True, help="ISO-8601 clock; never wall-clock (law L7)")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--policy-book", default=None, help="override policy_book.json wholesale")
    parser.add_argument("--period-start", default=None, help="out_of_period window start (ISO-8601); needs --period-end")
    parser.add_argument("--period-end", default=None, help="out_of_period window end (ISO-8601); needs --period-start")
    args = parser.parse_args(argv)

    as_of = parse_instant(args.as_of)
    result = run(
        args.ledger, args.bank, args.outcomes, args.settlements, as_of, args.out_dir,
        policy_path=args.policy_book, period_start=args.period_start, period_end=args.period_end,
    )

    total = len(result.decisions)
    print(f"bank lines={total}; policy_version={result.policy_applied['policy_version']}")
    for verdict in ("allow", "block", "hold"):
        count = result.verdict_counts[verdict]
        pct = (Decimal(count) / Decimal(total) * Decimal(100)) if total else Decimal(0)
        print(f"{verdict}: {count}/{total} ({pct:.2f}%)")


if __name__ == "__main__":
    main()
