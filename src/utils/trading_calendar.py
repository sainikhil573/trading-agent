"""NSE 2026 trading calendar. Weekends + listed NSE holidays are non-trading days."""

from __future__ import annotations
import datetime

# NSE officially declared trading holidays for 2026.
# Lunar-calendar holidays (marked ~) are approximate — verify against NSE circular.
_NSE_HOLIDAYS_2026 = [
    "2026-01-26",  # Republic Day
    "2026-02-26",  # Mahashivratri (~)
    "2026-03-13",  # Holi (~)
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day / Labour Day
    "2026-05-12",  # Buddha Purnima (~)
    "2026-06-07",  # Eid ul-Adha (~, subject to moon sighting)
    "2026-08-15",  # Independence Day (Saturday — already weekend)
    "2026-08-24",  # Ganesh Chaturthi (~)
    "2026-10-02",  # Gandhi Jayanti / Dussehra (~)
    "2026-10-20",  # Diwali Laxmi Pujan (~)
    "2026-10-21",  # Diwali Balipratipada (~)
    "2026-11-05",  # Gurunanak Jayanti (~)
    "2026-12-25",  # Christmas
]

NSE_HOLIDAYS: set[datetime.date] = {
    datetime.date.fromisoformat(d) for d in _NSE_HOLIDAYS_2026
}


def is_trading_day(dt: datetime.date | None = None) -> bool:
    """Return True if the given date is an NSE trading day."""
    if dt is None:
        import pytz
        dt = datetime.datetime.now(pytz.timezone("Asia/Kolkata")).date()
    if dt.weekday() >= 5:        # Saturday=5, Sunday=6
        return False
    return dt not in NSE_HOLIDAYS


def next_trading_day(dt: datetime.date | None = None) -> datetime.date:
    if dt is None:
        dt = datetime.date.today()
    candidate = dt + datetime.timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += datetime.timedelta(days=1)
    return candidate
