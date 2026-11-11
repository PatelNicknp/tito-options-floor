"""Contract choice, price checks and scenarios for long calls (Page: Contract choice; Scenarios and Greeks)."""
from __future__ import annotations

import math
import re
from datetime import date

from tito.calendar_us import add_trading_days
from tito.money import max_entry_ask, max_entry_net_debit
from tito.options_math import american_call, bs_call, call_greeks

OCC = re.compile(r"^([A-Z0-9.]+?)(\d{6})([CP])(\d{8})$")
DTE_MIN, DTE_MAX = 35, 70
DELTA_MIN, DELTA_MAX = 0.35, 0.60
MIN_OI, MIN_VOL = 500, 50
IV_RATIO_MAX = 1.3
SKEW_POINTS = 0.03
SPEED_MAX = 1.0
GATE_MULT = 2.0
TARGET_MULT = 3.0


def parse_occ(symbol: str) -> dict | None:
    m = OCC.match(symbol or "")
    if not m:
        return None
    root, ymd, cp, k = m.groups()
    return {"root": root, "expiry": date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:])), "type": cp, "strike": int(k) / 1000}


def spread_ok(bid: float, ask: float) -> bool:
    mid = (bid + ask) / 2
    return ask - bid <= max(0.05, 0.12 * mid) + 1e-9


ASK_ABOVE_STOCK = "call ask above the stock price"
ASK_FAR_ABOVE_MODEL = "call ask far above model value"


def quote_sane(ask: float, stock: float, model_value: float | None = None) -> str | None:
    """Reject delayed call quotes that break basic bounds.

    A call should not cost more than the stock (C ≤ S). An ask far above the
    model value is treated as a bad or stale quote, not a real price to trade.
    The SLN 2026-12-18 $2.50 call quoted at $20 on a ~$19.79 stock is the
    example: intrinsic is about $17.29, but $20 is already above the stock.
    """
    if ask is None or stock is None:
        return None
    try:
        ask_f, stock_f = float(ask), float(stock)
    except (TypeError, ValueError):
        return None
    if ask_f > stock_f + 0.01:
        return ASK_ABOVE_STOCK
    if model_value is not None:
        try:
            model_f = float(model_value)
        except (TypeError, ValueError):
            model_f = 0.0
        if model_f > 0 and ask_f > max(model_f * 2.5, model_f + 1.0) + 1e-9:
            return ASK_FAR_ABOVE_MODEL
    return None


def fits_dollar_cap(max_loss: float | None, cap: float) -> bool:
    if max_loss is None or cap is None:
        return True
    return float(max_loss) <= float(cap) + 1e-9


def _sanity(calls: list[dict], spot: float) -> set[str]:
    """Symbols on expiries whose synchronized quotes break strike ordering or the intrinsic bound."""
    bad: set[str] = set()
    by_exp: dict[date, list[dict]] = {}
    for c in calls:
        by_exp.setdefault(c["expiry"], []).append(c)
    for rows in by_exp.values():
        rows.sort(key=lambda r: r["strike"])
        broken = False
        for lo, hi in zip(rows, rows[1:]):
            if hi["bid"] > 0 and lo["ask"] > 0 and hi["bid"] > lo["ask"] + 0.01:
                broken = True
        for r in rows:
            if r["ask"] > 0 and r["ask"] + 0.01 < spot - r["strike"]:
                broken = True
        if broken:
            bad.update(r["symbol"] for r in rows)
    return bad


