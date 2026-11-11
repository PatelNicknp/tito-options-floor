"""Next-earnings dates: merge several free calendars without loosening the 15-day rule."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

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



_EARN_BLOCK = re.compile(r'earningsDate(?:\\"|")?:\[(.*?)\]', re.S)
_FMT = re.compile(r'(?:\\"|")?fmt(?:\\"|")?:(?:\\"|")?(20\d{2}-\d{2}-\d{2})')
_RAW = re.compile(r'(?:\\"|")?raw(?:\\"|")?:(\d{9,12})')
_EST = re.compile(r'(?:\\"|")?isEarningsDateEstimate(?:\\"|")?:(true|false)')


def parse_yahoo_quote_html(html: str) -> dict | None:
    """Read the next earnings date from a Yahoo quote page.

    The page embeds calendarEvents as JSON or as escaped JSON inside a script
    tag (\\"earningsDate\\":[{\\"fmt\\":\\"2026-12-03\\"}]). No Yahoo crumb is needed,
    which matters on CI where quoteSummary often fails with Invalid Cookie.
    """
    if not html:
        return None
    dates: list[date] = []
    for blob in _EARN_BLOCK.findall(html):
        fmts = _FMT.findall(blob)
        if fmts:
            for item in fmts:
                d = parse_iso(item)
                if d:
                    dates.append(d)
            continue
        for raw in _RAW.findall(blob):
            try:
                dates.append(datetime.fromtimestamp(int(raw), timezone.utc).astimezone(ET).date())
            except (ValueError, OSError, OverflowError):
                continue
    if not dates:
        return None
    flags = _EST.findall(html)
    return {
        "source": "Yahoo Finance quote page",
        "date": min(dates),
        "estimated": any(flag == "true" for flag in flags[:6]),
    }


NASDAQ_FAIL_LIMIT = 8


def nasdaq_scan_should_stop(consecutive_failures: int, limit: int = NASDAQ_FAIL_LIMIT) -> bool:
    """Stop a by-date scan after enough failed days (blocked or empty error responses)."""
    return consecutive_failures >= limit
