"""Run Agent 1: python -m ledger_sense.matching --help."""

import argparse

from ..tracing import traced_run
from .adjudication import NoneAdjudicator, StubAdjudicator
from .io import run
from .llm_adjudicator import get_adjudicator


@traced_run("matching")
def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent 1 deterministic cheap matcher + zero-cost stub")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--out-dir", required=True)
    # "stub"/"none" stay explicit, deterministic force-overrides (unchanged
    # default -- CI and every existing test still get plain StubAdjudicator()
    # with zero config/env involvement). "auto" is the new v2 config-driven
    # path (W9): get_adjudicator() returns the real OpenAIAdjudicator only
    # when config.openai_enabled() is True, else the same StubAdjudicator.
    parser.add_argument("--adjudicator", choices=("stub", "none", "auto"), default="stub")
    args = parser.parse_args(argv)
    if args.adjudicator == "auto":
        adjudicator = get_adjudicator()
    else:
        adjudicator = StubAdjudicator() if args.adjudicator == "stub" else NoneAdjudicator()
    result = run(args.ledger, args.bank, args.out_dir, adjudicator)
    matched = sum(row["status"] == "matched" for row in result.outcomes)
    print(f"bank lines={len(result.outcomes)}; ledger entries={len(result.settlements)}; matched={matched}")
    print(f"cheap-tier match rate: {result.cheap_match_rate:.2f}% ({result.cheap_matches}/{len(result.outcomes)})")
    print(f"llm_is_stub={result.llm_is_stub}; llm_calls={result.llm_calls}; adjudicator={adjudicator.model}")


if __name__ == "__main__":
    main()
