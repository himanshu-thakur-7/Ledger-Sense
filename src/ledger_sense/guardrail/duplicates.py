"""Independent duplicate-release detection (spec §8.1 ``duplicate_release``).

Deliberately re-derives duplicate bank fingerprints from ``bank.csv`` alone.
Agent 1's own ``relation == "duplicate"`` flag in ``match_outcomes.csv`` is
*never* the trigger here -- it is surfaced only as corroborating context on
the audit row, per the card's "genuine corroboration, on purpose" note. A
line can fire ``duplicate_release`` even if Agent 1 never flagged it, and a
line Agent 1 flagged does not fire this rule unless our own fingerprint also
finds a sibling.

Fingerprint: ``(amount_cents, currency, normalized reference)``. Two bank
lines that repost the same amount, currency, and reference are the same
economic event posted twice -- recurring same-amount charges to the same
counterparty use a *different* reference each time, so they never collide
here. Lines with a blank reference are never fingerprinted (no reliable
independent signal without one; ``missing_reference`` lines are left to
Agent 1/2).

Detecting *that* a group is a duplicate never touches Agent 1's per-line
verdict -- only the fingerprint decides that. But once a group is flagged,
Agent 1's own ``matched`` status *is* consulted, as corroboration, to pick
*which* member is safe to keep: if our own capacity-blind tie-break disagreed
and blocked the very line Agent 1 already cleanly settled against a real
ledger obligation, that would be worse than useless -- it would hold up a
clean payment while waving through the actual repost. With no clean match to
protect either way, the fallback tie-break is deterministic (earliest
``value_date``, ties by ``bank_txn_id``).
"""

from collections import defaultdict

from .normalize import normalize_key


def _fingerprint(bank_row: dict):
    reference = normalize_key(bank_row["reference_raw"])
    if not reference:
        return None
    return (bank_row["_amount_cents"], bank_row["currency"].strip().upper(), reference)


def find_duplicate_releases(bank_rows: list, matched_bank_txn_ids: frozenset = frozenset()) -> dict:
    """Return ``{bank_txn_id: sibling_bank_txn_id}`` for every line that fires ``duplicate_release``.

    ``matched_bank_txn_ids`` is the set of bank lines Agent 1 itself marked
    ``status == "matched"`` -- used only to break the tie within an
    already-detected group (see module docstring), never to decide whether a
    group is a duplicate in the first place.
    """
    groups = defaultdict(list)
    for row in bank_rows:
        fp = _fingerprint(row)
        if fp is not None:
            groups[fp].append(row)

    fired = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        matched_members = [m for m in members if m["bank_txn_id"] in matched_bank_txn_ids]
        if len(matched_members) == 1:
            original = matched_members[0]
        else:
            original = min(members, key=lambda r: (r["value_date"], r["bank_txn_id"]))
        for member in members:
            if member["bank_txn_id"] != original["bank_txn_id"]:
                fired[member["bank_txn_id"]] = original["bank_txn_id"]
    return fired
