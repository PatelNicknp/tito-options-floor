import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from tito import rules
from tito.build import DISCLAIMER, LABEL, classify_gate, earnings_ok
from tito.calendar_us import add_trading_days, is_trading_day, next_trading_day, trading_days_between
from tito.earnings import merge_earnings, nasdaq_scan_should_stop, parse_yahoo_quote_html
from tito.contracts import ASK_ABOVE_STOCK, ASK_FAR_ABOVE_MODEL, evaluate, evaluate_plan, evaluate_plan_book, fits_dollar_cap, parse_occ, plan_reject_reason, quote_sane, spread_ok
from tito.money import max_entry_ask, max_entry_net_debit
from tito.options_math import american_call, bs_call, call_greeks

ROOT = Path(__file__).resolve().parents[1]


# --- X formula -------------------------------------------------------------
@pytest.mark.parametrize("cap,fee,qty,cash,expected", [
    (200, 0.65, 1, 1000, 1.98),    # (200 - 1.30) / 100 = 1.987 -> 1.98
    (200, 0.65, 2, 1000, 0.99),    # (200 - 2.60) / 200 = 0.987 -> 0.98? check below
    (200, 0.0, 1, 1000, 2.00),
    (200, 0.65, 1, 150, 1.48),     # cash lower than cap
    (1, 0.65, 1, 1000, None),      # fees eat the cap
    (200, 0.65, 0, 1000, None),
])
def test_max_entry_ask(cap, fee, qty, cash, expected):
    got = max_entry_ask(cap, fee, qty, cash)
    if qty == 2:
        expected = math.floor((200 - 2.6) / 200 * 100) / 100
    assert got == expected


def test_x_rounds_down_never_up():
    assert max_entry_ask(199.99, 0.65, 1, 1000) == 1.98


# --- calendar ----------------------------------------------------------------
def test_trading_calendar():
    assert not is_trading_day(date(2026, 11, 26))  # Thanksgiving
    assert next_trading_day(date(2026, 11, 6)) == date(2026, 11, 9)
    assert add_trading_days(date(2026, 11, 24), 2) == date(2026, 11, 27)
    assert trading_days_between(date(2026, 11, 6), date(2026, 11, 13)) == 5


# --- earnings exclusion ------------------------------------------------------
def test_earnings_unknown_rejected():
    assert earnings_ok(None, date(2026, 11, 9))[0] is False
    assert earnings_ok("", date(2026, 11, 9))[0] is False
    assert earnings_ok("2026-10-01", date(2026, 11, 9))[0] is False  # past date = unknown next date


def test_earnings_within_15_trading_days_rejected():
    entry = date(2026, 11, 9)
    limit = add_trading_days(entry, 15)
    assert earnings_ok(limit.isoformat(), entry)[0] is False
    assert earnings_ok((limit + timedelta(days=1)).isoformat(), entry)[0] is True


# --- stock rules ----------------------------------------------------------------
def make_bars(n=300, breakout=True):
    bars, price, d = [], 50.0, date(2025, 8, 1)
    for i in range(n - 31):
        price *= 1.004
        bars.append({"date": d.isoformat(), "open": price, "high": price * 1.01, "low": price * 0.99, "close": price, "volume": 1_000_000})
        d += timedelta(days=1)
    top = price
    for i in range(30):  # tight base, drying volume
        c = top * (0.97 + 0.01 * (i % 3)) if i < 20 else top * 0.985
        rng = 0.02 if i < 20 else 0.005
        bars.append({"date": d.isoformat(), "open": c, "high": min(c * (1 + rng), top * 1.01), "low": c * (1 - rng), "close": c, "volume": 700_000})
        d += timedelta(days=1)
    pivot = max(b["high"] for b in bars[-30:])
    close = pivot * 1.02
    bars.append({"date": d.isoformat(), "open": close, "high": close * 1.005, "low": pivot * 0.995, "close": close,
                 "volume": 2_000_000 if breakout else 900_000})
    return bars


