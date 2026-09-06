"""§6.4 -- ownership resolves to a named person, never a queue.

Eleven people on a fixed roster: three AR, three AP, three recon-ops, two
controllers. Desk is a pure function of category + direction (§6.4). The
individual within a desk is a pure function of the subject's *counterparty*,
hashed with blake2b -- never Python's ``hash()`` (per-process salted, would
silently reshuffle owners between runs) and never the transaction id (would
put the same counterparty in a different inbox every time it shows up under a
new bank_txn_id/ledger_id). No capacity spill: whoever the hash points to
gets the item, however deep their queue already is -- load is only reported,
on ``owner_queues.csv``, never rebalanced.
"""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Owner:
    owner_id: str
    owner_name: str
    owner_team: str


AR = (
    Owner("AR-1", "Ava Brennan", "AR"),
    Owner("AR-2", "Priya Natarajan", "AR"),
    Owner("AR-3", "Marcus Cole", "AR"),
)
AP = (
    Owner("AP-1", "Derek Simmons", "AP"),
    Owner("AP-2", "Lena Ortiz", "AP"),
    Owner("AP-3", "Samuel Reyes", "AP"),
)
RECON_OPS = (
    Owner("RECON-1", "Nora Whitfield", "recon_ops"),
    Owner("RECON-2", "Owen Castillo", "recon_ops"),
    Owner("RECON-3", "Ines Falk", "recon_ops"),
)
CONTROLLER = (
    Owner("CTRL-1", "Grace Halden", "controller"),
    Owner("CTRL-2", "Victor Amos", "controller"),
)

DESKS = {"AR": AR, "AP": AP, "recon_ops": RECON_OPS, "controller": CONTROLLER}
ROSTER = AR + AP + RECON_OPS + CONTROLLER

assert len(ROSTER) == 11
assert [len(DESKS[d]) for d in ("AR", "AP", "recon_ops", "controller")] == [3, 3, 3, 2]


def desk_for(category: str, inbound: bool) -> str:
    """§6.4 desk rule: suspect_posting -> controller, duplicate -> recon_ops,
    else AR if the money is inbound, else AP."""
    if category == "suspect_posting":
        return "controller"
    if category == "duplicate":
        return "recon_ops"
    return "AR" if inbound else "AP"


def individual_for(desk: str, counterparty_key: str) -> Owner:
    """Pick a person within ``desk`` deterministically from the counterparty key.

    blake2b, not Python's ``hash()``: ``hash()`` is randomized per interpreter
    process (PYTHONHASHSEED) and would put the same counterparty on a
    different desk member every run -- breaking both determinism (law L4) and
    the "same counterparty, same owner" guarantee the spec wants (§6.4).
    """
    team = DESKS[desk]
    digest = hashlib.blake2b(counterparty_key.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest, "big") % len(team)
    return team[index]


def assign(category: str, inbound: bool, counterparty_key: str) -> tuple[Owner, str]:
    """Resolve (category, direction, counterparty) to one named owner.

    Returns ``(owner, assignment_basis)`` where ``assignment_basis`` is a
    short audit string explaining both the desk pick and the individual pick.
    """
    desk = desk_for(category, inbound)
    owner = individual_for(desk, counterparty_key)
    basis = (
        f"desk={desk}(category={category},inbound={inbound})"
        f";individual=blake2b(counterparty_key)%{len(DESKS[desk])}={owner.owner_id}"
    )
    return owner, basis
