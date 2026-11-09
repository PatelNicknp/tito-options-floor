"""Read-only fetchers for free public data. Direct requests only; no third-party proxies."""
from __future__ import annotations

import csv
import os
import io
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0"


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

    def get(self, url: str) -> bytes:
        if self.fail_pattern and self.fail_pattern in url:
            raise FetchError(f"{url}: simulated failure (TITO_FAIL_FETCH)")
        last_err: Exception | None = None
        for attempt in range(self.tries):
            wait = self.pause - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self._last = time.time()
                    return r.read()
            except urllib.error.HTTPError as exc:
                self._last = time.time()
                last_err = exc
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