def test_breakout_found_and_chase_limit():
    base = rules.find_base(make_bars())
    assert base is not None and rules.BASE_LEN_MIN <= base["length"] <= rules.BASE_LEN_MAX and base["depth"] <= 0.25
    assert base["volRatio"] >= rules.BREAKOUT_VOL_MIN


def test_no_breakout_without_volume():
    assert rules.find_base(make_bars(breakout=False)) is None


def test_breakout_volume_test_bar_is_1_25_was_1_50():
    assert rules.BREAKOUT_VOL_WAS == 1.50
    assert rules.BREAKOUT_VOL_MIN == 1.25
    assert 1.0 < rules.BREAKOUT_VOL_MIN < rules.BREAKOUT_VOL_WAS
    bars = make_bars()
    avg50 = sum(b["volume"] for b in bars[-51:-1]) / 50
    bars[-1] = dict(bars[-1], volume=avg50 * 1.24)
    assert rules.find_base(bars) is None
    assert rules.classify_breakout_fail(bars)["kind"] == "volume"
    bars[-1] = dict(bars[-1], volume=avg50 * 1.26)
    assert rules.find_base(bars) is not None
    assert rules.find_base(bars, min_vol=rules.BREAKOUT_VOL_WAS) is None
    assert rules.classify_breakout_fail(bars)["kind"] == "volume_between"
    mid = make_bars()
    mid[-1] = dict(mid[-1], close=mid[-2]["close"])  # no breakout price
    assert rules.classify_breakout_fail(mid)["kind"] in {"price", "base"}


def test_base_min_length_test_is_5_was_10():
    assert rules.BASE_LEN_MIN_WAS == 10
    assert rules.BASE_LEN_MIN == 5
    assert rules.BASE_LEN_MAX == 40
    assert rules.recent_len(40) == 10 and rules.recent_len(10) == 10
    assert rules.recent_len(9) == 5 and rules.recent_len(5) == 3
    # A 5-session tightening base (deeper history so 10+ session windows fail depth).
    bars, price, d = [], 50.0, date(2025, 8, 1)
    for i in range(280):
        price *= 1.003
        bars.append({"date": d.isoformat(), "open": price, "high": price * 1.01, "low": price * 0.7, "close": price, "volume": 1_000_000})
        d += timedelta(days=1)
    top = price
    for i in range(5):
        c = top * (0.99 + 0.002 * i)
        rng = 0.02 if i < 2 else 0.004
        bars.append({"date": d.isoformat(), "open": c, "high": min(c * (1 + rng), top * 1.005), "low": c * (1 - rng), "close": c, "volume": 700_000})
        d += timedelta(days=1)
    pivot = max(b["high"] for b in bars[-5:])
    close = pivot * 1.02
    bars.append({"date": d.isoformat(), "open": close, "high": close * 1.005, "low": pivot * 0.995, "close": close, "volume": 2_000_000})
    found = rules.find_base(bars)
    assert found is not None and found["length"] == 5
    assert rules.find_base(bars, min_length=10) is None


def test_market_rule():
    up = [100 + i * 0.5 for i in range(80)]
    down = [140 - i * 0.5 for i in range(80)]
    assert rules.market_rule(up)["pass"] is True
    assert rules.market_rule(down)["pass"] is False


# --- options math ------------------------------------------------------------------
def test_binomial_matches_black_scholes_without_dividends():
    v = american_call(100, 100, 45, 0.3, 0.04)
    assert v["converged"]
    assert abs(v["value"] - bs_call(100, 100, 45 / 365, 0.3, 0.04)) < 0.02


def test_greeks_units_in_dollars():
    g = call_greeks(100, 100, 45, 0.3, 0.04)
    assert 0.5 < g["delta"] < 0.6 and g["deltaDollarsPer1"] == round(g["delta"] * 100, 2)
    assert g["thetaDollarsPerDay"] < 0 and g["vegaDollarsPerPoint"] > 0 and g["rhoDollarsPerPoint"] > 0


