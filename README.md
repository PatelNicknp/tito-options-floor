# Tito Options Floor (Phase 1)

Delayed, research-only long-call suggestions from daily candles, plus a private manual log of paper
trades Nick confirms in TradingView. **Education, not advice. This app never places, opens, closes or
fills a trade.** Spec: the "Options Floor: Tito-Inspired Module, v1 Design" Page.

## Run it
```sh
./run_generator.sh                     # builds docs/data/latest.json from free public data (~3 min)
./run_generator.sh --limit 20          # quick run on 20 tickers
python3 -m http.server 8765 --directory docs   # then open http://localhost:8765
```
Python 3.11+ standard library only for the generator. The site is plain HTML/JS (no build step) and is
served by GitHub Pages from the `docs/` folder on `main`.

## Tests
```sh
python3 -m venv .venv && .venv/bin/pip install pytest playwright
.venv/bin/python -m pytest -q          # generator: X formula, calendar, earnings, rules, model, no-fill, labels
node --test docs/tests/logic.test.mjs  # page: X formula, every form check, delayed rejection, no fill path, labels
.venv/bin/python tools/screenshots.py  # needs both local servers (see tools/screenshots.py)
```

## Data sources (all free, read directly, no proxies)
- **US Stock Box first** (published files on raw.githubusercontent.com, read-only, never modified): ticker list
  (S&P 500 + watchlist + VCP/flag/buy-setup picks), sectors, `market_ok`, earnings dates, cache stamps, and the
  daily candles in `data/ohlc/<TICKER>.csv` and `data/index_ohlc/SPY.csv`. Stock Box's newest bar is trusted only
  when its `meta.json` says an after-close `full` run for that session; otherwise (its hourly runs write partial
  intraday bars) the newest bars are replaced from Yahoo's 5-day series.
- Yahoo Finance only for what Stock Box lacks: those newest bars, full history for any ticker Stock Box has no
  file for, sector ETFs (XLK, XLF, ...) and ^IRX for the rate. Completed bars only; today's bar is dropped until 4:15 PM ET.
- Cboe delayed option quotes (only for stocks that pass every stock rule). The Cboe file has a timestamp
  with no stated time zone; quotes carry no quote time, so the page says "quote time not provided".

## Where things are
- `generator/tito/` rules.py (daily-candle rules), contracts.py (contract choice, price check, scenarios),
  options_math.py (Black-Scholes Greeks in dollars, CRR binomial American value with convergence check),
  money.py (X formula), build.py (writes the data file), diagnose.py (terminal-only contract check).
- `docs/` index.html, js/logic.js (all form checks), js/render.js, js/app.js, style.css.
- `screenshots/`, `proposed/` (Stock Box link diff, not applied), `tools/` (fixture + screenshots).

## Privacy
The trade log and settings live only in the browser's localStorage (`tito-floor-log-v1`,
`tito-floor-settings-v1`). Export/import JSON for backups. Nothing is sent anywhere.

## Automatic updates (`.github/workflows/update.yml`)
- Runs on US trading days at about **6:00 AM PT** (13:00 and 14:00 UTC crons; a guard keeps only the one in the
  PT window, so it works through daylight saving). Backup at about **6:40 AM PT** and catch-up at about
  **9:00 AM PT**, each only if that day's data is still missing. Plus the manual "Run workflow" button.
- Market holidays (NYSE 2026-2027 list in `calendar_us.py` / `logic.js`) and weekends are skipped.
  **Add the 2028 holidays before 2028.**
- Self-healing: each network fetch retries with exponential backoff (2s, 4s, 8s; no retry on 404); the whole
  build retries 3 times in the job; runs never overlap (concurrency group); a failed run keeps the last good
  `latest.json`, writes `docs/data/status.json` (last run, result, last success, next run), and the page shows a
  red "Out of date" banner with the last success time.
- Permissions: `contents: write` only. No secrets. The job commits `docs/data/` and GitHub Pages rebuilds.
- Keepalive: GitHub disables scheduled workflows in a public repo after 60 days with no repository activity.
  Every run that goes ahead commits `status.json` (its run time always changes), so there is a commit every
  trading day and the 60-day limit is not reached. If it ever is disabled: `gh workflow enable update.yml`.
- Test the failure path locally: `TITO_FAIL_FETCH=query1.finance.yahoo.com ./run_generator.sh --out /tmp/x.json --status /tmp/s.json`
  (any URL containing the text fails).

## Publishing notes
- Data is delayed and for research only. Yahoo Finance and Cboe have their own terms of use for their data;
  Nick has said he will handle the legal side of publishing it.
- The private trade log never leaves the browser and is not in this repo.
