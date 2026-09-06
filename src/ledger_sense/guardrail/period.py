"""The ``out_of_period`` reporting window (spec §8.1).

The window is always explicit -- either passed in as CLI flags or derived
from ``--as-of`` -- never from a wall-clock read (law L7: no ``datetime.now()``
anywhere in this package).
"""

from datetime import datetime, timezone


def parse_instant(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` suffix accepted) into an aware UTC datetime."""
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _start_of_month(instant: datetime) -> datetime:
    return instant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _start_of_next_month(instant: datetime) -> datetime:
    start = _start_of_month(instant)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def resolve_period(as_of: datetime, period_start: str = None, period_end: str = None):
    """Return ``(period_start, period_end)`` as a half-open ``[start, end)`` UTC window.

    Explicit ``--period-start``/``--period-end`` CLI flags win, and must be given
    together. Otherwise the default is the calendar month containing ``as_of``.
    """
    if (period_start is None) != (period_end is None):
        raise ValueError("--period-start and --period-end must be given together")
    if period_start is not None:
        return parse_instant(period_start), parse_instant(period_end)
    return _start_of_month(as_of), _start_of_next_month(as_of)


def in_period(value_date: datetime, period_start: datetime, period_end: datetime) -> bool:
    """True if ``value_date`` falls inside the half-open ``[period_start, period_end)`` window."""
    return period_start <= value_date < period_end