def test_spread_rule():
    assert spread_ok(1.00, 1.05) and not spread_ok(1.00, 1.30) and spread_ok(0.10, 0.15)


def test_parse_occ():
    p = parse_occ("F261218C00013000")
    assert p["root"] == "F" and p["strike"] == 13.0 and p["expiry"] == date(2026, 12, 18) and p["type"] == "C"


def chain_fixture(ask=0.31):
    rows = []
    for k, iv in ((12, 0.40), (12.5, 0.39), (13, 0.38), (13.5, 0.38), (14, 0.39)):
        sym = f"F261218C{int(k * 1000):08d}"
        bid = ask - 0.02 if k == 13 else 0.2
        rows.append({"option": sym, "bid": bid if k == 13 else max(0.01, 12.44 - k + 0.3), "ask": ask if k == 13 else max(0.03, 12.44 - k + 0.34),
                     "iv": iv, "open_interest": 5000, "volume": 500, "last_trade_time": "2026-11-06T15:59:00"})
    return rows


SETTINGS = {"capDollars": 200, "feePerContract": 0.65, "startingCash": 1000, "contracts": 1, "maxSuggestionsPerTicker": 2}


def test_contract_rejections_are_counted_and_cap_skips():
    signal = {"target": 13.44, "stop": 12.0}
    res = evaluate("F", chain_fixture(ask=2.5), 12.44, signal, date(2026, 11, 9), 0.04, 0.30, 0.32, SETTINGS)
    assert all(p["ask"] <= 1.98 for p in res["picks"])
    assert sum(res["reasons"].values()) + len(res["picks"]) <= res["checked"]


def test_non_standard_contract_rejected():
    rows = [dict(r, option=r["option"].replace("F26", "F126")) for r in chain_fixture()]
    res = evaluate("F", rows, 12.44, {"target": 13.44, "stop": 12}, date(2026, 11, 9), 0.04, 0.3, 0.3, SETTINGS)
    assert res["picks"] == [] and res["reasons"].get("non-standard contract")


# --- output safety -----------------------------------------------------------------
def test_labels():
    assert "DELAYED" in LABEL and "research only" in LABEL
    assert "never places" in DISCLAIMER and "TradingView" in DISCLAIMER


def test_generator_has_no_fill_path_and_no_proxy():
    code = "\n".join(p.read_text() for p in (ROOT / "generator").rglob("*.py"))
    assert "r.jina.ai" not in code and "jina" not in code
    assert not re.search(r"def\s+\w*(fill|open_trade|close_trade|place)\w*\(", code)


def test_published_file_has_no_fills_and_is_labelled():
    path = ROOT / "docs" / "data" / "latest.json"
    if not path.exists():
        pytest.skip("generator not run yet")
    text = path.read_text()
    data = json.loads(text)
    assert data["label"].startswith("DELAYED")
    for key in ('"fills"', '"trades"', '"positions"', '"ledger"', '"fillType"'):
        assert key not in text
    for idea in data["suggestions"] + data.get("marketAgainst", []) + data["blocked"] + data.get("settingUp", []):
        if idea.get("quoteSource"):
            assert idea["quoteSource"]["quoteTime"] == "quote time not provided"
            assert idea["quoteSource"]["retrievedAtPT"].endswith("PT")
    kind = classify_gate(data["gate"]["checks"])
    if kind == "pass":
        assert data["gate"]["pass"] is True
        assert data.get("marketAgainst", []) == []
    elif kind == "market_against":
        assert data["suggestions"] == []
        assert data["noTradeToday"] is (not data.get("marketAgainst"))
        for idea in data.get("marketAgainst") or []:
            assert idea.get("tag") == "Market against you"
            assert idea.get("marketNote") == "The market is weak, so breakouts fail more often."
    else:
        assert data["suggestions"] == [] and data.get("marketAgainst", []) == []
    cfg = json.loads((ROOT / "config.json").read_text())
    assert cfg["capDollars"] == 200


