"""Run Agent 2: python -m ledger_sense.routing --help."""

import argparse
from collections import Counter

from .io import run


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent 2 ownership / routing (spec §6)")
    parser.add_argument("--outcomes", required=True, help="Agent 1's match_outcomes.csv")
    parser.add_argument("--settlements", required=True, help="Agent 1's ledger_settlements.csv")
    parser.add_argument("--ledger", required=True, help="§4 ledger.csv")
    parser.add_argument("--bank", required=True, help="§4 bank.csv")
    parser.add_argument("--as-of", required=True, dest="as_of",
                         help="ISO-8601 instant this run treats as 'now' (never wall-clock time)")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)

    exception_rows, queue_rows = run(args.outcomes, args.settlements, args.ledger, args.bank,
                                      args.as_of, args.out_dir)
    categories = Counter(row["category"] for row in exception_rows)
    kinds = Counter(row["subject_kind"] for row in exception_rows)
    breached = sum(row["sla_state"] == "breached" for row in exception_rows)
    print(f"exceptions={len(exception_rows)}; owners={len(queue_rows)}; breached={breached}")
    print(f"by category: {dict(categories)}")
    print(f"by subject_kind: {dict(kinds)}")


if __name__ == "__main__":
    main()
