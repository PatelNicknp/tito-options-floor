"""Compare tight-base minimum length 10 vs 5 on today's candles. Volume bar stays 1.25x. No Cboe calls."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from tito import rules
from tito.build import yahoo_symbol
from tito.candles import expected_session, load_candles, stockbox_trusted
from tito.sources import ET, Fetcher, completed_only, stockbox_ohlc, yahoo_daily

ROOT = Path(__file__).resolve().parents[1]


def chart_ok(bars, spy_closes, sector, etf, min_length):
    checks, signal = rules.stock_rules(bars, spy_closes, rules.BREAKOUT_VOL_MIN, min_length)
    checks.append(rules.sector_rule(rules.SECTOR_ETF.get(sector), etf.get(rules.SECTOR_ETF.get(sector, "")), spy_closes))
    failed = [c["name"] for c in checks if not c["pass"]]
    return bool(signal) and not failed, failed, signal


def main() -> None:
    config = json.loads((ROOT / "config.json").read_text())
    near = [n["ticker"] for n in json.loads((ROOT / "docs/data/latest.json").read_text()).get("nearMisses") or []]
    f = Fetcher(pause=config["requestPauseSeconds"])
    base = config["stockBoxBase"]
    now_et = datetime.now(ET)

    def sb(name, kind="csv"):
        url = f"{base}/{name}" if not name.startswith("../") else base.rsplit("/", 1)[0] + "/" + name[3:]
        return f.csv(url) if kind == "csv" else f.json(url)

    meta = sb("meta.json", "json") or {}
    universe = sb("universe.csv") or []
    watch = sb("watchlist.csv") or []
    vcp = sb("scanner_vcp.csv") or []
    flag = sb("scanner_flag.csv") or []
    setups = sb("buy_setups.csv") or []
    sector_of = {r["ticker"]: r.get("sector", "") for r in universe}
    for rows in (vcp, flag, setups):
        for r in rows:
            sector_of.setdefault(r["ticker"], r.get("sector", ""))
    tickers = sorted({r["ticker"] for r in universe if r.get("in_sp500") == "yes"}
                     | {r["ticker"] for r in watch} | {r["ticker"] for r in vcp + flag + setups if r.get("ticker")})
    tickers = [t for t in tickers if t and t != "MISSING"]

    expected = expected_session(now_et)
    trusted = stockbox_trusted(meta, expected)
    unconfirmed = None if trusted else meta.get("price_as_of")

    def candles(sym, url):
        return load_candles(
            lambda: completed_only(stockbox_ohlc(f, url), now_et),
            lambda: yahoo_daily(f, yahoo_symbol(sym), now_et, "5d"),
            lambda: yahoo_daily(f, yahoo_symbol(sym), now_et, "2y"),
            trusted, expected, unconfirmed)[0]

    spy = candles("SPY", f"{base}/index_ohlc/SPY.csv")
    if not spy or spy[-1]["date"] != expected.isoformat():
        raise SystemExit(f"SPY session mismatch: expected {expected}, got {spy[-1]['date'] if spy else None}")
    spy_closes = [b["close"] for b in spy]
    etf = {e: [b["close"] for b in yahoo_daily(f, e, now_et)] for e in sorted(set(rules.SECTOR_ETF.values()))}

    old_n = new_n = fresh = 0
    old_pass, new_pass = [], []
    near_rows = []
    kinds_old, kinds_new = Counter(), Counter()
    newly = []
    for i, t in enumerate(tickers):
        bars = candles(t, f"{base}/ohlc/{t}.csv")
        if not bars or bars[-1]["date"] != expected.isoformat():
            if (i + 1) % 100 == 0:
                print(f"  checked {i + 1}/{len(tickers)}", flush=True)
            continue
        fresh += 1
        sector = sector_of.get(t, "")
        ok_old, fail_old, sig_old = chart_ok(bars, spy_closes, sector, etf, 10)
        ok_new, fail_new, sig_new = chart_ok(bars, spy_closes, sector, etf, 5)
        if ok_old:
            old_n += 1
            old_pass.append(t)
        if ok_new:
            new_n += 1
            new_pass.append({"ticker": t, "length": None if not sig_new else sig_new["baseLength"],
                             "volRatio": None if not sig_new else sig_new["volRatio"]})
        if t in near:
            c_old = rules.classify_breakout_fail(bars, min_length=10)
            c_new = rules.classify_breakout_fail(bars, min_length=5)
            kinds_old[c_old["kind"]] += 1
            kinds_new[c_new["kind"]] += 1
            row = {"ticker": t, "chartPassOld": ok_old, "chartPassNew": ok_new,
                   "failOld": fail_old, "failNew": fail_new,
                   "classOld": c_old, "classNew": c_new,
                   "baseLengthNew": None if not sig_new else sig_new["baseLength"]}
            near_rows.append(row)
            if ok_new and not ok_old:
                newly.append(t)
        if (i + 1) % 100 == 0:
            print(f"  checked {i + 1}/{len(tickers)}", flush=True)

    report = {
        "signalSession": expected.isoformat(),
        "trustedStockBoxLastBar": trusted,
        "tickers": len(tickers),
        "withFreshCandles": fresh,
        "volumeThreshold": rules.BREAKOUT_VOL_MIN,
        "oldMinLength": 10,
        "newMinLength": 5,
        "chartPassOld": old_n,
        "chartPassNew": new_n,
        "chartPassOldTickers": old_pass,
        "chartPassNewTickers": new_pass,
        "nearMissCountListed": len(near),
        "nearMissClassified": len(near_rows),
        "nearMissKindsOld": dict(kinds_old),
        "nearMissKindsNew": dict(kinds_new),
        "nearMissNowPassChart": newly,
        "nearMissDetail": near_rows,
    }
    out = Path("/tmp/base_len_diag.json")
    out.write_text(json.dumps(report, indent=2) + "\n")
    skip = {"nearMissDetail", "chartPassOldTickers", "chartPassNewTickers"}
    print(json.dumps({k: report[k] for k in report if k not in skip}, indent=2))
    print("new passers:", new_pass)
    print("wrote", out)


if __name__ == "__main__":
    main()