def test_no_breakout_when_close_stays_in_base():
    bars = make_bars()
    bars[-1] = dict(bars[-1], close=bars[-2]["close"] * 0.99)
    assert rules.find_base(bars) is None


def test_classify_gate_market_against_only():
    market = {"name": "Market: SPY above a rising 50-day average", "pass": False}
    mok = {"name": "US Stock Box market_ok", "pass": False}
    session = {"name": "US Stock Box prices are from the last completed session", "pass": True}
    rate = {"name": "Interest rate for the model", "pass": True}
    assert classify_gate([{"name": market["name"], "pass": True}, {"name": mok["name"], "pass": True}, session, rate]) == "pass"
    assert classify_gate([market, mok, session, rate]) == "market_against"
    assert classify_gate([market, {"name": mok["name"], "pass": True}, session, rate]) == "market_against"
    assert classify_gate([{"name": market["name"], "pass": True}, mok, session, rate]) == "market_against"
    assert classify_gate([market, mok, {**session, "pass": False}, rate]) == "blocked"
    assert classify_gate([market, mok, session, {**rate, "pass": False}]) == "blocked"


def test_find_setup_waiting_and_light_volume():
    waiting = make_bars()
    waiting[-1] = dict(waiting[-1], close=waiting[-2]["close"])
    setup = rules.find_setup(waiting)
    assert setup is not None and setup["status"] == "waiting"
    assert setup["close"] <= setup["pivot"]
    assert setup["volumeNeeded"] == int(__import__("math").ceil(rules.BREAKOUT_VOL_MIN * setup["avg50Volume"] - 1e-9))
    light = make_bars(breakout=False)
    setup = rules.find_setup(light)
    assert setup is not None and setup["status"] == "light_volume"
    assert setup["close"] > setup["pivot"]
    assert setup["volRatio"] < rules.BREAKOUT_VOL_MIN
    assert rules.find_setup(make_bars()) is None  # completed breakout is not a setup


def test_setting_up_list_has_no_contracts_or_fills():
    data = json.loads((ROOT / "docs" / "data" / "latest.json").read_text()) if (ROOT / "docs" / "data" / "latest.json").exists() else {}
    for row in data.get("settingUp") or []:
        assert "contracts" not in row and "fill" not in row
        assert row["note"].startswith("Not a trade yet. Set a TradingView alert at $")
        assert row["volumeNeeded"] > 0
    rows = [
        {"ticker": "A", "pctBelowPivot": 2.0},
        {"ticker": "B", "pctBelowPivot": -0.3},
        {"ticker": "C", "pctBelowPivot": 0.5},
    ]
    rows.sort(key=lambda r: abs(r["pctBelowPivot"]))
    assert [r["ticker"] for r in rows] == ["B", "C", "A"]


def plan_chain():
    quotes = {
        12: (0.88, 0.94, 0.40), 12.5: (0.62, 0.67, 0.39), 13: (0.35, 0.38, 0.38),
        13.5: (0.22, 0.26, 0.38), 14: (0.14, 0.18, 0.39),
    }
    rows = []
    for k, (bid, ask, iv) in quotes.items():
        rows.append({"option": f"F261218C{int(k * 1000):08d}", "bid": bid, "ask": ask, "iv": iv,
                     "open_interest": 5000, "volume": 500, "last_trade_time": "2026-11-06T15:59:00"})
    return rows


