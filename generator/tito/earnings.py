"""Next-earnings dates: merge several free calendars without loosening the 15-day rule."""
from __future__ import annotations

from datetime import date, timedelta

DISAGREE_DAYS = 3  # more than a few days → use the earlier date (safer)


def parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def merge_earnings(observations: list[dict], asof: date) -> dict:
    """Combine {source, date, estimated} rows. Past dates are not a next date.

    If future dates from different sources disagree by more than DISAGREE_DAYS,
    the earlier date is used (safer). An estimated/projected date still counts
    as known and is labelled estimated.
    """
    cleaned = []
    for raw in observations:
        d = raw.get("date") if isinstance(raw.get("date"), date) else parse_iso(raw.get("date"))
        if not d:
            continue
        cleaned.append({
            "source": raw.get("source") or "unknown",
            "date": d,
            "estimated": bool(raw.get("estimated")),
        })
    future = [o for o in cleaned if o["date"] >= asof]
    if not future:
        return {
            "date": None,
            "estimated": False,
            "source": None,
            "sources": cleaned,
            "disagreementDays": 0,
            "usedEarlierDueToDisagreement": False,
        }
    dates = sorted({o["date"] for o in future})
    chosen = dates[0]
    spread = (dates[-1] - dates[0]).days
    contributors = [o for o in future if o["date"] == chosen]
    estimated = any(o["estimated"] for o in contributors)
    source = "; ".join(dict.fromkeys(o["source"] for o in contributors))
    return {
        "date": chosen,
        "estimated": estimated,
        "source": source,
        "sources": cleaned,
        "disagreementDays": spread,
        "usedEarlierDueToDisagreement": spread > DISAGREE_DAYS,
    }


def earnings_label(merged: dict) -> str:
    """Short card label for a resolved date (or unknown)."""
    if not merged.get("date"):
        return "earnings date unknown"
    day = merged["date"].isoformat()
    tag = " (estimated)" if merged.get("estimated") else ""
    src = f" · source: {merged['source']}" if merged.get("source") else ""
    extra = ""
    if merged.get("usedEarlierDueToDisagreement"):
        extra = f" (earlier of disagreeing calendars, {merged['disagreementDays']} days apart)"
    return f"{day}{tag}{src}{extra}"


def scan_window(asof: date, calendar_days: int = 130) -> list[date]:
    """Weekdays from asof through asof+calendar_days (Nasdaq calendar is dated)."""
    out = []
    for i in range(0, calendar_days + 1):
        d = asof + timedelta(days=i)
        if d.weekday() < 5:
            out.append(d)
    return out
