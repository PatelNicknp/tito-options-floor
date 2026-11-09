"""US stock market trading days (NYSE full-day holidays, 2026-2027 hard-coded)."""
from __future__ import annotations

from datetime import date, timedelta

HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS


def next_trading_day(day: date) -> date:
    nxt = day + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def add_trading_days(day: date, count: int) -> date:
    """The count-th trading day after day (day itself not counted)."""
    out = day
    for _ in range(count):
        out = next_trading_day(out)
    return out


def trading_days_between(start: date, end: date) -> int:
    """Trading days after start up to and including end. 0 if end <= start."""
    count, cur = 0, start
    while cur < end:
        cur = next_trading_day(cur)
        if cur <= end:
            count += 1
    return count