def evaluate(ticker: str, options: list[dict], spot: float, signal: dict, entry: date, rate: float,
             hv20: float | None, hv60: float | None, settings: dict) -> dict:
    """Return {'picks': [...], 'checked': n, 'reasons': {reason: count}}."""
    calls = []
    for o in options:
        p = parse_occ(o.get("option", ""))
        if not p or p["type"] != "C":
            continue
        calls.append({"symbol": o["option"], "root": p["root"], "expiry": p["expiry"], "strike": p["strike"],
                      "bid": float(o.get("bid") or 0), "ask": float(o.get("ask") or 0), "iv": float(o.get("iv") or 0),
                      "oi": float(o.get("open_interest") or 0), "volume": float(o.get("volume") or 0),
                      "lastTradeTime": o.get("last_trade_time")})
    bad = _sanity(calls, spot)
    hv = max([v for v in (hv20, hv60) if v] or [0])
    fee = settings["feePerContract"]
    qty = settings["contracts"]
    x = max_entry_ask(settings["capDollars"], fee, qty, settings["startingCash"])
    reasons: dict[str, int] = {}
    picks = []
    by_exp: dict[date, list[dict]] = {}
    for c in calls:
        by_exp.setdefault(c["expiry"], []).append(c)
    for c in calls:
        def fail(why: str) -> None:
            reasons[why] = reasons.get(why, 0) + 1
        dte = (c["expiry"] - entry).days
        if c["root"] != ticker.replace(".", ""):
            fail("non-standard contract"); continue
        if not DTE_MIN <= dte <= DTE_MAX:
            fail("expiry outside 35-70 days"); continue
        if c["strike"] >= signal["target"]:
            fail("strike not below target"); continue
        if c["bid"] <= 0 or c["ask"] < c["bid"]:
            fail("no two-sided quote"); continue
        insane = quote_sane(c["ask"], spot)
        if insane:
            fail(insane); continue
        if c["iv"] <= 0:
            fail("IV missing"); continue
        g = call_greeks(spot, c["strike"], dte, c["iv"], rate)
        if g is None or not DELTA_MIN <= g["delta"] <= DELTA_MAX:
            fail("delta outside 0.35-0.60"); continue
        if not spread_ok(c["bid"], c["ask"]):
            fail("bid-ask spread too wide"); continue
        if c["oi"] < MIN_OI or c["volume"] < MIN_VOL:
            fail("open interest < 500 or volume < 50"); continue
        if hv <= 0 or c["iv"] > IV_RATIO_MAX * hv:
            fail("IV above 1.3x realized volatility" if hv > 0 else "realized volatility missing"); continue
        row = sorted(by_exp[c["expiry"]], key=lambda r: r["strike"])
        i = next(k for k, r in enumerate(row) if r["symbol"] == c["symbol"])
        neigh = [r["iv"] for r in (row[i - 1] if i > 0 else None, row[i + 1] if i + 1 < len(row) else None) if r and r["iv"] > 0]
        if not neigh:
            fail("no neighbouring strikes to check skew"); continue
        if c["iv"] - sum(neigh) / len(neigh) > SKEW_POINTS:
            fail("IV more than 3 points above neighbours"); continue
        if c["symbol"] in bad:
            fail("quotes inconsistent across strikes (recheck)"); continue
        # Speed check
        d15 = add_trading_days(entry, 15)
        t15 = (d15 - entry).days / 365
        speed = math.log(signal["target"] / spot) / (c["iv"] * math.sqrt(t15)) if signal["target"] > spot else 0.0
        if speed > SPEED_MAX:
            fail("speed check above 1.0"); continue
        # Gate: at target on the 10th trading day, IV unchanged
        d10 = add_trading_days(entry, 10)
        model10 = american_call(signal["target"], c["strike"], (c["expiry"] - d10).days, c["iv"], rate)
        spread = c["ask"] - c["bid"]
        debit = c["ask"] * 100 * qty + fee * qty
        gate_value = (model10["value"] - spread) * 100 * qty - fee * qty
        if not model10["converged"]:
            fail("model did not converge"); continue
        if gate_value < GATE_MULT * debit:
            fail("scenario gate: under 2x the entry cost at target on day 10"); continue
        now_model = american_call(spot, c["strike"], dte, c["iv"], rate)
        affordable = x is not None and c["ask"] <= x
        if not affordable:
            fail("over the dollar cap (skipped, not replaced)"); continue
        scenarios = []
        for label, s in (("At stop", signal["stop"]), ("Unchanged", spot), ("At target", signal["target"])):
            for tdays in (0, 5, 15):
                day = add_trading_days(entry, tdays) if tdays else entry
                for dv in (-0.05, 0.0, 0.05):
                    v = american_call(s, c["strike"], (c["expiry"] - day).days, max(c["iv"] + dv, 0.01), rate)["value"]
                    pnl = v * 100 * qty - fee * qty - debit
                    scenarios.append({"stock": label, "price": round(s, 2), "tradingDay": tdays, "ivShift": round(dv * 100),
                                      "value": round(v * 100 * qty, 2), "pnl": round(pnl, 2)})
        picks.append({
            "symbol": c["symbol"], "expiry": c["expiry"].isoformat(), "strike": c["strike"], "dte": dte, "type": "CALL",
            "bid": c["bid"], "ask": c["ask"], "mid": round((c["bid"] + c["ask"]) / 2, 3), "spread": round(spread, 2),
            "oi": int(c["oi"]), "volume": int(c["volume"]), "iv": round(c["iv"], 4),
            "hv20": round(hv20, 4) if hv20 else None, "hv60": round(hv60, 4) if hv60 else None,
            "ivRatio": round(c["iv"] / hv, 2), "greeks": g, "lastTradeTime": c["lastTradeTime"],
            "model": {"name": "CRR binomial American, 800 steps, no dividends", "valueNow": round(now_model["value"], 3),
                      "converged": now_model["converged"] and model10["converged"]},
            "speed": round(speed, 2),
            "gate": {"valueAtTargetDay10": round(gate_value, 2), "needed": round(GATE_MULT * debit, 2)},
            "entryCost": round(debit, 2),
            "maxLoss": round(c["ask"] * 100 * qty + 2 * fee * qty, 2),
            "maxProfit": "No fixed limit (stock could keep rising)",
            "breakevenAtExpiry": round(c["strike"] + c["ask"] + 2 * fee / 100, 2),
            "valueAtExpiryAtTarget": round(max(0.0, signal["target"] - c["strike"]) * 100 * qty, 2),
            "optionProfitTargetBid": round((TARGET_MULT * debit + fee * qty) / (100 * qty), 2),
            "expiryExitDate": date.fromordinal(c["expiry"].toordinal() - 21).isoformat(),
            "maxEntryAsk": x,
            "scenarios": scenarios,
        })
    picks.sort(key=lambda p: (p["spread"] / max(p["mid"], 0.01), abs(p["greeks"]["delta"] - 0.47)))
    return {"picks": picks[: settings.get("maxSuggestionsPerTicker", 2)], "checked": len(calls), "reasons": reasons}


CAP_FAIL = "over the dollar cap (skipped, not replaced)"
PLAN_DISCLAIMER = ("Plan only — not a trade until it closes above the pivot on volume; "
                   "recheck live price in TradingView")


# Later checks first, so a chain full of weeklies does not hide why the 35-70 DTE names failed.
_PLAN_REASON_PRIORITY = (
    CAP_FAIL,
    ASK_ABOVE_STOCK,
    ASK_FAR_ABOVE_MODEL,
    "scenario gate: under 2x the entry cost at target on day 10",
    "speed check above 1.0",
    "model did not converge",
    "quotes inconsistent across strikes (recheck)",
    "IV more than 3 points above neighbours",
    "IV above 1.3x realized volatility",
    "realized volatility missing",
    "open interest < 500 or volume < 50",
    "bid-ask spread too wide",
    "delta outside 0.35-0.60",
    "IV missing",
    "no two-sided quote",
    "strike not below target",
    "expiry outside 35-70 days",
    "non-standard contract",
)


def plan_reject_reason(reasons: dict, cap: float) -> str:
    """Plain-English why no planned contract passed. Prefer the cap if it blocked any name."""
    if reasons.get(CAP_FAIL):
        cap_s = f"${cap:.0f}" if float(cap) == int(cap) else f"${cap:.2f}"
        return f"No contract fits your {cap_s} limit"
    if not reasons:
        return "No listed call passed every rule"
    why = next((k for k in _PLAN_REASON_PRIORITY if reasons.get(k)), None)
    if why is None:
        why = max(reasons.items(), key=lambda kv: kv[1])[0]
    return why[0].upper() + why[1:] if why else "No listed call passed every rule"


