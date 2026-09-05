"""``generate`` CLI: writes ledger.csv, bank.csv, match_links.csv for a batch.

Reference command (spec §4, BOARD.md W1 card)::

    python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 25000 --out-dir data/pass1
    python -m ledger_sense.data --seed 42 --pass-number 2 --n-cases 25000 --out-dir data/pass2

Add ``--overlay`` to enable the disclosed demo-overlay mode (BOARD.md locked Q3):
plants 12-20 labeled ``fee_offset`` siblings only if no naturally-occurring exception
class already clears the 8-sibling threshold.
"""

import argparse
import os
import sys

from .generator import GeneratedDataset, GeneratorConfig, generate
from .io_csv import write_csv
from .models import BANK_COLUMNS, LEDGER_COLUMNS, MATCH_LINK_COLUMNS


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ledger_sense.data",
        description="Generate a deterministic Ledger Sense synthetic batch (spec §4).",
    )
    parser.add_argument("--seed", type=int, required=True, help="counterparty + case seed")
    parser.add_argument(
        "--pass-number", type=int, required=True, dest="pass_number", help="1 or 2"
    )
    parser.add_argument("--n-cases", type=int, required=True, dest="n_cases")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="directory to write ledger.csv/bank.csv/match_links.csv into "
        "(omit to only print the summary, e.g. for a dry run)",
    )
    parser.add_argument(
        "--overlay",
        action="store_true",
        help="enable the disclosed fee_offset demo-overlay mode (BOARD.md locked Q3)",
    )
    parser.add_argument("--universe-size", type=int, default=800, dest="universe_size")
    return parser


def write_dataset(dataset: GeneratedDataset, out_dir: str) -> None:
    write_csv(os.path.join(out_dir, "ledger.csv"), LEDGER_COLUMNS, dataset.ledger_rows)
    write_csv(os.path.join(out_dir, "bank.csv"), BANK_COLUMNS, dataset.bank_rows)
    write_csv(
        os.path.join(out_dir, "match_links.csv"), MATCH_LINK_COLUMNS, dataset.match_link_rows
    )


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = GeneratorConfig(
        seed=args.seed,
        pass_number=args.pass_number,
        n_cases=args.n_cases,
        overlay=args.overlay,
        universe_size=args.universe_size,
    )
    dataset = generate(config)
    if args.out_dir:
        write_dataset(dataset, args.out_dir)
    print(dataset.summary.format())
    return 0


if __name__ == "__main__":
    sys.exit(main())
