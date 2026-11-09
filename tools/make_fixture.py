"""Build a clearly labelled TEST FIXTURE data file (for layout screenshots only) in /tmp/tito-fixture.
Uses one real delayed Cboe chain with a HYPOTHETICAL signal. Never written into docs/data."""
import json, shutil, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, "generator")
from tito.calendar_us import add_trading_days, next_trading_day
from tito.contracts import evaluate
from tito.indicators import realized_vol
from tito.sources import ET, Fetcher, cboe_chain, now_pt, yahoo_daily

out = Path("/tmp/tito-fixture"); shutil.rmtree(out, ignore_errors=True); shutil.copytree("docs", out)
data = json.loads(Path("docs/data/latest.json").read_text())
f = Fetcher(); bars = yahoo_daily(f, "F", datetime.now(ET)); closes = [b["close"] for b in bars]; spot = closes[-1]
entry = next_trading_day(datetime.fromisoformat(bars[-1]["date"]).date())
sig = {"date": bars[-1]["date"], "close": spot, "low": round(bars[-1]["low"], 2), "pivot": round(spot * 0.99, 2), "baseLow": round(spot * 0.92, 2),
       "baseLength": 18, "depth": 0.08, "volRatio": 1.6, "target": round(spot * 1.08, 2), "stop": round(bars[-1]["low"], 2),
       "liveMin": round(spot * 0.99, 2), "liveMax": round(spot * 0.99 * 1.05, 2)}
raw = cboe_chain(f, "F")
s = {"capDollars": 200, "feePerContract": 0.65, "startingCash": 1000, "contracts": 1, "maxSuggestionsPerTicker": 2}
res = evaluate("F", raw["data"]["options"], spot, sig, entry, 0.04, realized_vol(closes, 20), realized_vol(closes, 60), s)
idea = {"ticker": "F", "company": "TEST FIXTURE · hypothetical signal, not a suggestion", "sector": "Consumer Discretionary", "signal": sig,
        "nextEarnings": "2027-02-04 (fixture)", "contracts": res["picks"], "levels": {"timeStopDate": add_trading_days(entry, 15).isoformat()},
        "quoteSource": {"label": "Cboe delayed quotes", "url": "https://cdn.cboe.com/api/global/delayed_quotes/options/F.json",
                        "fileTimestamp": f"{raw['timestamp']} (time zone not stated by Cboe)", "quoteTime": "quote time not provided", "retrievedAtPT": now_pt()}}
data.update({"noTradeToday": False, "suggestions": [idea], "generatedAtPT": "TEST FIXTURE (layout only) " + data["generatedAtPT"]})
data["gate"] = {"pass": True, "checks": [{"name": "TEST FIXTURE: real market check is BLOCKED today; this file is for layout screenshots only", "pass": False, "value": "", "limit": ""}]}
(out / "data" / "latest.json").write_text(json.dumps(data, default=str))
print("fixture picks:", [p["symbol"] for p in res["picks"]])
