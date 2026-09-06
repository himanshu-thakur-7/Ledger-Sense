"""The guardrail veto every learned rule must clear before it fires (spec
§7.3, law L12).

Imports guardrail's *public* ``would_block_or_hold`` function only -- its
documented API, not its files (allowed per L1's own carve-out; see
``ledger_sense.guardrail.engine.would_block_or_hold``'s docstring, which
exists for exactly this call site).
"""

from ledger_sense.guardrail import would_block_or_hold


def veto(*, outcome_row: dict, ledger_row: dict, bank_row: dict, as_of: str, policy=None,
         period_start: str = None, period_end: str = None) -> str:
    """The verdict (``allow`` / ``hold`` / ``block``) this line would get if
    it were released right now, independently re-derived by Agent 4's own
    policy engine. A learned rule may only fire on ``allow`` -- the caller
    is responsible for treating anything else as a veto (law L12).

    ``duplicate_release`` cannot be evaluated from a single line (documented
    limitation of ``would_block_or_hold`` itself); callers must not offer a
    row already flagged ``relation == "duplicate"`` to this function at all.

    ``period_start``/``period_end`` mirror guardrail's own ``--period-start``/
    ``--period-end`` override for ``out_of_period`` (spec §8.1); omitting
    both falls back to ``would_block_or_hold``'s own default (the calendar
    month containing ``as_of``), same as guardrail's CLI.
    """
    counterparty_name = (ledger_row or {}).get("counterparty_name") or (bank_row or {}).get(
        "counterparty_name_raw", ""
    )
    line = {
        "amount": bank_row["amount"],
        "currency": bank_row.get("currency") or (ledger_row or {}).get("currency", ""),
        "value_date": bank_row["value_date"],
        "as_of": as_of,
        "counterparty_name": counterparty_name,
        "counterparty_id": (ledger_row or {}).get("counterparty_id"),
        "upstream_reason": outcome_row.get("reason"),
    }
    if period_start and period_end:
        line["period_start"], line["period_end"] = period_start, period_end
    return would_block_or_hold(line, policy=policy)