def test_evaluate_plan_uses_pivot_delta_and_cap_reason():
    signal = {"target": 14.00, "stop": 11.60, "pivot": 12.80}
    res = evaluate_plan("F", plan_chain(), 12.44, signal, date(2026, 11, 9), 0.04, 0.30, 0.32, SETTINGS)
    assert res["picks"]
    pick = res["picks"][0]
    assert pick["symbol"] == "F261218C00013000"
    assert pick["deltaEvaluatedAt"] == "pivot"
    assert pick["ask"] == 0.38
    assert pick["estimatedAskAtPivot"] == 0.54
    assert pick["qty"] == 1
    assert pick["estimatedAskAtPivot"] <= pick["maxEntryAsk"]
    assert 0.35 <= pick["greeks"]["delta"] <= 0.60
    tight = {**SETTINGS, "capDollars": 30}
    expensive = evaluate_plan("F", plan_chain(), 12.44, signal, date(2026, 11, 9), 0.04, 0.30, 0.32, tight)
    assert expensive["picks"] == []
    assert plan_reject_reason(expensive["reasons"], 30) == "No contract fits your $30 limit"
    assert plan_reject_reason({"over the dollar cap (skipped, not replaced)": 1}, 200) == "No contract fits your $200 limit"
    assert plan_reject_reason({}, 200) == "No listed call passed every rule"
    assert plan_reject_reason({
        "expiry outside 35-70 days": 500,
        "delta outside 0.35-0.60": 10,
        "bid-ask spread too wide": 2,
    }, 200) == "Bid-ask spread too wide"


def test_merge_earnings_uses_earlier_when_sources_disagree_and_keeps_estimates():
    asof = date(2026, 11, 10)
    m = merge_earnings([
        {"source": "US Stock Box earnings_calendar.csv", "date": date(2026, 12, 20), "estimated": False},
        {"source": "Yahoo Finance calendarEvents", "date": date(2026, 12, 8), "estimated": True},
    ], asof)
    assert m["date"] == date(2026, 12, 8)
    assert m["estimated"] is True
    assert m["usedEarlierDueToDisagreement"] is True
    assert "Yahoo" in m["source"]
    past = merge_earnings([{"source": "Yahoo Finance calendarEvents", "date": date(2026, 11, 5), "estimated": True}], asof)
    assert past["date"] is None
    unknown = merge_earnings([], asof)
    assert unknown["date"] is None


def test_max_entry_net_debit_counts_fees_on_both_legs():
    assert max_entry_net_debit(200, 0.65, 1, 1000, 1) == 1.98
    assert max_entry_net_debit(200, 0.65, 1, 1000, 2) == 1.97


def test_evaluate_plan_book_roles_whatif_and_spread():
    signal = {"target": 14.00, "stop": 11.60, "pivot": 12.80}
    book = evaluate_plan_book("F", plan_chain(), 12.44, signal, date(2026, 11, 9), 0.04, 0.30, 0.32, SETTINGS, move=0.64)
    roles = [c["role"] for c in book["choices"]]
    assert "Balanced" in roles
    bal = next(c for c in book["choices"] if c["role"] == "Balanced")
    assert "Why" not in bal["whyThisContract"] or "Balanced" in bal["whyThisContract"]
    assert bal["whyThisContract"]
    assert bal["greeksPlain"]["delta"].startswith("If the stock rises $1")
    assert "from the pivot" in bal["howFar"]
    assert any(r["when"] == "About 1 week" and r.get("estimate") for r in bal["whatIf"])
    assert any(r["when"] == "About 3 weeks" for r in bal["whatIf"])
    assert "no fixed limit" in bal["maxProfit"].lower()
    assert book["spread"]["type"] == "CALL_DEBIT_SPREAD"
    assert book["spread"]["long"]["strike"] == bal["strike"]
    assert book["spread"]["short"]["strike"] > bal["strike"]
    tight = {**SETTINGS, "capDollars": 30}
    book2 = evaluate_plan_book("F", plan_chain(), 12.44, signal, date(2026, 11, 9), 0.04, 0.30, 0.32, tight, move=0.64)
    assert book2["picks"] == []
    assert book2["choices"] == []
    assert book2["reasons"].get("over the dollar cap (skipped, not replaced)")
    assert plan_reject_reason(book2["reasons"], 30) == "No contract fits your $30 limit"

