"""Read-only fetchers for free public data. Direct requests only; no third-party proxies."""
from __future__ import annotations

import csv
import os
import io
import json
import time
import http.cookiejar
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def now_pt() -> str:
    return datetime.now(PT).strftime("%Y-%m-%d %I:%M %p PT")


class FetchError(RuntimeError):
    pass


class Fetcher:
    """GET with retries and exponential backoff (2s, 4s, 8s) on timeouts, 429 and 5xx. 404 is not retried.
    TITO_FAIL_FETCH=<text> makes every URL containing <text> fail (used to test the failure path)."""

    def __init__(self, pause: float = 0.15, timeout: float = 25, tries: int = 4, backoff: float = 2.0) -> None:
        self.pause, self.timeout, self.tries, self.backoff = pause, timeout, tries, backoff
        self._last = 0.0
        self.fail_pattern = os.environ.get("TITO_FAIL_FETCH", "")
        self.retries_used = 0
        self._cj = http.cookiejar.CookieJar()
        self._yahoo_crumb: str | None = None

    def get(self, url: str, extra_headers: dict | None = None, allow_codes: tuple[int, ...] = ()) -> bytes:
        if self.fail_pattern and self.fail_pattern in url:
            raise FetchError(f"{url}: simulated failure (TITO_FAIL_FETCH)")
        last_err: Exception | None = None
        headers = {"User-Agent": UA, "Accept": "*/*"}
        if extra_headers:
            headers.update(extra_headers)
        for attempt in range(self.tries):
            wait = self.pause - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers=headers)
                self._cj.add_cookie_header(req)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self._last = time.time()
                    if hasattr(r, "info"):
                        self._cj.extract_cookies(r, req)
                    return r.read()
            except urllib.error.HTTPError as exc:
                self._last = time.time()
                last_err = exc
                if exc.code in allow_codes:
                    return exc.read() if exc.fp else b""
                if exc.code not in (408, 429) and exc.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_err = exc
            if attempt + 1 < self.tries:
                self.retries_used += 1
                time.sleep(self.backoff * 2 ** attempt)
        raise FetchError(f"{url}: {last_err}")

    def csv(self, url: str) -> list[dict]:
        return list(csv.DictReader(io.StringIO(self.get(url).decode("utf-8", "replace"))))

    def json(self, url: str) -> dict:
        return json.loads(self.get(url))


def completed_only(bars: list[dict], now_et: datetime) -> list[dict]:
    """Drop today's bar until 4:15 PM ET, so only finished sessions are used."""
    cutoff = now_et.replace(hour=16, minute=15, second=0, microsecond=0)
    if bars and bars[-1]["date"] == now_et.date().isoformat() and now_et < cutoff:
        return bars[:-1]
    return bars


def stockbox_ohlc(f: Fetcher, url: str) -> list[dict]:
    """US Stock Box daily candles (date,open,high,low,close,volume), oldest first."""
    bars = []
    for r in f.csv(url):
        try:
            bars.append({"date": r["date"][:10], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                         "close": float(r["close"]), "volume": float(r["volume"] or 0)})
        except (KeyError, ValueError):
            continue
    bars.sort(key=lambda b: b["date"])
    return bars


