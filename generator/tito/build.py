"""Build docs/data/latest.json. Research suggestions only: this program never opens, closes or fills a trade."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from tito import rules
from tito.calendar_us import add_trading_days, is_trading_day, next_trading_day
from tito.contracts import evaluate
from tito.indicators import realized_vol, sma
from tito.money import max_entry_ask
from tito.candles import expected_session, load_candles, stockbox_trusted
from tito.sources import ET, PT, Fetcher, cboe_chain, completed_only, now_pt, stockbox_ohlc, yahoo_daily

LABEL = "DELAYED · research only · not advice"
DISCLAIMER = ("Education, not advice. Prices are free delayed data and may be stale. This page never places, opens, "
              "closes or fills any trade. Check the live chart and price in TradingView before you log anything.")
SCHEDULE = ("US trading days about 6:00 AM PT; backup 6:40 AM PT and 9:00 AM PT if that day's data is still missing; "
            "a keeper makes up a dropped GitHub cron")
# workflow_dispatch reasons that must obey the same "once per day" rule as a cron.
# A button press (reason manual, or no reason) always runs.
MAKEUP_REASONS = frozenset({"keeper", "keeper_retry", "watchdog"})



MARKET_CHECK_NAMES = frozenset({
    "Market: SPY above a rising 50-day average",
    "US Stock Box market_ok",
})
MARKET_AGAINST_BADGE = "Market against you"
MARKET_AGAINST_NOTE = "The market is weak, so breakouts fail more often."


def classify_gate(checks: list[dict]) -> str:
    """pass = market is fine; market_against = only the S&P 500 / market_ok checks failed;
    blocked = some other gate check failed (session date, missing rate, ...)."""
    failed = [c for c in checks if not c.get("pass")]
    if not failed:
        return "pass"
    if all(c.get("name") in MARKET_CHECK_NAMES for c in failed):
        return "market_against"
    return "blocked"


def yahoo_symbol(t: str) -> str:
    return t.replace(".", "-")


def earnings_ok(next_earnings: str | None, entry: date) -> tuple[bool, str]:
    """No entry if earnings fall on or before the 15th trading day after entry. Unknown = reject."""
    if not next_earnings:
        return False, "earnings date unknown"
    try:
        d = date.fromisoformat(next_earnings[:10])
    except ValueError:
        return False, "earnings date unreadable"
    if d < entry:
        return False, f"listed date {d} is in the past (unknown next date)"
    limit = add_trading_days(entry, rules.EARNINGS_TRADING_DAYS)
    return d > limit, f"next earnings {d}; must be after {limit}"


def source(sid: str, label: str, url: str, ok: bool, detail: str, source_time: str | None = None) -> dict:
    return {"id": sid, "label": label, "url": url, "ok": ok, "detail": detail,
            "retrievedAtPT": now_pt(), "sourceTime": source_time or "not provided"}


def run(config: dict, limit: int | None = None) -> dict:
    f = Fetcher(pause=config["requestPauseSeconds"])
    base = config["stockBoxBase"]
    now_et = datetime.now(ET)
    sources: list[dict] = []
    warnings: list[str] = []

    def sb(name: str, kind: str = "csv"):
        url = f"{base}/{name}" if not name.startswith("../") else base.rsplit("/", 1)[0] + "/" + name[3:]
        try:
            data = f.csv(url) if kind == "csv" else f.json(url)
            sources.append(source("stockbox_" + name, f"US Stock Box {name}", url, True, f"{len(data)} rows" if kind == "csv" else "read"))
            return data
        except Exception as exc:  # noqa: BLE001
            sources.append(source("stockbox_" + name, f"US Stock Box {name}", url, False, str(exc)[:200]))
            return None

    meta = sb("meta.json", "json") or {}
    if sources[-1]["ok"]:
        sources[-1]["sourceTime"] = f"updated {meta.get('updated_pt')}, prices as of {meta.get('price_as_of')}, mode {meta.get('mode')}"
    universe = sb("universe.csv") or []
    watch = sb("watchlist.csv") or []
    vcp = sb("scanner_vcp.csv") or []
    flag = sb("scanner_flag.csv") or []
    setups = sb("buy_setups.csv") or []
    earnings = sb("earnings_calendar.csv") or []
    stamps = sb("../cache/stamps.json", "json")

    sector_of = {r["ticker"]: r.get("sector", "") for r in universe}
    company_of = {r["ticker"]: r.get("company", "") for r in universe}
    for rows in (vcp, flag, setups):
        for r in rows:
            sector_of.setdefault(r["ticker"], r.get("sector", ""))
            company_of.setdefault(r["ticker"], r.get("company", ""))
    tickers = sorted({r["ticker"] for r in universe if r.get("in_sp500") == "yes"}
                     | {r["ticker"] for r in watch} | {r["ticker"] for r in vcp + flag + setups if r.get("ticker")})
    tickers = [t for t in tickers if t and t != "MISSING"]
    if limit:
        tickers = tickers[:limit]
    if not tickers:
        raise RuntimeError("US Stock Box stock list unavailable, so there is nothing to check.")
    next_earn = {r["ticker"]: r.get("next_earnings") for r in earnings}
    sb_ref = {}
    for name, rows in (("VCP", vcp), ("Flag", flag)):
        for r in rows:
            if r.get("ticker") and r["ticker"] != "MISSING":
                sb_ref.setdefault(r["ticker"], f"Stock Box {name} scanner: {r.get('status')} (pivot {r.get('pivot')}, as of {r.get('price_as_of')})")

    # Market data: Stock Box candles first, Yahoo only for what Stock Box lacks
    expected = expected_session(now_et)
    trusted = stockbox_trusted(meta, expected)
    unconfirmed = None if trusted else meta.get("price_as_of")
    repo_base = base.rsplit("/", 1)[0]
    candle_sources: dict[str, int] = {}

    def daily(sym: str, label: str) -> list[dict]:
        try:
            return yahoo_daily(f, yahoo_symbol(sym), now_et)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{label}: {str(exc)[:120]}")
            return []

    def candles(sym: str, sb_url: str) -> tuple[list[dict], str]:
        bars, src = load_candles(
            lambda: completed_only(stockbox_ohlc(f, sb_url), now_et),
            lambda: yahoo_daily(f, yahoo_symbol(sym), now_et, "5d"),
            lambda: yahoo_daily(f, yahoo_symbol(sym), now_et, "2y"),
            trusted, expected, unconfirmed)
        candle_sources[src] = candle_sources.get(src, 0) + 1
        return bars, src

    spy, spy_src = candles("SPY", f"{base}/index_ohlc/SPY.csv")
    if not spy or spy[-1]["date"] != expected.isoformat():
        raise RuntimeError(f"SPY daily candles for {expected} unavailable (got {spy[-1]['date'] if spy else 'none'}); nothing can be checked.")
    spy_closes = [b["close"] for b in spy]
    signal_date = date.fromisoformat(spy[-1]["date"])
    entry_date = next_trading_day(signal_date)
    sources.append(source("stockbox_ohlc", "US Stock Box daily candles (data/ohlc, data/index_ohlc/SPY.csv)", f"{base}/ohlc/",
                          True, "used first for every ticker",
                          f"Stock Box run {meta.get('updated_pt')} ({meta.get('mode')}); last bar trusted as complete: {trusted}"))
    irx = daily("^IRX", "13-week T-bill")
    rate = irx[-1]["close"] / 100 if irx else None
    sources.append(source("yahoo_irx", "13-week T-bill yield (^IRX) for the model rate", "https://finance.yahoo.com/quote/%5EIRX",
                          rate is not None, f"{rate * 100:.2f}%" if rate is not None else "missing", irx[-1]["date"] if irx else None))
    etf = {e: [b["close"] for b in daily(e, e)] for e in sorted(set(rules.SECTOR_ETF.values()))}

    # Gate 0
    gate: list[dict] = []
    m = rules.market_rule(spy_closes)
    gate.append(m)
    ok_values = {r.get("market_ok") for r in setups}
    gate.append(rules.check("US Stock Box market_ok", ok_values == {"yes"},
                            f"market_ok = {', '.join(sorted(v for v in ok_values if v)) or 'missing'} on {len(setups)} buy setups",
                            "market_ok = yes"))
    sb_asof = meta.get("price_as_of")
    gate.append(rules.check("US Stock Box prices are from the last completed session", sb_asof == signal_date.isoformat(),
                            f"Stock Box price_as_of {sb_asof}; last completed session {signal_date}", "same session"))
    gate.append(rules.check("Interest rate for the model", rate is not None, f"{rate:.4f}" if rate is not None else "missing", "available"))
    gate_pass = all(g["pass"] for g in gate)
    warnings.append("US Stock Box publishes no manifest or generation ID, so mixed generations cannot be ruled out. "
                    "Its candle history is reused; its newest bar is used only after its after-close full run for that "
                    "session, otherwise the newest bars come from Yahoo.")
    if meta.get("mode") != "full":
        warnings.append(f"US Stock Box's latest run was '{meta.get('mode')}' ({meta.get('updated_pt')}), not an after-close full run, so its newest bar was not trusted and recent bars came from Yahoo.")
    if stamps:
        keys = [k for k in ("prices_full_refresh", "yahoo_slow", "options_chains", "universe_sp500") if k in stamps]
        warnings.append("US Stock Box cache dates (mixed generations): " + ", ".join(f"{k} {stamps[k]}" for k in keys)
                        + ". Its earnings dates come from the Yahoo cache, so they may be a few days old.")

    # Stock rules
    marks: dict[str, dict] = {}
    passed: list[dict] = []
    near: list[dict] = []
    funnel = {"tickers": len(tickers), "withFreshCandles": 0, "stockRulesPass": 0, "earningsPass": 0, "withContracts": 0}
    for i, t in enumerate(tickers):
        bars, _src = candles(t, f"{base}/ohlc/{t}.csv")
        if not bars:
            continue
        closes = [b["close"] for b in bars]
        marks[t] = {"date": bars[-1]["date"], "close": round(closes[-1], 2), "low": round(bars[-1]["low"], 2),
                    "sma10": round(sma(closes, 10) or 0, 2), "sma20": round(sma(closes, 20) or 0, 2)}
        if bars[-1]["date"] != signal_date.isoformat():
            continue
        funnel["withFreshCandles"] += 1
        checks, signal = rules.stock_rules(bars, spy_closes)
        sector = sector_of.get(t, "")
        checks.append(rules.sector_rule(rules.SECTOR_ETF.get(sector), etf.get(rules.SECTOR_ETF.get(sector, "")), spy_closes))
        e_ok, e_text = earnings_ok(next_earn.get(t), entry_date)
        checks.append(rules.check("No earnings within 15 trading days", e_ok, e_text, "known date after the 15th trading day"))
        fails = [c for c in checks if not c["pass"]]
        if signal and not any(not c["pass"] for c in checks if c["name"] != "No earnings within 15 trading days"):
            funnel["stockRulesPass"] += 1
        if not fails and signal:
            funnel["earningsPass"] += 1
            ref = sb_ref.get(t)
            passed.append({"ticker": t, "company": company_of.get(t, ""), "sector": sector, "stockBoxReference": ref, "candleSource": _src,
                           "sectorEtf": rules.SECTOR_ETF.get(sector), "signal": signal, "checks": checks,
                           "closes": closes, "nextEarnings": next_earn.get(t)})
        elif len(fails) == 1:
            near.append({"ticker": t, "company": company_of.get(t, ""), "failed": fails[0]["name"], "detail": fails[0]["value"]})
        if (i + 1) % 100 == 0:
            print(f"  checked {i + 1}/{len(tickers)}", flush=True)

    # Contracts for stocks that passed every stock rule
    settings = {k: config[k] for k in ("capDollars", "feePerContract", "startingCash", "contracts", "maxSuggestionsPerTicker")}
    ideas: list[dict] = []
    chain_ok = 0
    for p in passed:
        try:
            raw = cboe_chain(f, p["ticker"])
            chain_ok += 1
        except Exception as exc:  # noqa: BLE001
            p["contractNote"] = f"Cboe delayed chain unavailable: {str(exc)[:120]}"
            p["contracts"] = []
            ideas.append(p)
            continue
        data = raw.get("data") or {}
        closes = p.pop("closes")
        res = evaluate(p["ticker"], data.get("options") or [], p["signal"]["close"], p["signal"], entry_date, rate or 0,
                       realized_vol(closes, 20), realized_vol(closes, 60), settings)
        p["contracts"] = res["picks"]
        p["contractsChecked"] = res["checked"]
        p["contractRejects"] = res["reasons"]
        p["quoteSource"] = {"label": "Cboe delayed quotes", "url": f"https://cdn.cboe.com/api/global/delayed_quotes/options/{p['ticker']}.json",
                            "fileTimestamp": f"{raw.get('timestamp')} (time zone not stated by Cboe)",
                            "quoteTime": "quote time not provided", "retrievedAtPT": now_pt()}
        p["levels"] = {"timeStopDate": add_trading_days(entry_date, 15).isoformat()}
        if p["contracts"]:
            funnel["withContracts"] += 1
        ideas.append(p)
    for p in ideas:
        p.pop("closes", None)
    yahoo_used = sum(v for k, v in candle_sources.items() if "yahoo" in k)
    sources.append(source("yahoo_daily", "Yahoo Finance daily candles (only what Stock Box lacks; completed bars only)",
                          "https://query1.finance.yahoo.com/v8/finance/chart/", True,
                          f"{yahoo_used} tickers needed Yahoo; sector ETFs and ^IRX always", f"session close {signal_date} 1:00 PM PT"))
    sources.append(source("cboe_delayed", "Cboe delayed option quotes", "https://cdn.cboe.com/api/global/delayed_quotes/options/",
                          chain_ok == len(passed), f"{chain_ok} of {len(passed)} chains loaded" if passed else "not needed (no stock passed)"))

    kind = classify_gate(gate)
    ready = [p for p in ideas if p.get("contracts")]
    suggestions, market_against, blocked = [], [], []
    if kind == "pass":
        suggestions = ready
    elif kind == "market_against":
        for p in ready:
            p["marketAgainst"] = True
            p["tag"] = MARKET_AGAINST_BADGE
            p["marketNote"] = MARKET_AGAINST_NOTE
        market_against = ready
    else:
        blocked = ideas
    near.sort(key=lambda r: r["failed"])
    if funnel["withFreshCandles"] < 0.8 * funnel["tickers"]:
        raise RuntimeError(f"Only {funnel['withFreshCandles']} of {funnel['tickers']} stocks had fresh candles; keeping the last good data.")
    return {
        "version": 1,
        "label": LABEL,
        "disclaimer": DISCLAIMER,
        "generatedAtPT": now_pt(),
        "signalSession": signal_date.isoformat(),
        "entrySession": entry_date.isoformat(),
        "entryWindowPT": "7:00 AM – 12:30 PM PT",
        "defaults": {**settings, "maxEntryAsk": max_entry_ask(settings["capDollars"], settings["feePerContract"],
                                                             settings["contracts"], settings["startingCash"])},
        "gate": {"pass": gate_pass, "kind": kind, "checks": gate},
        "warnings": warnings,
        "sources": sources,
        "funnel": funnel,
        "noTradeToday": not suggestions and not market_against,
        "suggestions": suggestions,
        "marketAgainst": market_against,
        "blocked": blocked,
        "nearMisses": near[:25],
        "provenance": provenance(candle_sources, trusted, meta),
        "expectedSession": expected.isoformat(),
        "notChecked": ["Ex-dividend dates (no free source wired in); every suggestion is flagged 'not checked'."],
        "marks": marks,
    }


def provenance(candle_sources: dict, trusted: bool, meta: dict) -> list[dict]:
    n = lambda k: candle_sources.get(k, 0)  # noqa: E731
    return [
        {"field": "Stock list (S&P 500, watchlist, scanner picks), company names, sectors", "from": "US Stock Box", "how": "universe, watchlist, scanner_vcp, scanner_flag, buy_setups files"},
        {"field": "Market veto (market_ok)", "from": "US Stock Box", "how": "buy_setups.csv"},
        {"field": "Earnings dates", "from": "US Stock Box", "how": "earnings_calendar.csv"},
        {"field": "Daily candle history (stocks and SPY)", "from": "US Stock Box", "how": f"data/ohlc and index_ohlc/SPY.csv for {n('stockbox') + n('stockbox+yahoo-recent') + n('stockbox-unconfirmed-dropped')} tickers"},
        {"field": "Newest completed bar", "from": "US Stock Box" if trusted else "Yahoo (5-day) where Stock Box's bar was not confirmed",
         "how": f"Stock Box run {meta.get('updated_pt')} mode {meta.get('mode')}; {n('stockbox')} from Stock Box, {n('stockbox+yahoo-recent')} topped up from Yahoo, {n('stockbox-unconfirmed-dropped')} dropped unconfirmed bar"},
        {"field": "Full candle history where Stock Box had none", "from": "Yahoo (fetched directly)", "how": f"{n('yahoo')} tickers; {n('none')} with no data"},
        {"field": "Moving averages, relative strength, base, volume ratios, breakout", "from": "Computed here", "how": "from the candles above, using this module's own rules (Stock Box's own scores use different definitions)"},
        {"field": "Stock Box VCP / flag status on idea cards", "from": "US Stock Box", "how": "reference only, not a rule"},
        {"field": "Sector ETF candles (XLK, XLF, ...)", "from": "Yahoo (fetched directly)", "how": "Stock Box does not publish them"},
        {"field": "Model interest rate (13-week T-bill, ^IRX)", "from": "Yahoo (fetched directly)", "how": "Stock Box does not publish it"},
        {"field": "Option chains (bid, ask, IV, open interest, volume)", "from": "Cboe delayed quotes (fetched directly)", "how": "only for stocks that pass every stock rule"},
    ]


def next_run_pt(now_pt: datetime) -> str:
    """Next scheduled 6:00 AM PT run on a US trading day."""
    day = now_pt.date()
    if not (is_trading_day(day) and (now_pt.hour, now_pt.minute) < (6, 0)):
        day = next_trading_day(day)
    return f"{day.isoformat()} 06:00 AM PT"


def run_url() -> str | None:
    if os.environ.get("GITHUB_RUN_ID"):
        return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return None


def trigger_name() -> str:
    """Honest label for status.json: keeper / watchdog when a make-up started the run, otherwise the event."""
    return os.environ.get("TITO_TRIGGER") or os.environ.get("GITHUB_EVENT_NAME") or "local"


def data_already_produced(status: dict, now_pt: datetime) -> bool:
    """Today's file is on main when the last success is this PT date and the session the morning run expects."""
    want = expected_session(now_pt.astimezone(ET)).isoformat()
    return status.get("lastSuccessSession") == want and str(status.get("lastSuccessPT", "")).startswith(now_pt.date().isoformat())


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str) + "\n")
    tmp.replace(path)