def evaluate_plan(ticker: str, options: list[dict], spot: float, signal: dict, entry: date, rate: float,
                  hv20: float | None, hv60: float | None, settings: dict) -> dict:
    """Like evaluate, but delta and the estimated ask are taken at the pivot when the model allows.

    Today's delayed ask is kept on each pick. The dollar cap is checked against the estimated
    ask at the pivot (what you would expect to pay if the stock is at the breakout).
    Prefers the Balanced choice from evaluate_plan_book so older callers still get one pick.
    """
    book = evaluate_plan_book(ticker, options, spot, signal, entry, rate, hv20, hv60, settings)
    return {"picks": book["picks"], "checked": book["checked"], "reasons": book["reasons"],
            "choices": book["choices"], "spread": book["spread"]}


def evaluate_plan_legacy_unused(ticker: str, options: list[dict], spot: float, signal: dict, entry: date, rate: float,
                  hv20: float | None, hv60: float | None, settings: dict) -> dict:
    """Kept only so the original loop body still parses; not called."""
    pivot = float(signal["pivot"])
    calls = []
    for o in options:
        p = parse_occ(o.get("option", ""))
        if not p or p["type"] != "C":
            continue
        calls.append({"symbol": o["option"], "root": p["root"], "expiry": p["expiry"], "strike": p["strike"],
                      "bid": float(o.get("bid") or 0), "ask": float(o.get("ask") or 0), "iv": float(o.get("iv") or 0),
                      "oi": float(o.get("open_interest") or 0), "volume": float(o.get("volume") or 0),
                      "lastTradeTime": o.get("last_trade_time")})
    bad = _sanity(calls, spot)
    hv = max([v for v in (hv20, hv60) if v] or [0])
    fee = settings["feePerContract"]
    qty = settings["contracts"]
    x = max_entry_ask(settings["capDollars"], fee, qty, settings["startingCash"])
    reasons: dict[str, int] = {}
    picks = []
    by_exp: dict[date, list[dict]] = {}
    for c in calls:
        by_exp.setdefault(c["expiry"], []).append(c)
    for c in calls:
        def fail(why: str) -> None:
            reasons[why] = reasons.get(why, 0) + 1
        dte = (c["expiry"] - entry).days
        if c["root"] != ticker.replace(".", ""):
            fail("non-standard contract"); continue
        if not DTE_MIN <= dte <= DTE_MAX:
            fail("expiry outside 35-70 days"); continue
        if c["strike"] >= signal["target"]:
            fail("strike not below target"); continue
        if c["bid"] <= 0 or c["ask"] < c["bid"]:
            fail("no two-sided quote"); continue
        if c["iv"] <= 0:
            fail("IV missing"); continue
        delta_at = "pivot"
        g = call_greeks(pivot, c["strike"], dte, c["iv"], rate)
        if g is None:
            g = call_greeks(spot, c["strike"], dte, c["iv"], rate)
            delta_at = "current price"
        if g is None or not DELTA_MIN <= g["delta"] <= DELTA_MAX:
            fail("delta outside 0.35-0.60"); continue
        if not spread_ok(c["bid"], c["ask"]):
            fail("bid-ask spread too wide"); continue
        if c["oi"] < MIN_OI or c["volume"] < MIN_VOL:
            fail("open interest < 500 or volume < 50"); continue
        if hv <= 0 or c["iv"] > IV_RATIO_MAX * hv:
            fail("IV above 1.3x realized volatility" if hv > 0 else "realized volatility missing"); continue
        row = sorted(by_exp[c["expiry"]], key=lambda r: r["strike"])
        i = next(k for k, r in enumerate(row) if r["symbol"] == c["symbol"])
        neigh = [r["iv"] for r in (row[i - 1] if i > 0 else None, row[i + 1] if i + 1 < len(row) else None) if r and r["iv"] > 0]
        if not neigh:
            fail("no neighbouring strikes to check skew"); continue
        if c["iv"] - sum(neigh) / len(neigh) > SKEW_POINTS:
            fail("IV more than 3 points above neighbours"); continue
        if c["symbol"] in bad:
            fail("quotes inconsistent across strikes (recheck)"); continue
        d15 = add_trading_days(entry, 15)
        t15 = (d15 - entry).days / 365
        speed = math.log(signal["target"] / pivot) / (c["iv"] * math.sqrt(t15)) if signal["target"] > pivot else 0.0
        if speed > SPEED_MAX:
            fail("speed check above 1.0"); continue
        d10 = add_trading_days(entry, 10)
        model10 = american_call(signal["target"], c["strike"], (c["expiry"] - d10).days, c["iv"], rate)
        now_model = american_call(spot, c["strike"], dte, c["iv"], rate)
        pivot_model = american_call(pivot, c["strike"], dte, c["iv"], rate)
        if not (model10["converged"] and now_model["converged"] and pivot_model["converged"]):
            fail("model did not converge"); continue
        spread = c["ask"] - c["bid"]
        est_ask = round(max(pivot_model["value"] + (c["ask"] - now_model["value"]), max(0.0, pivot - c["strike"]), 0.01), 2)
        debit = est_ask * 100 * qty + fee * qty
        gate_value = (model10["value"] - spread) * 100 * qty - fee * qty
        if gate_value < GATE_MULT * debit:
            fail("scenario gate: under 2x the entry cost at target on day 10"); continue
        if x is None or est_ask > x:
            fail(CAP_FAIL); continue
        scenarios = []
        for label, s in (("At stop", signal["stop"]), ("Unchanged", pivot), ("At target", signal["target"])):
            for tdays in (0, 5, 15):
                day = add_trading_days(entry, tdays) if tdays else entry
                for dv in (-0.05, 0.0, 0.05):
                    v = american_call(s, c["strike"], (c["expiry"] - day).days, max(c["iv"] + dv, 0.01), rate)["value"]
                    pnl = v * 100 * qty - fee * qty - debit
                    scenarios.append({"stock": label, "price": round(s, 2), "tradingDay": tdays, "ivShift": round(dv * 100),
                                      "value": round(v * 100 * qty, 2), "pnl": round(pnl, 2)})
        picks.append({
            "symbol": c["symbol"], "expiry": c["expiry"].isoformat(), "strike": c["strike"], "dte": dte, "type": "CALL",
            "bid": c["bid"], "ask": c["ask"], "estimatedAskAtPivot": est_ask,
            "mid": round((c["bid"] + c["ask"]) / 2, 3), "spread": round(spread, 2),
            "oi": int(c["oi"]), "volume": int(c["volume"]), "iv": round(c["iv"], 4),
            "hv20": round(hv20, 4) if hv20 else None, "hv60": round(hv60, 4) if hv60 else None,
            "ivRatio": round(c["iv"] / hv, 2), "greeks": g, "lastTradeTime": c["lastTradeTime"],
            "deltaEvaluatedAt": delta_at,
            "model": {"name": "CRR binomial American, 800 steps, no dividends",
                      "valueNow": round(now_model["value"], 3),
                      "valueAtPivot": round(pivot_model["value"], 3),
                      "converged": True},
            "speed": round(speed, 2),
            "gate": {"valueAtTargetDay10": round(gate_value, 2), "needed": round(GATE_MULT * debit, 2)},
            "entryCost": round(debit, 2),
            "qty": qty,
            "maxLoss": round(est_ask * 100 * qty + 2 * fee * qty, 2),
            "maxProfit": "No fixed limit (stock could keep rising)",
            "breakevenAtExpiry": round(c["strike"] + est_ask + 2 * fee / 100, 2),
            "valueAtExpiryAtTarget": round(max(0.0, signal["target"] - c["strike"]) * 100 * qty, 2),
            "optionProfitTargetBid": round((TARGET_MULT * debit + fee * qty) / (100 * qty), 2),
            "expiryExitDate": date.fromordinal(c["expiry"].toordinal() - 21).isoformat(),
            "maxEntryAsk": x,
            "scenarios": scenarios,
        })
    picks.sort(key=lambda p: (p["spread"] / max(p["mid"], 0.01), abs(p["greeks"]["delta"] - 0.47)))
    return {"picks": picks[:1], "checked": len(calls), "reasons": reasons}




