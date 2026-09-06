"""Run Agent 3: python -m ledger_sense.learning <resolve|promote|apply-rules> --help."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