def guard(status_path: Path, event: str, reason: str = "") -> bool:
    """Should this workflow trigger run? A button press always does. Scheduled runs, and make-up
    dispatches (keeper / watchdog), run on a US trading day between 5:45 AM and 1:00 PM PT, and only
    if today's data has not already been produced. The guard cannot stop GitHub from creating a run;
    it only skips the fetch once a run exists."""
    if event != "schedule" and reason not in MAKEUP_REASONS:
        return True
    now = datetime.now(PT)
    if not is_trading_day(now.date()):
        print(f"skip: {now.date()} is not a US trading day")
        return False
    if not ((5, 45) <= (now.hour, now.minute) < (13, 0)):
        print(f"skip: {now.strftime('%H:%M')} PT is outside the 5:45 AM-1:00 PM PT run window (other DST twin)")
        return False
    st = read_json(status_path)
    if data_already_produced(st, now):
        print(f"skip: today's data (session {st.get('lastSuccessSession')}) was already produced at {st.get('lastSuccessPT')}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Build research suggestions. Never places or fills trades.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="docs/data/latest.json")
    ap.add_argument("--status", default="docs/data/status.json")
    ap.add_argument("--limit", type=int, default=None, help="check only the first N tickers (for quick runs)")
    ap.add_argument("--guard", metavar="EVENT", help="only decide whether a workflow run should go ahead")
    ap.add_argument("--reason", default="", help="workflow_dispatch reason: manual, keeper, keeper_retry, or watchdog")
    ap.add_argument("--record-failure", metavar="MESSAGE", help="only mark the latest run as failed in the status file")
    args = ap.parse_args()
    out, status_path = Path(args.out), Path(args.status)
    if args.record_failure:
        status = read_json(status_path)
        status.update({"lastRunPT": now_pt(), "trigger": trigger_name(), "runUrl": run_url(),
                       "nextRunPT": next_run_pt(datetime.now(PT)), "schedule": SCHEDULE,
                       "lastRunResult": "failed", "lastError": args.record_failure[:300]})
        write_json(status_path, status)
        return 0
    if args.guard:
        ok = guard(status_path, args.guard, args.reason)
        print(f"should_run={'true' if ok else 'false'}")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                fh.write(f"should_run={'true' if ok else 'false'}\n")
        return 0
    config = json.loads(Path(args.config).read_text())
    status = read_json(status_path)
    now = datetime.now(PT)
    status.update({"lastRunPT": now_pt(), "trigger": trigger_name(), "runUrl": run_url(),
                   "nextRunPT": next_run_pt(now), "schedule": SCHEDULE})
    try:
        report = run(config, args.limit)
    except Exception as exc:  # noqa: BLE001 - keep last good data, record the failure
        status.update({"lastRunResult": "failed", "lastError": str(exc)[:300]})
        write_json(status_path, status)
        print(f"FAILED: {exc}. Kept the last good data file; status written to {status_path}.")
        return 1
    write_json(out, report)
    status.update({"lastRunResult": "success", "lastError": None, "lastSuccessPT": report["generatedAtPT"],
                   "lastSuccessSession": report["signalSession"]})
    write_json(status_path, status)
    print(f"Wrote {out}: gate {report['gate'].get('kind', 'pass')}, {len(report['suggestions'])} suggestion(s), "
          f"{len(report.get('marketAgainst') or [])} tagged (market against), {len(report['blocked'])} blocked setup(s), funnel {report['funnel']}")
    for w in report["warnings"]:
        print("  note:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
