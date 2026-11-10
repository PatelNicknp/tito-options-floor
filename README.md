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

## Rules
A stock must pass the chart rules (breakout, volume, moving averages, chase limit, sector, relative strength)
and a contract must pass the contract rules (35–70 days to expiry, delta 0.35–0.60, open interest at least 500,
volume at least 50, IV at most 1.3× realized volatility, skew, earnings at least 15 trading days away, price at
or under the ceiling X). At most 3 open positions and 1 per sector.

**Test rule:** breakout-day volume must be at least **1.25×** the prior 50-day average (**was 1.50×**).

**Test rule:** the tight base may be **5–40 sessions (was 10–40)**. Depth still ≤ 25%. For bases of 10 or more
sessions the last-10 range must still be ≤ 60% of the base. For a shorter 5–9 session base the last half of
the base is used instead (rounded up, at least 2 sessions): a last-10 window would be longer than the base,
and using the whole short base would ask a range to be 60% of itself. Volume dry-up uses that same trailing
slice. No other rule was loosened.

When the **S&P 500 / market_ok check** is the only rule that fails (SPY below a falling 50-day average, or
Stock Box `market_ok` is no), ideas that passed everything else are still shown, tagged **Market against you**.
The market is weak, so breakouts fail more often. They are shown because you said you will manage the risk.
Your adjustable max loss per trade stays at the amount you set (default $200 including fees). It is not
lowered on tagged ideas, and no extra relative-strength filter is added.

If some other market-check item fails (interest rate missing, or Stock Box prices not from the last completed
session), those ideas stay in a learning-only list and cannot be logged from the page.

## Privacy
The trade log and settings live only in the browser's localStorage (`tito-floor-log-v1`,
`tito-floor-settings-v1`). Export/import JSON for backups. Nothing is sent anywhere.

## Automatic updates (`.github/workflows/update.yml`)
- Runs on US trading days at about **6:00 AM PT** (13:00 and 14:00 UTC crons; a guard keeps only the one in the
  PT window, so it works through daylight saving). Backup at about **6:40 AM PT** and catch-up at about
  **9:00 AM PT**, each only if that day's data is still missing. Plus the manual "Run workflow" button.
  Those backup and catch-up times are real crons. The guard only runs after GitHub has created a run, so it
  cannot fill in a cron GitHub never started.
- GitHub may not run a schedule on the day it is added or changed, and it can drop a cron at a busy minute
  (especially at the top of the hour). `watchdog.yml` is a second set of crons (6:15, 6:55, 9:15, 11:45,
  12:45 PT, both PST and PDT). Each one only looks at `status.json` and dispatches the daily build when
  today's file is missing. `keeper.yml` is a long-running job that sleeps until those same slots and
  dispatches a missed run with the workflow's own `GITHUB_TOKEN`. At most two build attempts a day, and a
  second run waits until the first finishes. `selfHeal` in `config.json` turns the make-up off.
- Market holidays (NYSE 2026-2027 list in `calendar_us.py` / `logic.js`) and weekends are skipped.
  **Add the 2028 holidays before 2028.**
- Self-healing: each network fetch retries with exponential backoff (2s, 4s, 8s; no retry on 404); the whole
  build retries 3 times in the job; runs never overlap (concurrency group); a failed run keeps the last good
  `latest.json`, writes `docs/data/status.json` (last run, result, last success, next run), and the page shows a
  red "Out of date" banner with the last success time. An empty idea list is a finished result: the top of the
  page says which market check failed and how many stocks passed the rules.
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