def yahoo_daily(f: Fetcher, symbol: str, now_et: datetime, rng: str = "2y") -> list[dict]:
    """Completed daily candles only: today's bar is dropped until 4:15 PM ET."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}?range={rng}&interval=1d&includePrePost=false"
    res = f.json(url)["chart"]["result"][0]
    stamps = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(stamps):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c, v):
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).date()
        bars.append({"date": day.isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": v})
    return completed_only(bars, now_et)


def cboe_chain(f: Fetcher, symbol: str) -> dict:
    """Cboe delayed quotes. The file has a timestamp with no stated time zone; quotes have no quote time."""
    sym = symbol.replace(".", "")
    return f.json(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json")



NASDAQ_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity/earnings",
}
YAHOO_HTML_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def yahoo_prepare(f: Fetcher) -> str | None:
    """Warm Yahoo cookies and return a crumb, or None if Yahoo will not issue one."""
    if f._yahoo_crumb:
        return f._yahoo_crumb
    try:
        f.get("https://fc.yahoo.com", allow_codes=(403, 404))
        f.get("https://finance.yahoo.com/quote/SPY/", allow_codes=(401, 403, 404))
        crumb = f.get("https://query2.finance.yahoo.com/v1/test/getcrumb").decode("utf-8", "replace").strip()
    except FetchError:
        return None
    if not crumb or crumb.startswith("{") or crumb.startswith("<") or len(crumb) > 40:
        return None
    f._yahoo_crumb = crumb
    return crumb


def yahoo_calendar_events(f: Fetcher, symbol: str) -> dict | None:
    """Next earnings from Yahoo quoteSummary calendarEvents. Estimated dates are flagged."""
    crumb = yahoo_prepare(f)
    if not crumb:
        return None
    url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.request.quote(symbol)}"
           f"?modules=calendarEvents&crumb={urllib.request.quote(crumb)}")
    try:
        data = f.json(url)
    except (FetchError, json.JSONDecodeError, TypeError, KeyError):
        return None
    res = ((data.get("quoteSummary") or {}).get("result") or [None])[0]
    if not res:
        return None
    ev = ((res.get("calendarEvents") or {}).get("earnings")) or {}
    dates = []
    for item in ev.get("earningsDate") or []:
        if isinstance(item, dict):
            raw, fmt = item.get("raw"), item.get("fmt")
            if raw:
                dates.append(datetime.fromtimestamp(raw, timezone.utc).astimezone(ET).date())
            elif fmt:
                try:
                    dates.append(date_from_fmt(fmt))
                except ValueError:
                    pass
    if not dates:
        return None
    return {
        "source": "Yahoo Finance calendarEvents",
        "date": min(dates),  # earlier of a start/end window
        "estimated": bool(ev.get("isEarningsDateEstimate")),
        "url": "https://query2.finance.yahoo.com/v10/finance/quoteSummary",
    }


def yahoo_quote_page_earnings(f: Fetcher, symbol: str) -> dict | None:
    """Next earnings from the public Yahoo quote page HTML (no crumb required)."""
    from tito.earnings import parse_yahoo_quote_html
    url = f"https://finance.yahoo.com/quote/{urllib.request.quote(symbol)}/"
    try:
        html = f.get(url, extra_headers=YAHOO_HTML_HEADERS).decode("utf-8", "replace")
    except (FetchError, UnicodeError):
        return None
    parsed = parse_yahoo_quote_html(html)
    if not parsed:
        return None
    parsed["url"] = url
    return parsed


def date_from_fmt(fmt: str):
    from datetime import date as _date
    return _date.fromisoformat(str(fmt)[:10])


def nasdaq_analyst_earnings_date(f: Fetcher, symbol: str) -> dict | None:
    """Nasdaq/Zacks 'earnings-date' page. Often has no upcoming date; then returns None."""
    url = f"https://api.nasdaq.com/api/analyst/{urllib.request.quote(symbol)}/earnings-date"
    try:
        data = json.loads(f.get(url, extra_headers=NASDAQ_HEADERS))
    except (FetchError, json.JSONDecodeError, TypeError):
        return None
    blob = data.get("data") or {}
    text = " ".join(str(blob.get(k) or "") for k in ("reportText", "announcement", "heading"))
    if "hasn't provided" in text.lower() or "has not provided" in text.lower():
        return None
    import re
    # e.g. "November 23, 2026" or "2026-11-23"
    iso = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    if iso:
        d = date_from_fmt(iso.group(1))
        return {"source": "Nasdaq earnings-date", "date": d, "estimated": "estimat" in text.lower(),
                "url": url}
    named = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(20\d{2})",
        text,
    )
    if not named:
        return None
    months = {m: i for i, m in enumerate(
        "January February March April May June July August September October November December".split(), 1)}
    from datetime import date as _date
    d = _date(int(named.group(3)), months[named.group(1)], int(named.group(2)))
    return {"source": "Nasdaq earnings-date", "date": d, "estimated": "estimat" in text.lower(), "url": url}


def nasdaq_calendar_day(f: Fetcher, day) -> list[dict] | None:
    """Rows on Nasdaq's earnings calendar for one date (free, no account).

    Returns None when the request fails or the payload looks blocked (so a
    GitHub-hosted runner can stop scanning instead of treating every day as empty).
    """
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}"
    try:
        data = json.loads(f.get(url, extra_headers=NASDAQ_HEADERS))
    except (FetchError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("data") is None:
        return None
    rows = ((data.get("data") or {}).get("rows")) or []
    out = []
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if not sym:
            continue
        out.append({"symbol": sym, "date": day, "time": row.get("time"), "name": row.get("name")})
    return out


def resolve_earnings_dates(f: Fetcher, tickers: list[str], stockbox: dict[str, str | None], asof) -> dict[str, dict]:
    """Stock Box first, then Yahoo calendarEvents and Nasdaq, cross-checked.

    Returns ticker -> merge_earnings result.
    """
    from tito.earnings import merge_earnings, scan_window

    tickers = [t for t in tickers if t]
    obs: dict[str, list] = {t: [] for t in tickers}
    stats = {"yahoo_html": 0, "yahoo_qs": 0, "nasdaq_analyst": 0,
             "nasdaq_days_ok": 0, "nasdaq_days_fail": 0, "nasdaq_aborted": False}
    for t in tickers:
        raw = stockbox.get(t)
        if raw and str(raw).strip():
            from tito.earnings import parse_iso
            d = parse_iso(raw)
            if d:
                obs[t].append({"source": "US Stock Box earnings_calendar.csv", "date": d, "estimated": False})

    for t in tickers:
        # Quote-page HTML first: no crumb, and it works when quoteSummary says Invalid Cookie.
        page = yahoo_quote_page_earnings(f, t)
        if page:
            obs[t].append(page)
            stats["yahoo_html"] += 1
        y = yahoo_calendar_events(f, t)
        if y:
            obs[t].append(y)
            stats["yahoo_qs"] += 1
        n = nasdaq_analyst_earnings_date(f, t)
        if n:
            obs[t].append(n)
            stats["nasdaq_analyst"] += 1

    from tito.earnings import nasdaq_scan_should_stop
    wanted = {t.upper(): t for t in tickers}
    day_cache: dict = {}

    def rows_on(day):
        if day not in day_cache:
            day_cache[day] = nasdaq_calendar_day(f, day)
        return day_cache[day]

    def add_nasdaq_hits(day, restrict=None) -> bool:
        rows = rows_on(day)
        if rows is None:
            stats["nasdaq_days_fail"] += 1
            return False
        stats["nasdaq_days_ok"] += 1
        for row in rows:
            t = wanted.get(row["symbol"])
            if t and (restrict is None or t in restrict):
                obs[t].append({"source": "Nasdaq earnings calendar", "date": row["date"], "estimated": False})
        return True

    still = {t for t in tickers if merge_earnings(obs[t], asof)["date"] is None}
    consecutive_fail = 0
    for d in scan_window(asof, 130):
        if not still:
            break
        ok = add_nasdaq_hits(d, still)
        consecutive_fail = 0 if ok else consecutive_fail + 1
        if nasdaq_scan_should_stop(consecutive_fail):
            stats["nasdaq_aborted"] = True
            break
        still = {t for t in still if merge_earnings(obs[t], asof)["date"] is None}

    from datetime import timedelta
    extra = set()
    for t in tickers:
        for o in obs[t]:
            d = o["date"]
            if d < asof:
                continue
            extra.add(d)
            for delta in (-3, -2, -1, 1, 2, 3):
                nxt = d + timedelta(days=delta)
                if nxt.weekday() < 5 and nxt >= asof:
                    extra.add(nxt)
    for d in sorted(extra):
        add_nasdaq_hits(d)

    merged = {t: merge_earnings(obs[t], asof) for t in tickers}
    merged["_stats"] = stats
    return merged
