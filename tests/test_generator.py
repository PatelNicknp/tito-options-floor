import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from tito import rules
from tito.build import DISCLAIMER, LABEL, classify_gate, earnings_ok
from tito.calendar_us import add_trading_days, is_trading_day, next_trading_day, trading_days_between
from tito.contracts import evaluate, parse_occ, spread_ok
from tito.money import max_entry_ask
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
    assert base is not None and 10 <= base["length"] <= 40 and base["depth"] <= 0.25 and base["volRatio"] >= 1.5


def test_no_breakout_without_volume():
    assert rules.find_base(make_bars(breakout=False)) is None


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
    for idea in data["suggestions"] + data.get("marketAgainst", []) + data["blocked"]:
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
