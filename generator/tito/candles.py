"""Stock Box-first daily candles, with Yahoo only for what Stock Box lacks. Pure logic, testable offline."""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable

from tito.calendar_us import is_trading_day

MIN_BARS = 260


def expected_session(now_et: datetime) -> date:
    """The last US trading session whose daily bar is complete (after 4:15 PM ET)."""
    day = now_et.date()
    after_close = (now_et.hour, now_et.minute) >= (16, 15)
    if is_trading_day(day) and after_close:
        return day
    from datetime import timedelta
    d = day - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def stockbox_trusted(meta: dict, expected: date) -> bool:
    """Stock Box's last bar is a finished session only after its after-close full run for that session."""
    return meta.get("mode") == "full" and meta.get("price_as_of") == expected.isoformat()


def splice(history: list[dict], recent: list[dict]) -> list[dict]:
    if not recent:
        return history
    first = recent[0]["date"]
    return [b for b in history if b["date"] < first] + recent


def load_candles(get_stockbox: Callable[[], list[dict]], get_recent: Callable[[], list[dict]],
                 get_full: Callable[[], list[dict]], trusted: bool, expected: date, unconfirmed: str | None) -> tuple[list[dict], str]:
    """Return (bars, source). Sources: stockbox, stockbox+yahoo-recent, stockbox-unconfirmed-dropped, yahoo, none."""
    exp = expected.isoformat()
    try:
        sb = get_stockbox()
    except Exception:  # noqa: BLE001
        sb = []
    if len(sb) >= MIN_BARS:
        if trusted and sb[-1]["date"] == exp:
            return sb, "stockbox"
        try:
            merged = splice(sb, get_recent())
            if merged and merged[-1]["date"] == exp:
                return merged, "stockbox+yahoo-recent"
        except Exception:  # noqa: BLE001
            pass
        # Could not confirm the newest bar: drop any bar from the unconfirmed (intraday) Stock Box run.
        kept = [b for b in sb if not unconfirmed or b["date"] < unconfirmed]
        return kept, "stockbox-unconfirmed-dropped"
    try:
        full = get_full()
        return full, "yahoo" if full else "none"
    except Exception:  # noqa: BLE001
        return [], "none"
