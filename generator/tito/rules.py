"""Daily-candle stock rules from the Page (all 'Ours' settings). Pure functions."""
from __future__ import annotations

import math

from tito.indicators import ret, sma

SECTOR_ETF = {
    "Information Technology": "XLK", "Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
    "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC",
}

CHASE_SIGNAL_MAX = 0.04
CHASE_LIVE_MAX = 0.05
EARNINGS_TRADING_DAYS = 15
# Test: breakout-day volume vs the prior 50-day average. Was 1.50; never below 1.0.
BREAKOUT_VOL_WAS = 1.50
BREAKOUT_VOL_MIN = 1.25
# Test: shortest tight base. Was 10 sessions; max stays 40.
BASE_LEN_MIN_WAS = 10
BASE_LEN_MIN = 5
BASE_LEN_MAX = 40


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


def recent_len(length: int) -> int:
    """Trailing sessions used for tightness (range ≤ 60% of the base) and volume dry-up.

    The original rule always used the last 10 sessions, which assumes the base is at least
    10 sessions long. min(10, length) cannot work for a 5-session base: that slice would
    be the whole base, and a range cannot be ≤ 60% of itself. So bases of 10 or more still
    use the last 10 (unchanged); shorter test bases (5–9) use the last half, rounded up,
    at least 2 sessions.
    """
    if length >= BASE_LEN_MIN_WAS:
        return 10
    return max(2, (length + 1) // 2)


def breakout_limit_text() -> str:
    return (f"{BASE_LEN_MIN}–{BASE_LEN_MAX} session base (test; was {BASE_LEN_MIN_WAS}–{BASE_LEN_MAX}), "
            f"depth ≤ 25%, last {BASE_LEN_MIN_WAS} sessions (or last half if the base is shorter) range ≤ 60% of base, "
            f"volume dry-up, close > pivot on ≥ {BREAKOUT_VOL_MIN:g}× volume (test; was {BREAKOUT_VOL_WAS:g}×)")


def stock_rules(bars: list[dict], spy_closes: list[float], min_vol: float | None = None,
               min_length: int | None = None) -> tuple[list[dict], dict | None]:
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
    base = find_base(bars, min_vol, min_length)
    if base is None:
        checks.append(check("Tight base then breakout on volume", False,
                            f"no qualifying {BASE_LEN_MIN}–{BASE_LEN_MAX} session base (test; was {BASE_LEN_MIN_WAS}–{BASE_LEN_MAX}) and breakout on the last completed day",
                            breakout_limit_text()))
        return checks, None
    checks.append(check("Tight base then breakout on volume", True,
                        f"{base['length']}-session base, depth {pct(base['depth'])}, volume {base['volRatio']:.2f}x",
                        breakout_limit_text()))
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


def _window_stats(window: list[dict], avg50: float) -> tuple[float, float, float, bool, bool] | None:
    hi, lo = max(b["high"] for b in window), min(b["low"] for b in window)
    if hi <= 0 or hi == lo:
        return None
    depth = (hi - lo) / hi
    n = recent_len(len(window))
    recent = window[-n:]
    tight = (max(b["high"] for b in recent) - min(b["low"] for b in recent)) <= 0.60 * (hi - lo)
    dry = sum(b["volume"] for b in recent) / n <= avg50
    return hi, lo, depth, tight, dry


def find_base(bars: list[dict], min_vol: float | None = None, min_length: int | None = None) -> dict | None:
    """Longest qualifying base ending the day before the last bar, broken out on the last bar."""
    need = BREAKOUT_VOL_MIN if min_vol is None else min_vol
    lo_len = BASE_LEN_MIN if min_length is None else min_length
    if len(bars) < 92:
        return None
    last = bars[-1]
    vols = [b["volume"] for b in bars]
    avg50 = sum(vols[-51:-1]) / 50
    if avg50 <= 0:
        return None
    vol_ratio = last["volume"] / avg50
    best = None
    for length in range(lo_len, BASE_LEN_MAX + 1):
        window = bars[-1 - length:-1]
        stats = _window_stats(window, avg50)
        if stats is None:
            continue
        hi, lo, depth, tight, dry = stats
        breakout = last["close"] > hi and vol_ratio >= need
        if depth <= 0.25 and tight and dry and breakout:
            best = {"length": length, "pivot": round(hi, 2), "low": round(lo, 2), "depth": depth, "volRatio": vol_ratio}
    return best


def classify_breakout_fail(bars: list[dict], min_length: int | None = None) -> dict:
    """Split a failed breakout check into volume vs tight-base vs breakout price. Does not change the rule."""
    empty = {"kind": "base", "volRatio": None, "detail": "not enough history or no volume average"}
    lo_len = BASE_LEN_MIN if min_length is None else min_length
    if len(bars) < 92:
        return empty
    last = bars[-1]
    vols = [b["volume"] for b in bars]
    avg50 = sum(vols[-51:-1]) / 50
    if avg50 <= 0:
        return empty
    vol_ratio = last["volume"] / avg50
    has_tight = False
    has_price = False
    best_len = None
    for length in range(lo_len, BASE_LEN_MAX + 1):
        window = bars[-1 - length:-1]
        stats = _window_stats(window, avg50)
        if stats is None:
            continue
        hi, lo, depth, tight, dry = stats
        if depth <= 0.25 and tight and dry:
            has_tight = True
            best_len = length
            if last["close"] > hi:
                has_price = True
    if has_price:
        if vol_ratio >= BREAKOUT_VOL_WAS:
            return {"kind": "pass_old", "volRatio": vol_ratio, "length": best_len,
                    "detail": f"volume {vol_ratio:.2f}× meets the old {BREAKOUT_VOL_WAS:g}× bar"}
        if vol_ratio >= BREAKOUT_VOL_MIN:
            return {"kind": "volume_between", "volRatio": vol_ratio, "length": best_len,
                    "detail": f"volume {vol_ratio:.2f}× meets the test {BREAKOUT_VOL_MIN:g}× bar but not the old {BREAKOUT_VOL_WAS:g}× bar"}
        return {"kind": "volume", "volRatio": vol_ratio, "length": best_len,
                "detail": f"tight base and close above pivot, but volume {vol_ratio:.2f}× is below {BREAKOUT_VOL_MIN:g}×"}
    if has_tight:
        return {"kind": "price", "volRatio": vol_ratio, "length": best_len,
                "detail": "a tight base exists but the last close was not above the pivot"}
    return {"kind": "base", "volRatio": vol_ratio, "length": None,
            "detail": f"no qualifying {lo_len}–{BASE_LEN_MAX} session tight base (depth, tightness or dry-up)"}


def find_setup(bars: list[dict], min_length: int | None = None) -> dict | None:
    """A qualifying tight base that is not yet a completed breakout.

    Waiting: close has not printed above the pivot.
    Light volume: close is above the pivot but breakout-day volume is below BREAKOUT_VOL_MIN.
    A completed breakout (close above pivot and volume at the bar) is not a setup.
    """
    lo_len = BASE_LEN_MIN if min_length is None else min_length
    if len(bars) < 92:
        return None
    last = bars[-1]
    vols = [b["volume"] for b in bars]
    avg50 = sum(vols[-51:-1]) / 50
    if avg50 <= 0:
        return None
    vol_ratio = last["volume"] / avg50
    best = None
    for length in range(lo_len, BASE_LEN_MAX + 1):
        window = bars[-1 - length:-1]
        stats = _window_stats(window, avg50)
        if stats is None:
            continue
        hi, lo, depth, tight, dry = stats
        if depth <= 0.25 and tight and dry:
            best = {"length": length, "pivot": hi, "low": lo, "depth": depth}
    if best is None:
        return None
    pivot = best["pivot"]
    close = last["close"]
    above = close > pivot
    if above and vol_ratio >= BREAKOUT_VOL_MIN:
        return None
    needed = math.ceil(BREAKOUT_VOL_MIN * avg50 - 1e-9)
    return {
        "length": best["length"],
        "pivot": round(pivot, 2),
        "low": round(best["low"], 2),
        "depth": best["depth"],
        "close": close,
        "date": last["date"],
        "volRatio": vol_ratio,
        "avg50Volume": avg50,
        "lastVolume": last["volume"],
        "volumeNeeded": int(needed),
        "pctBelowPivot": (pivot - close) / pivot * 100,
        "status": "light_volume" if above else "waiting",
    }