PLAIN_FAIL = {
    CAP_FAIL: "The estimated ask at the pivot is above the $ cap after fees, so it does not fit the max-loss budget.",
    ASK_ABOVE_STOCK: "The delayed ask is higher than the stock price, which is not a usable call quote.",
    ASK_FAR_ABOVE_MODEL: "The delayed ask is far above the model value, so the quote looks unreliable.",
    "scenario gate: under 2x the entry cost at target on day 10": "At the target price on the 10th trading day, the model value is under 2× what you would pay to get in.",
    "speed check above 1.0": "The move from the pivot to the target is too large relative to this option’s implied volatility in 15 trading days.",
    "model did not converge": "The pricing model did not agree closely enough between 400 and 800 steps.",
    "quotes inconsistent across strikes (recheck)": "The delayed quotes for this expiry are out of order across strikes, so they are not safe to use.",
    "IV more than 3 points above neighbours": "This strike’s implied volatility sits more than 3 points above its neighbours (a skew check).",
    "IV above 1.3x realized volatility": "Implied volatility is more than 1.3× recent realized volatility.",
    "realized volatility missing": "There is not enough price history to compare implied volatility with realized volatility.",
    "open interest < 500 or volume < 50": "Open interest is under 500 or today’s volume is under 50, so the contract is not liquid enough.",
    "bid-ask spread too wide": "The delayed bid and ask are too far apart (more than 12% of the mid, or 5 cents).",
    "delta outside 0.35-0.60": "Delta at the planned entry is outside 0.35–0.60, so this strike is not in the range we allow.",
    "IV missing": "The quote has no implied volatility.",
    "no two-sided quote": "There is no usable two-sided delayed bid and ask.",
    "strike not below target": "The strike is at or above the planned stock target.",
    "expiry outside 35-70 days": "The expiry is outside the 35–70 day window.",
    "non-standard contract": "The option symbol is not a standard OCC call on this stock.",
    "no neighbouring strikes to check skew": "There are no neighbouring strikes on this expiry to check skew.",
    "earnings date unknown": "The next earnings date is unknown, so this cannot be a trade.",
}


def plain_fail(why: str, cap: float | None = None) -> str:
    if why == CAP_FAIL and cap is not None:
        cap_s = f"${cap:.0f}" if float(cap) == int(cap) else f"${cap:.2f}"
        return f"The estimated ask at the pivot is above the {cap_s} cap after fees, so it does not fit the max-loss budget."
    return PLAIN_FAIL.get(why, why[0].upper() + why[1:] if why else "This contract did not pass every rule.")


def _est_px(model_at_pivot: float, model_now: float, quote: float, pivot: float, strike: float, floor_intrinsic: bool = True) -> float:
    raw = model_at_pivot + (quote - model_now)
    intrinsic = max(0.0, pivot - strike) if floor_intrinsic else 0.0
    return round(max(raw, intrinsic, 0.01), 2)


def _greeks_plain(g: dict | None) -> dict:
    if not g:
        return {}
    gamma = g.get("gamma") or 0
    return {
        "delta": f"If the stock rises $1, this option gains about ${g.get('deltaDollarsPer1', 0):.2f}.",
        "gamma": (f"Delta grows about {gamma} per $1 move "
                  f"(about ${round(gamma * 100, 2):.2f} more gain on the next dollar)."),
        "theta": f"Loses about ${abs(g.get('thetaDollarsPerDay') or 0):.2f} per day just from time passing.",
        "vega": f"Gains or loses about ${abs(g.get('vegaDollarsPerPoint') or 0):.2f} if expected volatility moves 1 point.",
    }


def _how_far(pivot: float, breakeven: float, expiry: str) -> str:
    need = round(breakeven - pivot, 2)
    pct = abs(need) / pivot * 100 if pivot else 0
    if need > 0:
        return (f"Needs the stock to rise ${need:.2f} ({pct:.1f}%) from the pivot "
                f"by {expiry} just to break even.")
    if need < 0:
        return (f"Breaks even if the stock stays above ${breakeven:.2f} "
                f"(${abs(need):.2f} / {pct:.1f}% below the pivot) by {expiry}.")
    return f"Breaks even if the stock is at the pivot at expiry ({expiry})."


