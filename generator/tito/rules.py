"""Daily-candle stock rules from the Page (all 'Ours' settings). Pure functions."""
from __future__ import annotations

from tito.indicators import ret, sma

SECTOR_ETF = {
    "Information Technology": "XLK", "Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
    "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC",
}

CHASE_SIGNAL_MAX = 0.04
CHASE_LIVE_MAX = 0.05
EARNINGS_TRADING_DAYS = 15


def check(name: str, ok: bool | None, value: str, limit: str) -> dict:
    return {"name": name, "pass": bool(ok), "value": value, "limit": limit}


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def market_rule(spy_closes: list[float]) -> dict:
    s50 = sma(spy_closes, 50)
    s50_prev = sma(spy_closes, 50, len(spy_closes) - 10)
    ok = s50 is not None and s50_prev is not None and spy_closes[-1] > s50 and s50 > s50_prev
    return check(
        "Market: SPY above a rising 50-day average",
        ok,
        f"SPY {spy_closes[-1]:.2f}, 50-day {s50:.2f} (10 sessions ago {s50_prev:.2f})" if s50 and s50_prev else "not enough data",
        "close > 50-day SMA and 50-day SMA higher than 10 sessions ago",
    )


def sector_rule(etf: str | None, etf_closes: list[float] | None, spy_closes: list[float]) -> dict:
    if not etf or not etf_closes:
        return check("Sector ETF strong", False, "sector ETF data missing", "ETF > 50-day SMA and 63-day return ≥ SPY's")
    s50 = sma(etf_closes, 50)
    r_etf, r_spy = ret(etf_closes, 63), ret(spy_closes, 63)
    ok = s50 is not None and r_etf is not None and r_spy is not None and etf_closes[-1] > s50 and r_etf >= r_spy
    return check("Sector ETF strong", ok, f"{etf} 63-day {pct(r_etf)} vs SPY {pct(r_spy)}; above 50-day: {bool(s50 and etf_closes[-1] > s50)}",
                 "ETF > 50-day SMA and 63-day return ≥ SPY's")


def stock_rules(bars: list[dict], spy_closes: list[float]) -> tuple[list[dict], dict | None]:
    """Return (checks, signal). bars are completed daily candles, oldest first; the last bar is the signal day."""
    closes = [b["close"] for b in bars]
    checks: list[dict] = []
    if len(bars) < 260:
        return [check("Enough history", False, f"{len(bars)} sessions", "≥ 260 completed sessions")], None
    r63, r21 = ret(closes, 63), ret(closes, 21)
    s63, s21 = ret(spy_closes, 63), ret(spy_closes, 21)
    rs_ok = None not in (r63, r21, s63, s21) and (r63 - s63) >= 0.10 and r21 > s21
    checks.append(check("Relative strength vs SPY", rs_ok, f"63-day {pct(r63)} vs {pct(s63)}; 21-day {pct(r21)} vs {pct(s21)}",
                        "63-day beats SPY by ≥ 10 points and 21-day beats SPY"))
    high252 = max(closes[-252:])
    checks.append(check("Near 52-week high", closes[-1] >= 0.85 * high252, f"{(1 - closes[-1] / high252) * 100:.1f}% below high",
                        "within 15% of the 252-day closing high"))
    s10, s20, s50 = sma(closes, 10), sma(closes, 20), sma(closes, 50)
    s50_prev = sma(closes, 50, len(closes) - 20)
    trend_ok = closes[-1] > s10 > s20 > s50 and s50 > s50_prev
    checks.append(check("Trend: price > 10 > 20 > 50-day, 50-day rising", trend_ok,
                        f"{closes[-1]:.2f} / {s10:.2f} / {s20:.2f} / {s50:.2f}", "close > 10 > 20 > 50-day SMA; 50-day up over 20 sessions"))
    base = find_base(bars)
    if base is None:
        checks.append(check("Tight base then breakout on volume", False, "no qualifying 10–40 session base and breakout on the last completed day",
                            "depth ≤ 25%, last-10 range ≤ 60% of base, volume dry-up, close > pivot on ≥ 1.5x volume"))
        return checks, None
    checks.append(check("Tight base then breakout on volume", True,
                        f"{base['length']}-session base, depth {pct(base['depth'])}, volume {base['volRatio']:.2f}x",
                        "depth ≤ 25%, last-10 range ≤ 60% of base, volume dry-up, close > pivot on ≥ 1.5x volume"))
    chase = closes[-1] / base["pivot"] - 1
    checks.append(check("Signal-day chase limit", chase <= CHASE_SIGNAL_MAX, f"{pct(chase)} above pivot", "≤ 4% above pivot"))
    last = bars[-1]
    signal = {
        "date": last["date"], "close": last["close"], "low": last["low"], "pivot": base["pivot"],
        "baseLow": base["low"], "baseLength": base["length"], "depth": base["depth"], "volRatio": base["volRatio"],
        "target": round(base["pivot"] + (base["pivot"] - base["low"]), 2),
        "stop": last["low"], "liveMin": base["pivot"], "liveMax": round(base["pivot"] * (1 + CHASE_LIVE_MAX), 2),
        "sma10": s10, "sma20": s20,
    }
    return checks, signal


def find_base(bars: list[dict]) -> dict | None:
    """Longest 10-40 session base ending the day before the last bar, broken out on the last bar."""
    if len(bars) < 92:
        return None
    last = bars[-1]
    vols = [b["volume"] for b in bars]
    avg50 = sum(vols[-51:-1]) / 50
    if avg50 <= 0:
        return None
    vol_ratio = last["volume"] / avg50
    best = None
    for length in range(10, 41):
        window = bars[-1 - length:-1]
        hi, lo = max(b["high"] for b in window), min(b["low"] for b in window)
        if hi <= 0 or hi == lo:
            continue
        depth = (hi - lo) / hi
        last10 = window[-10:]
        tight = (max(b["high"] for b in last10) - min(b["low"] for b in last10)) <= 0.60 * (hi - lo)
        dry = sum(b["volume"] for b in last10) / 10 <= avg50
        breakout = last["close"] > hi and vol_ratio >= 1.5
        if depth <= 0.25 and tight and dry and breakout:
            best = {"length": length, "pivot": round(hi, 2), "low": round(lo, 2), "depth": depth, "volRatio": vol_ratio}
    return best