def test_parse_yahoo_quote_html_reads_escaped_and_raw_json():
    escaped = (
        'prefix "earningsDate":[{"raw":1796304600,"fmt":"2026-12-03"}],'
        '"isEarningsDateEstimate":true extra'
    )
    # The live Yahoo page escapes the JSON quotes. Both forms must parse.
    escaped_page = r'prefix \"earningsDate\":[{\"raw\":1796304600,\"fmt\":\"2026-12-03\"}],\"isEarningsDateEstimate\":true'
    got = parse_yahoo_quote_html(escaped_page)
    assert got is not None
    assert got["date"] == date(2026, 12, 3)
    assert got["estimated"] is True
    assert got["source"] == "Yahoo Finance quote page"
    raw = '{"earningsDate":[{"raw":1796304600,"fmt":"2026-12-03"}],"isEarningsDateEstimate":false}'
    got = parse_yahoo_quote_html(raw)
    assert got["date"] == date(2026, 12, 3)
    assert got["estimated"] is False
    assert parse_yahoo_quote_html("no earnings here") is None
    past = parse_yahoo_quote_html('{"earningsDate":[{"fmt":"2026-10-29"}],"isEarningsDateEstimate":false}')
    assert past["date"] == date(2026, 10, 29)
    # merge still drops a past Yahoo date, so it cannot become a next date
    merged = merge_earnings([past], date(2026, 11, 11))
    assert merged["date"] is None


def test_nasdaq_scan_stops_after_repeated_failures():
    assert nasdaq_scan_should_stop(8) is True
    assert nasdaq_scan_should_stop(7) is False


def test_quote_sane_rejects_ask_above_stock_or_far_above_model():
    assert quote_sane(20.00, 19.79) == ASK_ABOVE_STOCK
    assert quote_sane(19.79, 19.79) is None
    assert quote_sane(0.40, 19.79, 0.35) is None
    assert quote_sane(5.00, 19.79, 0.35) == ASK_FAR_ABOVE_MODEL
    assert fits_dollar_cap(2001.30, 200) is False
    assert fits_dollar_cap(180.00, 200) is True


def test_over_cap_and_insane_quotes_are_never_shown_as_choices():
    """A deep-ITM SLN-like $2.50 call asking $20 on a $19.79 stock must not be a 'Safer' pick."""
    signal = {"target": 22.00, "stop": 16.00, "pivot": 19.86}
    rows = [
        {"option": "SLN261218C00002500", "bid": 19.5, "ask": 20.0, "iv": 0.80,
         "open_interest": 5000, "volume": 500, "last_trade_time": "2026-11-10T15:59:00"},
        {"option": "SLN261218C00020000", "bid": 1.10, "ask": 1.20, "iv": 0.45,
         "open_interest": 5000, "volume": 500, "last_trade_time": "2026-11-10T15:59:00"},
        {"option": "SLN261218C00021000", "bid": 0.70, "ask": 0.80, "iv": 0.44,
         "open_interest": 5000, "volume": 500, "last_trade_time": "2026-11-10T15:59:00"},
        {"option": "SLN261218C00022000", "bid": 0.40, "ask": 0.50, "iv": 0.43,
         "open_interest": 5000, "volume": 500, "last_trade_time": "2026-11-10T15:59:00"},
    ]
    book = evaluate_plan_book("SLN", rows, 19.79, signal, date(2026, 11, 9), 0.04, 0.40, 0.42, SETTINGS)
    symbols = [c["symbol"] for c in book["choices"]]
    assert "SLN261218C00002500" not in symbols
    for c in book["choices"]:
        assert c["passes"] is True
        assert c["maxLoss"] <= SETTINGS["capDollars"] + 1e-9
        assert c["ask"] <= 19.79 + 0.01
    assert book["reasons"].get(ASK_ABOVE_STOCK) or book["reasons"].get("over the dollar cap (skipped, not replaced)")
