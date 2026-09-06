"""A tiny, routing-owned normalizer.

Deliberately not imported from ``ledger_sense.matching`` (which has its own
near-identical ``squash()``): the two packages must stay import-isolated from
each other, so a one-line string utility is duplicated rather than shared.
"""


def squash(text: str) -> str:
    return "".join(c for c in text.upper() if c.isalnum())