def _exit_plan(signal: dict, levels: dict | None, contract: dict) -> str:
    stop = signal.get("stop")
    pivot = signal.get("pivot")
    target = signal.get("target")
    time_stop = (levels or {}).get("timeStopDate") or "the 15th trading day"
    expiry_exit = contract.get("expiryExitDate") or "21 days before expiry"
    target_bid = contract.get("optionProfitTargetBid")
    bits = [
        f"Sell the whole position (the Floor does not scale out) if the option bid reaches ${target_bid:.2f} (about 3× the planned debit)."
        if target_bid else "Sell the whole position if the option reaches about 3× the planned debit.",
        f"Exit if the stock closes back below the pivot ${pivot:.2f} in the first 3 sessions.",
        f"Exit if the stock closes below ${stop:.2f} (planned stop: the base low until a breakout-day low prints).",
        f"Exit if the stock closes at or above ${target:.2f} (stock target) or closes below the 20-day average.",
        f"Time exit on {time_stop} (15 trading days) or on {expiry_exit} (21 days before expiry), before theta usually speeds up.",
        "Ex-dividend dates are not checked.",
    ]
    return " ".join(bits)


def _what_if(pivot: float, strike: float, dte: int, iv: float, rate: float, debit: float,
             qty: int, fee: float, move: float, expiry) -> list[dict]:
    rows = []
    from datetime import timedelta
    for label, days_used in (("About 1 week", 7), ("About 3 weeks", 21)):
        years = max(dte - days_used, 1) / 365
        for name, px in ((f"Pivot − ${move:.2f}", pivot - move), ("Flat at the pivot", pivot),
                         (f"Pivot + ${move:.2f}", pivot + move)):
            val = bs_call(max(px, 0.01), strike, years, max(iv, 0.01), rate)
            pnl = val * 100 * qty - fee * qty - debit
            rows.append({
                "when": label,
                "stock": name,
                "price": round(px, 2),
                "value": round(val * 100 * qty, 2),
                "pnl": round(pnl, 2),
                "estimate": True,
            })
    return rows


def _why(role: str, c: dict, cap: float) -> str:
    d = (c.get("greeks") or {}).get("delta")
    dte = c.get("dte")
    strike = c.get("strike")
    oi = c.get("oi")
    vol = c.get("volume")
    est = c.get("estimatedAskAtPivot")
    parts = []
    if role == "Safer":
        parts.append(f"Safer: this strike (${strike}) is closer to in-the-money (delta {d}), so it should move more like the stock and needs a smaller rise to help you.")
    elif role == "Cheaper":
        parts.append(f"Cheaper: this lower-delta call (delta {d}, strike ${strike}) costs less but needs a bigger rise in the stock to pay off.")
    else:
        parts.append(f"Balanced: delta {d} is near the middle of the 0.35–0.60 range we allow (target 0.47), at strike ${strike}.")
    if dte:
        parts.append(f"Expiry is {c.get('expiry')} ({dte} days), inside the 35–70 day window so there is time for a breakout without going too far out.")
    if est is not None:
        parts.append(f"The estimated ask at the pivot is ${est:.2f}; with fees that must fit the ${cap:.0f} max-loss cap.")
    if oi is not None and vol is not None:
        parts.append(f"Liquidity: open interest {oi} and volume {vol} (need at least 500 and 50).")
    return " ".join(parts)


def _finish_candidate(base: dict, signal: dict, levels: dict | None, cap: float, role: str | None = None) -> dict:
    out = dict(base)
    if role:
        out["role"] = role
    out["failedRulePlain"] = plain_fail(out["failedRule"], cap) if out.get("failedRule") else None
    out["whyThisContract"] = _why(role or out.get("role") or "Balanced", out, cap)
    out["greeksPlain"] = _greeks_plain(out.get("greeks"))
    if out.get("breakevenAtExpiry") and signal.get("pivot") and out.get("expiry"):
        out["howFar"] = _how_far(signal["pivot"], out["breakevenAtExpiry"], out["expiry"])
    out["exitPlan"] = _exit_plan(signal, levels, out)
    out["maxProfit"] = "No fixed limit (a long call can keep rising with the stock)"
    return out


STAGE = {
    "non-standard contract": 1,
    "expiry outside 35-70 days": 2,
    "strike not below target": 3,
    "no two-sided quote": 4,
    "IV missing": 5,
    "delta outside 0.35-0.60": 6,
    "bid-ask spread too wide": 7,
    "open interest < 500 or volume < 50": 8,
    "IV above 1.3x realized volatility": 9,
    "realized volatility missing": 9,
    "no neighbouring strikes to check skew": 10,
    "IV more than 3 points above neighbours": 10,
    "quotes inconsistent across strikes (recheck)": 11,
    "speed check above 1.0": 12,
    "model did not converge": 13,
    "scenario gate: under 2x the entry cost at target on day 10": 14,
    CAP_FAIL: 15,
    ASK_ABOVE_STOCK: 4,
    ASK_FAR_ABOVE_MODEL: 4,
}


