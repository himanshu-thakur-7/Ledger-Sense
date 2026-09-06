"""Name normalization shared by the ``denied_party`` and ``duplicate_release`` rules.

Kept deliberately separate from token-matching logic used elsewhere in the repo
(guardrail must not import ``ledger_sense.matching``) -- this is a small, self
-contained reimplementation.
"""

import re

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalize_tokens(raw: str) -> list:
    """Uppercase, strip punctuation, and split ``raw`` into whitespace tokens.

    Used for both denied-party names and duplicate-release fingerprints so a
    company name like ``"Prairie P. Corp."`` and ``"PRAIRIE P CORP"`` normalize
    to the same token list.
    """
    if not raw:
        return []
    squashed = _NON_ALNUM.sub(" ", raw.upper())
    return [tok for tok in squashed.split(" ") if tok]


def normalize_key(raw: str) -> str:
    """Collapse ``raw`` to a single normalized string (tokens joined with no separator)."""
    return "".join(normalize_tokens(raw))


def contains_token_sequence(haystack_tokens: list, needle_tokens: list) -> bool:
    """True if ``needle_tokens`` appears as a contiguous, whole-token run inside ``haystack_tokens``.

    This is the precision guard from spec §8.1: a list entry token must match a
    *whole* token in the name, never a substring of a longer token. So a list
    entry ``ORBEX`` (one token) never fires on the name ``ORBEXIA CORP``
    (tokens ``["ORBEXIA", "CORP"]``) -- ``"ORBEX" != "ORBEXIA"``.
    """
    if not needle_tokens:
        return False
    n = len(needle_tokens)
    for start in range(len(haystack_tokens) - n + 1):
        if haystack_tokens[start:start + n] == needle_tokens:
            return True
    return False
