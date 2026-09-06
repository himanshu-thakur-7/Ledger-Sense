"""Enables ``python -m ledger_sense.operator <pull|analyze|resolve|promote|next-close|status|logs|chat> ...``
(see ``cli.py`` for the reference commands)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