def evaluate_plan_book(ticker: str, options: list[dict], spot: float, signal: dict, entry: date, rate: float,
                       hv20: float | None, hv60: float | None, settings: dict,
                       move: float | None = None, levels: dict | None = None) -> dict:
    """Safer / Balanced / Cheaper planned calls plus a debit-spread alternative."""
    pivot = float(signal["pivot"])
    move = float(move) if move else round(max(pivot * 0.05, 0.01), 2)
    if levels is None:
        levels = {"timeStopDate": add_trading_days(entry, 15).isoformat()}
    calls = []
    for o in options:
        p = parse_occ(o.get("option", ""))
        if not p or p["type"] != "C":
            continue
        calls.append({"symbol": o["option"], "root": p["root"], "expiry": p["expiry"], "strike": p["strike"],
                      "bid": float(o.get("bid") or 0), "ask": float(o.get("ask") or 0), "iv": float(o.get("iv") or 0),
                      "oi": float(o.get("open_interest") or 0), "volume": float(o.get("volume") or 0),
                      "lastTradeTime": o.get("last_trade_time")})
    bad = _sanity(calls, spot)
    hv = max([v for v in (hv20, hv60) if v] or [0])
    fee = settings["feePerContract"]
    qty = settings["contracts"]
    cap = settings["capDollars"]
    x = max_entry_ask(cap, fee, qty, settings["startingCash"])
    reasons: dict[str, int] = {}
    candidates = []
    by_exp: dict[date, list[dict]] = {}
    for c in calls:
        by_exp.setdefault(c["expiry"], []).append(c)

    def consider(c, why: str | None, extra: dict) -> None:
        if why:
            reasons[why] = reasons.get(why, 0) + 1
        dte = extra.get("dte", (c["expiry"] - entry).days)
        g = extra.get("greeks")
        est_ask = extra.get("estimatedAskAtPivot")
        ask_for_money = est_ask if est_ask is not None else (c["ask"] or None)
        debit = extra.get("entryCost")
        if debit is None and ask_for_money is not None:
            debit = ask_for_money * 100 * qty + fee * qty
        rec = {
            "symbol": c["symbol"], "expiry": c["expiry"].isoformat(), "strike": c["strike"], "dte": dte,
            "type": "CALL", "bid": c["bid"], "ask": c["ask"],
            "estimatedAskAtPivot": est_ask, "mid": round((c["bid"] + c["ask"]) / 2, 3) if c["ask"] else None,
            "spread": round(c["ask"] - c["bid"], 2) if c["ask"] else None,
            "oi": int(c["oi"]), "volume": int(c["volume"]), "iv": round(c["iv"], 4) if c["iv"] else None,
            "hv20": round(hv20, 4) if hv20 else None, "hv60": round(hv60, 4) if hv60 else None,
            "ivRatio": round(c["iv"] / hv, 2) if hv and c["iv"] else None,
            "greeks": g, "lastTradeTime": c["lastTradeTime"],
            "deltaEvaluatedAt": extra.get("deltaEvaluatedAt"),
            "model": extra.get("model"), "speed": extra.get("speed"), "gate": extra.get("gate"),
            "entryCost": round(debit, 2) if debit is not None else None,
            "qty": qty,
            "maxLoss": round(ask_for_money * 100 * qty + 2 * fee * qty, 2) if ask_for_money is not None else None,
            "breakevenAtExpiry": (round(c["strike"] + ask_for_money + 2 * fee / 100, 2)
                                  if ask_for_money is not None else None),
            "optionProfitTargetBid": (round((TARGET_MULT * debit + fee * qty) / (100 * qty), 2)
                                      if debit is not None else None),
            "expiryExitDate": date.fromordinal(c["expiry"].toordinal() - 21).isoformat(),
            "maxEntryAsk": x,
            "passes": why is None,
            "failedRule": why,
            "stage": 16 if why is None else STAGE.get(why, 0),
            "whatIfMove": move,
            "whatIf": extra.get("whatIf") or [],
        }
        if debit is not None and g is not None and rec["dte"] and c["iv"]:
            rec["whatIf"] = _what_if(pivot, c["strike"], rec["dte"], c["iv"], rate, debit, qty, fee, move, c["expiry"])
        candidates.append(rec)

    for c in calls:
        dte = (c["expiry"] - entry).days
        extra = {"dte": dte}
        if c["root"] != ticker.replace(".", ""):
            consider(c, "non-standard contract", extra); continue
        if not DTE_MIN <= dte <= DTE_MAX:
            consider(c, "expiry outside 35-70 days", extra); continue
        if c["strike"] >= signal["target"]:
            consider(c, "strike not below target", extra); continue
        if c["bid"] <= 0 or c["ask"] < c["bid"]:
            consider(c, "no two-sided quote", extra); continue
        insane = quote_sane(c["ask"], spot)
        if insane:
            consider(c, insane, extra); continue
        loss_now = c["ask"] * 100 * qty + 2 * fee * qty
        if x is None or not fits_dollar_cap(loss_now, cap):
            extra["estimatedAskAtPivot"] = None
            consider(c, CAP_FAIL, extra); continue
        if c["iv"] <= 0:
            consider(c, "IV missing", extra); continue
        delta_at = "pivot"
        g = call_greeks(pivot, c["strike"], dte, c["iv"], rate)
        if g is None:
            g = call_greeks(spot, c["strike"], dte, c["iv"], rate)
            delta_at = "current price"
        extra["greeks"] = g
        extra["deltaEvaluatedAt"] = delta_at
        if g is None or not DELTA_MIN <= g["delta"] <= DELTA_MAX:
            consider(c, "delta outside 0.35-0.60", extra); continue
        if not spread_ok(c["bid"], c["ask"]):
            consider(c, "bid-ask spread too wide", extra); continue
        if c["oi"] < MIN_OI or c["volume"] < MIN_VOL:
            consider(c, "open interest < 500 or volume < 50", extra); continue
        if hv <= 0 or c["iv"] > IV_RATIO_MAX * hv:
            consider(c, "IV above 1.3x realized volatility" if hv > 0 else "realized volatility missing", extra); continue
        row = sorted(by_exp[c["expiry"]], key=lambda r: r["strike"])
        i = next(k for k, r in enumerate(row) if r["symbol"] == c["symbol"])
        neigh = [r["iv"] for r in (row[i - 1] if i > 0 else None, row[i + 1] if i + 1 < len(row) else None) if r and r["iv"] > 0]
        if not neigh:
            consider(c, "no neighbouring strikes to check skew", extra); continue
        if c["iv"] - sum(neigh) / len(neigh) > SKEW_POINTS:
            consider(c, "IV more than 3 points above neighbours", extra); continue
        if c["symbol"] in bad:
            consider(c, "quotes inconsistent across strikes (recheck)", extra); continue
        d15 = add_trading_days(entry, 15)
        t15 = (d15 - entry).days / 365
        speed = math.log(signal["target"] / pivot) / (c["iv"] * math.sqrt(t15)) if signal["target"] > pivot else 0.0
        extra["speed"] = round(speed, 2)
        if speed > SPEED_MAX:
            consider(c, "speed check above 1.0", extra); continue
        d10 = add_trading_days(entry, 10)
        model10 = american_call(signal["target"], c["strike"], (c["expiry"] - d10).days, c["iv"], rate)
        now_model = american_call(spot, c["strike"], dte, c["iv"], rate)
        pivot_model = american_call(pivot, c["strike"], dte, c["iv"], rate)
        extra["model"] = {"name": "CRR binomial American, 800 steps, no dividends",
                          "valueNow": round(now_model["value"], 3),
                          "valueAtPivot": round(pivot_model["value"], 3),
                          "converged": bool(model10["converged"] and now_model["converged"] and pivot_model["converged"])}
        if not extra["model"]["converged"]:
            consider(c, "model did not converge", extra); continue
        spread = c["ask"] - c["bid"]
        insane_now = quote_sane(c["ask"], spot, now_model["value"])
        if insane_now:
            consider(c, insane_now, extra); continue
        est_ask = _est_px(pivot_model["value"], now_model["value"], c["ask"], pivot, c["strike"])
        extra["estimatedAskAtPivot"] = est_ask
        insane_est = quote_sane(est_ask, pivot, pivot_model["value"])
        if insane_est:
            consider(c, insane_est, extra); continue
        debit = est_ask * 100 * qty + fee * qty
        extra["entryCost"] = debit
        gate_value = (model10["value"] - spread) * 100 * qty - fee * qty
        extra["gate"] = {"valueAtTargetDay10": round(gate_value, 2), "needed": round(GATE_MULT * debit, 2)}
        extra["whatIf"] = _what_if(pivot, c["strike"], dte, c["iv"], rate, debit, qty, fee, move, c["expiry"])
        if gate_value < GATE_MULT * debit:
            consider(c, "scenario gate: under 2x the entry cost at target on day 10", extra); continue
        if x is None or est_ask > x:
            consider(c, CAP_FAIL, extra); continue
        consider(c, None, extra)

    def delta_of(rec):
        return ((rec.get("greeks") or {}).get("delta"))

    scored = [c for c in candidates if delta_of(c) is not None and DTE_MIN <= (c.get("dte") or 0) <= DTE_MAX]
    passing = [c for c in scored if c["passes"] and fits_dollar_cap(c.get("maxLoss"), cap)]
    # Never present an over-cap or failed quote as Safer/Balanced/Cheaper.
    pool = passing

    def take(role, rec):
        return _finish_candidate(rec, signal, levels, cap, role)

    choices = []
    used = set()
    if pool:
        balanced = min(pool, key=lambda c: abs(delta_of(c) - 0.47))
        choices.append(take("Balanced", balanced))
        used.add(balanced["symbol"])
        safer_pool = [c for c in pool if c["symbol"] not in used]
        safer_pref = [c for c in safer_pool if delta_of(c) >= delta_of(balanced)]
        if safer_pref or safer_pool:
            safer = max(safer_pref or safer_pool, key=delta_of)
            choices.append(take("Safer", safer))
            used.add(safer["symbol"])
        cheaper_pool = [c for c in pool if c["symbol"] not in used]
        cheaper_pref = [c for c in cheaper_pool if delta_of(c) <= delta_of(balanced)]
        if cheaper_pref or cheaper_pool:
            cheaper = min(cheaper_pref or cheaper_pool, key=delta_of)
            choices.append(take("Cheaper", cheaper))
            used.add(cheaper["symbol"])
        # Keep Safer, Balanced, Cheaper display order
        order = {"Safer": 0, "Balanced": 1, "Cheaper": 2}
        choices.sort(key=lambda c: order.get(c["role"], 9))

    # Debit spread: buy the balanced call, sell a higher strike in the same expiry.
    spread_alt = _debit_spread(ticker, choices, calls, by_exp, spot, pivot, signal, entry, rate, fee, qty, cap,
                               settings, move, levels, x)
    passing_picks = [c for c in choices if c["passes"]]
    # Keep backward-compatible single pick (prefer Balanced if it passed).
    picks = [c for c in choices if c["role"] == "Balanced" and c["passes"]] or passing_picks[:1]
    return {
        "picks": picks[:1],
        "choices": choices,
        "spread": spread_alt,
        "reasons": reasons,
        "checked": len(calls),
        "whatIfMove": move,
    }


