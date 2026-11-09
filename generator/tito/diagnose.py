"""Diagnostic only: run the contract rules on one real delayed chain with a stated hypothetical target.
Output goes to the terminal, never to the site. Not a suggestion."""
from __future__ import annotations

import sys
from datetime import datetime

from tito.calendar_us import next_trading_day
from tito.contracts import evaluate
from tito.indicators import realized_vol
from tito.sources import ET, Fetcher, cboe_chain, yahoo_daily

ticker, target_pct = sys.argv[1], float(sys.argv[2])
f = Fetcher()
bars = yahoo_daily(f, ticker, datetime.now(ET))
closes = [b["close"] for b in bars]
spot = closes[-1]
raw = cboe_chain(f, ticker)
entry = next_trading_day(datetime.fromisoformat(bars[-1]["date"]).date())
signal = {"target": spot * (1 + target_pct), "stop": bars[-1]["low"] * 0.97}
settings = {"capDollars": 200, "feePerContract": 0.65, "startingCash": 1000, "contracts": 1, "maxSuggestionsPerTicker": 2}
res = evaluate(ticker, raw["data"]["options"], spot, signal, entry, 0.04, realized_vol(closes, 20), realized_vol(closes, 60), settings)
print(f"{ticker} spot {spot:.2f} (close {bars[-1]['date']}), hypothetical target +{target_pct:.0%}, Cboe stamp {raw['timestamp']}")
print("contracts read:", res["checked"])
for k, v in sorted(res["reasons"].items(), key=lambda kv: -kv[1]):
    print(f"  {v:5d}  {k}")
for p in res["picks"]:
    print("PASS", p["symbol"], p["bid"], p["ask"], p["greeks"], "X", p["maxEntryAsk"])
