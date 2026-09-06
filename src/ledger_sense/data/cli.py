"""``generate`` CLI: writes ledger.csv, bank.csv, match_links.csv for a batch.

Reference command (spec §4, BOARD.md W1 card)::

    python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 25000 --out-dir data/pass1
    python -m ledger_sense.data --seed 42 --pass-number 2 --n-cases 25000 --out-dir data/pass2

Add ``--overlay`` to enable the disclosed demo-overlay mode (BOARD.md locked Q3):
plants 12-20 labeled ``fee_offset`` siblings only if no naturally-occurring exception
class already clears the 8-sibling threshold.

Add ``--source dodo`` (W11, LEDGER-SENSE-v2-PRD.md) to pull real Dodo Payments
*sandbox* transactions instead of pure synthesis -- see ``dodo_source.py`` for
the pull-then-synthesize pipeline this delegates to. Requires ``DODO_API_KEY``
(``LEDGER_SENSE_DATA_SOURCE=dodo`` alone is not enough, mirroring
``config.py``'s ``using_dodo_source()`` gate); when the key is absent this
exits cleanly with a nonzero code and a one-line message (never a stack
trace, law L18) and never touches the default synthetic path.
"""

import argparse
import os
import sys
from typing import Optional

from ..config import Config, load_config
from .dodo_source import (
    DodoClient,
    DodoNotConfiguredError,
    DodoSandboxClient,
    build_dodo_dataset,
    ensure_dodo_configured,
)
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
    parser.add_argument(
        "--source",
        choices=["synthetic", "dodo"],
        default=None,
        help="bank-side data source: 'synthetic' (default, v1 generator) or "
        "'dodo' (pull real Dodo Payments sandbox transactions, W11). Omit to "
        "use LEDGER_SENSE_DATA_SOURCE (default 'synthetic').",
    )
    return parser


def write_dataset(dataset: GeneratedDataset, out_dir: str) -> None:
    write_csv(os.path.join(out_dir, "ledger.csv"), LEDGER_COLUMNS, dataset.ledger_rows)
    write_csv(os.path.join(out_dir, "bank.csv"), BANK_COLUMNS, dataset.bank_rows)
    write_csv(
        os.path.join(out_dir, "match_links.csv"), MATCH_LINK_COLUMNS, dataset.match_link_rows
    )


def main(argv=None, *, config: Optional[Config] = None, client: Optional[DodoClient] = None) -> int:
    """``config``/``client`` are injectable (default: real env / real transport)
    so tests never touch the real environment or network (law L20) -- see
    ``tests/test_dodo_source.py``."""
    args = build_arg_parser().parse_args(argv)
    cfg = config if config is not None else load_config()
    source = args.source or cfg.data_source

    if source == "dodo":
        try:
            ensure_dodo_configured(cfg)
        except DodoNotConfiguredError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        dodo_client = client if client is not None else DodoSandboxClient(api_key=cfg.dodo_api_key)
        dodo_dataset = build_dodo_dataset(dodo_client, seed=args.seed)
        if args.out_dir:
            write_dataset(dodo_dataset, args.out_dir)
        print(dodo_dataset.format())
        return 0

    generator_config = GeneratorConfig(
        seed=args.seed,
        pass_number=args.pass_number,
        n_cases=args.n_cases,
        overlay=args.overlay,
        universe_size=args.universe_size,
    )
    dataset = generate(generator_config)
    if args.out_dir:
        write_dataset(dataset, args.out_dir)
    print(dataset.summary.format())
    return 0


if __name__ == "__main__":
    sys.exit(main())