def _debit_spread(ticker, choices, calls, by_exp, spot, pivot, signal, entry, rate, fee, qty, cap,
                  settings, move, levels, long_x) -> dict:
    balanced = next((c for c in choices if c.get("role") == "Balanced"), None)
    highlight = any((not c.get("passes") and c.get("failedRule") == CAP_FAIL) for c in choices)
    empty = {
        "type": "CALL_DEBIT_SPREAD",
        "passes": False,
        "highlight": highlight,
        "failedRule": "no balanced call to build a spread from",
        "failedRulePlain": "There is no balanced call to pair with a higher strike.",
        "whyThisContract": "A call debit spread buys the balanced call and sells a higher strike in the same expiry to cut the debit and cap the gain.",
        "maxProfit": None,
    }
    if not balanced:
        return empty
    expiry = date.fromisoformat(balanced["expiry"])
    long_k = balanced["strike"]
    longs = [c for c in calls if c["symbol"] == balanced["symbol"]]
    if not longs:
        return empty
    long_c = longs[0]
    dte = (expiry - entry).days
    shorts = [c for c in sorted(by_exp.get(expiry, []), key=lambda r: r["strike"]) if c["strike"] > long_k]
    target = signal.get("target") or (long_k + 5)
    fail = None
    short_c = None
    liquid = []
    for c in shorts:
        if c["bid"] <= 0 or c["ask"] < c["bid"]:
            continue
        if not spread_ok(c["bid"], c["ask"]):
            continue
        if c["oi"] < MIN_OI or c["volume"] < MIN_VOL:
            continue
        if c["iv"] <= 0:
            continue
        liquid.append(c)
    if not liquid:
        fail = "open interest < 500 or volume < 50" if shorts else "no higher strike in this expiry"
        # pick something to describe if possible
        short_c = shorts[0] if shorts else None
        if short_c and short_c["ask"] > 0 and short_c["bid"] > 0 and not spread_ok(short_c["bid"], short_c["ask"]):
            fail = "bid-ask spread too wide"
    else:
        short_c = min(liquid, key=lambda c: abs(c["strike"] - target))

    if not short_c:
        out = dict(empty)
        out["failedRule"] = fail or "no higher strike in this expiry"
        out["failedRulePlain"] = plain_fail(out["failedRule"], cap) if out["failedRule"] in PLAIN_FAIL else (
            "No higher strike in the same expiry is listed to sell against the balanced call.")
        out["long"] = {"symbol": balanced["symbol"], "strike": long_k, "expiry": balanced["expiry"]}
        out["highlight"] = highlight or (balanced.get("estimatedAskAtPivot") or 0) > (long_x or 0)
        return out

    now_l = american_call(spot, long_k, dte, long_c["iv"] or 0.2, rate)
    piv_l = american_call(pivot, long_k, dte, long_c["iv"] or 0.2, rate)
    now_s = american_call(spot, short_c["strike"], dte, short_c["iv"] or 0.2, rate)
    piv_s = american_call(pivot, short_c["strike"], dte, short_c["iv"] or 0.2, rate)
    est_ask_l = balanced.get("estimatedAskAtPivot")
    if est_ask_l is None:
        est_ask_l = _est_px(piv_l["value"], now_l["value"], long_c["ask"], pivot, long_k)
    est_bid_s = _est_px(piv_s["value"], now_s["value"], short_c["bid"], pivot, short_c["strike"])
    net = round(est_ask_l - est_bid_s, 2)
    width = round(short_c["strike"] - long_k, 2)
    legs = 2
    x_spread = max_entry_net_debit(cap, fee, qty, settings["startingCash"], legs)
    fees_round = 2 * fee * qty * legs  # open+close both legs
    debit_dollars = net * 100 * qty
    max_loss = round(debit_dollars + fees_round, 2)
    max_profit = round(width * 100 * qty - debit_dollars - fees_round, 2)
    breakeven = round(long_k + net + fees_round / (100 * qty), 2)
    passes = True
    why = None
    if not balanced.get("passes") and balanced.get("failedRule") not in {CAP_FAIL, None}:
        # long failed a quality rule other than cap: spread inherits it unless we already have a better fail
        if balanced["failedRule"] in {"bid-ask spread too wide", "open interest < 500 or volume < 50",
                                      "no two-sided quote", "IV missing", "quotes inconsistent across strikes (recheck)"}:
            passes, why = False, balanced["failedRule"]
    if fail:
        passes, why = False, fail
    if net <= 0:
        passes, why = False, "spread net debit is zero or a credit"
    if x_spread is None or net > x_spread:
        passes, why = False, CAP_FAIL
    # liquidity already applied to short; long liquidity encoded in balanced
    out = {
        "type": "CALL_DEBIT_SPREAD",
        "passes": passes,
        "failedRule": why,
        "failedRulePlain": (plain_fail(why, cap) if why in PLAIN_FAIL or why == CAP_FAIL
                            else (why[0].upper() + why[1:] if why else None)),
        "highlight": highlight or (balanced.get("estimatedAskAtPivot") or 0) > (long_x or 0),
        "qty": qty,
        "expiry": balanced["expiry"],
        "dte": dte,
        "long": {"symbol": long_c["symbol"], "strike": long_k, "bid": long_c["bid"], "ask": long_c["ask"],
                 "estimatedAskAtPivot": est_ask_l, "oi": int(long_c["oi"]), "volume": int(long_c["volume"])},
        "short": {"symbol": short_c["symbol"], "strike": short_c["strike"], "bid": short_c["bid"], "ask": short_c["ask"],
                  "estimatedBidAtPivot": est_bid_s, "oi": int(short_c["oi"]), "volume": int(short_c["volume"])},
        "netDebit": net,
        "todayNetDebit": round(long_c["ask"] - short_c["bid"], 2),
        "width": width,
        "maxLoss": max_loss,
        "maxProfit": max_profit,
        "breakevenAtExpiry": breakeven,
        "maxEntryNetDebit": x_spread,
        "fees": round(fees_round, 2),
        "howFar": _how_far(pivot, breakeven, balanced["expiry"]),
        "whyThisContract": (f"Call debit spread: buy the {balanced['expiry']} ${long_k:.2f} call and sell the "
                            f"${short_c['strike']:.2f} call (width ${width:.2f}). The sale cuts what you pay "
                            f"(estimated net debit ${net:.2f} at the pivot) and caps the gain at the short strike. "
                            f"Both legs are checked for a two-sided quote, a tight spread, open interest ≥ 500 and volume ≥ 50. "
                            f"The ${cap:.0f} cap uses fees on both legs (open and close)."),
        "exitPlan": (_exit_plan(signal, levels, {"optionProfitTargetBid": None, "expiryExitDate": balanced.get("expiryExitDate")})
                     .replace("Sell the whole position if the option reaches about 3× the planned debit.",
                              "Sell the whole spread (both legs together) if the stock reaches the short strike or the spread’s value is near its max profit.")),
        "whatIfMove": move,
        "whatIf": _spread_what_if(pivot, long_k, short_c["strike"], dte, long_c["iv"], short_c["iv"], rate,
                                  debit_dollars, qty, fee, move),
    }
    if why == "spread net debit is zero or a credit":
        out["failedRulePlain"] = "The estimated short bid is at least the long ask, so this is not a debit spread."
    return out


