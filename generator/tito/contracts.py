"""Contract choice, price checks and scenarios for long calls (Page: Contract choice; Scenarios and Greeks)."""
from __future__ import annotations

import math
import re
from datetime import date

from tito.calendar_us import add_trading_days
from tito.money import max_entry_ask
from tito.options_math import american_call, call_greeks

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


def plan_reject_reason(reasons: dict, cap: float) -> str:
    """Plain-English why no planned contract passed. Prefer the cap if it blocked any name."""
    if reasons.get(CAP_FAIL):
        cap_s = f"${cap:.0f}" if float(cap) == int(cap) else f"${cap:.2f}"
        return f"Too expensive for {cap_s} cap"
    if not reasons:
        return "No listed call passed every rule"
    why = max(reasons.items(), key=lambda kv: kv[1])[0]
    return why[0].upper() + why[1:] if why else "No listed call passed every rule"


def evaluate_plan(ticker: str, options: list[dict], spot: float, signal: dict, entry: date, rate: float,
                  hv20: float | None, hv60: float | None, settings: dict) -> dict:
    """Like evaluate, but delta and the estimated ask are taken at the pivot when the model allows.

    Today's delayed ask is kept on each pick. The dollar cap is checked against the estimated
    ask at the pivot (what you would expect to pay if the stock is at the breakout).
    """
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

