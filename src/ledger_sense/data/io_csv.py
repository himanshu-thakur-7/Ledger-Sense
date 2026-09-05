"""CSV writing helpers.

Rows arrive as dicts already produced by a model's ``to_row()`` -- every amount field
has already gone through :func:`ledger_sense.data.money.money_str`, so nothing here
ever formats a number. Fixed newline handling and column order are what make two runs
byte-identical (law L4).
"""

import csv
import os
from typing import Iterable, Sequence


def write_csv(path: str, columns: Sequence[str], rows: Iterable[dict]) -> int:
    """Write ``rows`` (dicts with exactly ``columns`` keys) to ``path``. Returns count."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    count = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count