def _spread_what_if(pivot, long_k, short_k, dte, iv_l, iv_s, rate, debit_dollars, qty, fee, move):
    rows = []
    fees_rt = 4 * fee * qty
    for label, days_used in (("About 1 week", 7), ("About 3 weeks", 21)):
        years = max(dte - days_used, 1) / 365
        for name, px in ((f"Pivot − ${move:.2f}", pivot - move), ("Flat at the pivot", pivot),
                         (f"Pivot + ${move:.2f}", pivot + move)):
            long_v = bs_call(max(px, 0.01), long_k, years, max(iv_l or 0.2, 0.01), rate)
            short_v = bs_call(max(px, 0.01), short_k, years, max(iv_s or 0.2, 0.01), rate)
            val = (long_v - short_v) * 100 * qty
            pnl = val - debit_dollars - fees_rt / 2  # remaining close fees still ahead; show estimate after open fees only? 
            # P&L vs planned entry: value - debit - remaining close fees. Simpler: value - (debit_dollars + fees_rt) + open fees already in... 
            # debit_dollars is premium only. max_loss uses premium + all fees.
            # Estimate P&L = current spread value - premium paid - all round-trip fees (conservative).
            pnl = val - debit_dollars - fees_rt
            rows.append({
                "when": label, "stock": name, "price": round(px, 2),
                "value": round(val, 2), "pnl": round(pnl, 2), "estimate": True,
            })
    return rows
