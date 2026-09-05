"""Enables ``python -m ledger_sense.data ...`` (see cli.py for the reference command)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
