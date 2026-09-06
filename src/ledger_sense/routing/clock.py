"""§6.5 -- the SLA clock.

Every timestamp this module produces is a pure function of values the caller
hands it -- ``opened_at``, ``sla_hours`` and the run's explicit ``as_of``.
Nothing here (or anywhere else in this package) calls ``datetime.now()``:
the whole point of an explicit ``--as-of`` is that a compliance/ops report
run twice against the same inputs and the same ``as-of`` produces the exact
same due dates, breach flags and queue -- reruns are byte-identical (law L4)
only if the clock never consults the wall clock.

Design note on ``opened_at`` (spec is prose here, not a table like §6.2): the
clock's *reference instant* -- "now" for at_risk/breached purposes -- is
always the run's explicit ``--as-of``, never wall-clock time. But an
exception's ``opened_at`` is the underlying transaction's own timestamp
(bank ``value_date`` for a bank/pair subject, ledger ``booked_at`` for a
book-side subject) rather than the run's ``as_of``: that is the only reading
under which ``age_days``, ``at_risk`` and ``breached`` can ever mean anything
across a single run (if ``opened_at`` were pinned to ``as_of`` every run
would report zero age and nothing could ever be at_risk/breached, which no
column in §6.7's output contract would need to exist to say). This is
documented, not asserted quietly, per the pattern the matcher itself uses for
its own §5.4 known limitation.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

BASE_SLA_HOURS = {
    "duplicate": Decimal(24),
    "amount_mismatch": Decimal(48),
    "timing": Decimal(120),
    "unidentified_counterpart": Decimal(72),
    "suspect_posting": Decimal(4),
}

SEVERITY_MULTIPLIER = {
    "P1": Decimal("0.5"),
    "P2": Decimal("1.0"),
    "P3": Decimal("1.5"),
}

# §6.5 amount buckets. Deliberately no FX conversion (§14 explicitly rules out
# multi-currency modeling): "rough USD" means the raw abs(amount) magnitude,
# whatever the currency, is used as-is.
SEVERITY_HIGH_FLOOR = Decimal(10000)
SEVERITY_MED_FLOOR = Decimal(1000)

AT_RISK_FRACTION = Decimal("0.25")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def severity_for(category: str, amount: Decimal) -> str:
    """suspect_posting is always P1 regardless of amount; otherwise bucket on
    abs(amount) in rough USD."""
    if category == "suspect_posting":
        return "P1"
    magnitude = abs(amount)
    if magnitude >= SEVERITY_HIGH_FLOOR:
        return "P1"
    if magnitude >= SEVERITY_MED_FLOOR:
        return "P2"
    return "P3"


def sla_hours_for(category: str, severity: str) -> Decimal:
    return BASE_SLA_HOURS[category] * SEVERITY_MULTIPLIER[severity]


@dataclass(frozen=True)
class ClockResult:
    opened_at: datetime
    due_at: datetime
    now: datetime
    sla_hours: Decimal
    hours_remaining: Decimal
    age_days: int
    at_risk: bool
    breached: bool
    sla_state: str
    sla_display: str


def compute(opened_at: datetime, sla_hours: Decimal, now: datetime) -> ClockResult:
    """Plain elapsed hours, no business-hour calendar (documented
    simplification, per §6.5)."""
    due_at = opened_at + timedelta(hours=float(sla_hours))
    remaining_hours = Decimal(str((due_at - now).total_seconds() / 3600))
    breached = now >= due_at
    at_risk = (not breached) and remaining_hours < (sla_hours * AT_RISK_FRACTION)
    sla_state = "breached" if breached else "at_risk" if at_risk else "on_track"
    age_days = max((now - opened_at).days, 0)
    if breached:
        display = f"breached ({abs(remaining_hours):.1f}h overdue)"
    else:
        display = f"{sla_state} ({remaining_hours:.1f}h left)"
    return ClockResult(
        opened_at=opened_at,
        due_at=due_at,
        now=now,
        sla_hours=sla_hours,
        hours_remaining=remaining_hours,
        age_days=age_days,
        at_risk=at_risk,
        breached=breached,
        sla_state=sla_state,
        sla_display=display,
    )
