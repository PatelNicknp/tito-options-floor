import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as L from "../js/logic.js";
import * as R from "../js/render.js";

const S = { capDollars: 200, feePerContract: 0.65, startingCash: 1000 };
const good = () => ({
  ticker: "ABC", expiry: "2026-12-18", strike: "50", optionType: "CALL", side: "BUY_TO_OPEN", qty: "1",
  bid: "1.80", ask: "1.90", fill: "1.90", fees: "0.65", stockPrice: "51", timeSeen: "2026-11-09T08:15",
  dataStatus: "realtime", source: "TradingView", sector: "Industrials", nextEarnings: "2027-01-28", pivot: "50",
});
const has = (errs, text) => assert.ok(errs.some((e) => e.includes(text)), `expected "${text}" in ${JSON.stringify(errs)}`);
const pos = (sector, id = "p" + sector) => L.buildEntry({ ...good(), sector, ticker: "T" + sector.slice(0, 3).toUpperCase() }, S, [], id);

test("X formula: (cap - open fee - close fee) / (100 x qty), rounded down, never above cash", () => {
  assert.equal(L.maxEntryAsk(200, 0.65, 1, 1000), 1.98);
  assert.equal(L.maxEntryAsk(200, 0, 1, 1000), 2.0);
  assert.equal(L.maxEntryAsk(200, 0.65, 2, 1000), 0.98);
  assert.equal(L.maxEntryAsk(200, 0.65, 1, 120), 1.18);
  assert.equal(L.maxEntryAsk(1, 0.65, 1, 1000), null);
  assert.equal(L.maxEntryAsk(200, 0.65, 0, 1000), null);
});

test("a correct entry passes and is saved as a Nick-confirmed TradingView fill", () => {
  assert.deepEqual(L.checkEntry(good(), S, []), []);
  const e = L.buildEntry(good(), S, [], "x1");
  assert.equal(e.fillType, "nick-confirmed");
  assert.equal(e.fillLabel, "Nick-confirmed fill (TradingView)");
  assert.equal(e.entry.source, "TradingView");
});

test("each entry check rejects", () => {
  const cases = [
    [{ ticker: "" }, "ticker"], [{ expiry: "" }, "expiry"], [{ strike: "" }, "strike"],
    [{ optionType: "PUT" }, "long calls"], [{ side: "SELL_TO_CLOSE" }, "Buy to open"], [{ qty: "1.5" }, "whole number"],
    [{ bid: "0" }, "bid"], [{ ask: "1.70" }, "at or above the bid"], [{ fill: "1.85" }, "equal the ask"],
    [{ fees: "" }, "fees"], [{ source: "Other" }, "TradingView"], [{ dataStatus: "" }, "Confirm that TradingView showed real-time"],
    [{ timeSeen: "" }, "time you saw"], [{ timeSeen: "2026-11-08T08:00" }, "not a US market trading day"],
    [{ timeSeen: "2026-11-09T06:59" }, "7:00 AM and 12:30 PM"], [{ timeSeen: "2026-11-09T12:31" }, "7:00 AM and 12:30 PM"],
    [{ pivot: "" }, "pivot"], [{ stockPrice: "49.9" }, "below the pivot"], [{ stockPrice: "52.6" }, "more than 5% above"],
    [{ ask: "2.00", fill: "2.00" }, "above your max loss"], [{ sector: "" }, "sector"],
    [{ nextEarnings: "" }, "unknown"], [{ nextEarnings: "2026-11-20" }, "within 15 trading days"],
    [{ expiry: "2026-11-09" }, "after the entry date"],
  ];
  for (const [patch, text] of cases) has(L.checkEntry({ ...good(), ...patch }, S, []), text);
});

test("delayed TradingView data is rejected", () => {
  has(L.checkEntry({ ...good(), dataStatus: "delayed" }, S, []), "delayed data");
  assert.throws(() => L.buildEntry({ ...good(), dataStatus: "delayed" }, S, [], "x"));
});

test("cap uses cash when cash is lower, and cash check", () => {
  const log = [{ ...pos("Energy"), entry: { ...pos("Energy").entry, fill: 8.5 } }]; // $850 + fee spent
  has(L.checkEntry(good(), S, log), "above your max loss");
  assert.ok(L.cashAvailable(S, log) < 200);
});

test("max 3 open positions and 1 per sector", () => {
  const log = [pos("Energy"), pos("Utilities"), pos("Financials")];
  has(L.checkEntry(good(), S, log), "3 open positions");
  has(L.checkEntry({ ...good(), sector: "Energy" }, S, [pos("Energy")]), "1 per sector");
});

test("exit: whole position, sell at bid, real-time only, market hours", () => {
  const p = pos("Energy");
  const ex = { position: p.id, side: "SELL_TO_CLOSE", qty: "1", bid: "2.50", ask: "2.60", fill: "2.50", fees: "0.65",
    timeSeen: "2026-11-12T09:00", dataStatus: "realtime", source: "TradingView", reason: "Target" };
  assert.deepEqual(L.checkExit(ex, p), []);
  const closed = L.buildExit(ex, p);
  assert.equal(closed.status, "closed");
  assert.equal(closed.realizedPnl, Math.round((250 - 0.65 - 190.65) * 100) / 100);
  has(L.checkExit({ ...ex, fill: "2.60" }, p), "equal the bid");
  has(L.checkExit({ ...ex, qty: "2" }, p), "whole position");
  has(L.checkExit({ ...ex, dataStatus: "delayed" }, p), "delayed data");
  has(L.checkExit({ ...ex, timeSeen: "2026-11-12T13:30" }, p), "market hours");
  has(L.checkExit({ ...ex, reason: "" }, p), "exit reason");
  has(L.checkExit({ ...ex, timeSeen: "2026-11-09T08:00" }, p), "after the entry time");
});

test("exit flags are reminders computed from the daily data", () => {
  const p = { ...pos("Energy"), plan: { pivot: 50, stop: 49, target: 60 } };
  const f = L.exitFlags(p, { date: "2026-11-10", close: 48.5, sma10: 50, sma20: 50 }, "2026-11-10", null, 0.65);
  assert.ok(f.some((x) => x.startsWith("Stop")) && f.some((x) => x.startsWith("Failed breakout")));
  const before = L.exitFlags(p, { date: "2026-11-06", close: 48.5, sma10: 50, sma20: 50 }, "2026-11-09", null, 0.65);
  assert.ok(!before.some((x) => x.startsWith("Stop")) && before.some((x) => x.startsWith("Waiting")));
  assert.ok(L.exitFlags(p, null, "2026-12-01", null, 0.65).some((x) => x.startsWith("Time stop")));
  assert.ok(L.exitFlags(p, null, "2026-11-28", null, 0.65).some((x) => x.startsWith("Expiry")));
  assert.ok(L.exitFlags(p, null, "2026-11-10", 6.0, 0.65).some((x) => x.startsWith("Option target")));
});

test("import only accepts Nick-confirmed TradingView records", () => {
  assert.deepEqual(L.checkImport({ app: "tito-options-floor", log: [pos("Energy")] }), []);
  assert.ok(L.checkImport({ app: "tito-options-floor", log: [{ ...pos("Energy"), fillType: "bot" }] }).length);
  assert.ok(L.checkImport({ log: [] }).length);
});

test("no bot fill path: the page code never builds entries outside the form handlers", () => {
  const app = readFileSync(new URL("../js/app.js", import.meta.url), "utf8");
  assert.equal((app.match(/buildEntry\(/g) || []).length, 1);
  assert.equal((app.match(/buildExit\(/g) || []).length, 1);
  assert.ok(app.indexOf("buildEntry(") > app.indexOf('ef.addEventListener("submit"'));
  for (const f of ["app.js", "logic.js", "render.js"]) {
    const code = readFileSync(new URL(`../js/${f}`, import.meta.url), "utf8");
    assert.ok(!code.includes("jina"), `${f} must not use a proxy`);
  }
});

test("labels: DELAYED, research only, quote time not provided", () => {
  assert.ok(R.delayedBadge().includes("DELAYED") && R.delayedBadge().includes("Research only"));
  assert.ok(R.quoteTimes(null).includes("Quote time not provided"));
  const html = R.quoteTimes({ label: "Cboe delayed quotes", url: "https://x", quoteTime: "quote time not provided", fileTimestamp: "t", retrievedAtPT: "2026-11-08 09:00 PM PT" });
  assert.ok(html.includes("DELAYED") && html.includes("quote time not provided") && html.includes("PT"));
  const nt = R.renderToday({ generatedAtPT: "g", signalSession: "s", entrySession: "e", entryWindowPT: "w", gate: { checks: [] }, noTradeToday: true,
    funnel: { tickers: 1, withFreshCandles: 1, stockRulesPass: 0, earningsPass: 0, withContracts: 0 }, suggestions: [], marketAgainst: [], blocked: [], nearMisses: [] }, S, 1000);
  assert.ok(nt.includes("No trade today") && nt.includes("DELAYED"));
  const skip = R.skipLine({ ask: 1.5 }, S, 1000);
  assert.ok(skip.includes("Skip if live ask above $1.98"));
});

test("market-against ideas are tagged amber and listed after market-OK ideas; cap stays $200", () => {
  assert.equal(S.capDollars, 200);
  const idea = (ticker, tag) => ({
    ticker, company: "Co", sector: "Energy", nextEarnings: "2027-02-01", marketAgainst: tag,
    marketNote: tag ? "The market is weak, so breakouts fail more often." : undefined,
    signal: { date: "2026-11-06", close: 12, pivot: 11.5, volRatio: 2, baseLength: 15, liveMin: 11.5, liveMax: 12.075, stop: 11, target: 13 },
    contracts: [{ symbol: ticker + "C", expiry: "2026-12-18", strike: 12, bid: 0.40, ask: 0.45, dte: 42, maxLoss: 46.3, maxProfit: "uncapped",
      breakevenAtExpiry: 12.45, optionProfitTargetBid: 1.35, iv: 0.3, hv20: 0.25, hv60: 0.25, ivRatio: 1.2, oi: 800, volume: 100,
      greeks: {}, scenarios: [], speed: 0.4, gate: {}, model: { name: "test" }, expiryExitDate: "2026-11-27" }],
    levels: { timeStopDate: "2026-12-01" },
  });
  const html = R.renderToday({ generatedAtPT: "g", signalSession: "s", entrySession: "e", entryWindowPT: "w",
    gate: { checks: [{ name: "Market: SPY above a rising 50-day average", pass: false, value: "weak" }] },
    noTradeToday: false, funnel: {}, suggestions: [idea("OK1", false)], marketAgainst: [idea("TAG1", true)], blocked: [], nearMisses: [] }, S, 1000);
  assert.match(html, /Ideas to check in TradingView[\s\S]*OK1[\s\S]*Market against you[\s\S]*TAG1/);
  assert.match(html, /The market is weak, so breakouts fail more often/);
  assert.ok(html.includes('class="badge market"') && html.includes('class="card card-fold market-against"'));
  assert.ok(!html.includes("No trade today"));
  const rules = R.renderRules();
  assert.match(rules, /default \$200/);
  assert.match(rules, /not lowered/);
  assert.match(rules, /no extra relative-strength filter/);
  assert.match(rules, /Market against you/);
  assert.match(rules, /Test rule/);
  assert.match(rules, /1\.25/);
  assert.match(rules, /1\.50/);
  assert.match(rules, /5–40 sessions \(was 10–40\)/);
  assert.match(rules, /last half/);
});

import { emptyDay, freshness, plainGateFailure, prevTradingDay } from "../js/logic.js";

test("freshness: current data, failed run, and out-of-date data", () => {
  const data = { entrySession: "2026-11-09", generatedAtPT: "2026-11-09 06:02 AM PT" };
  const ok = { lastRunResult: "success", lastSuccessPT: "2026-11-09 06:02 AM PT" };
  assert.equal(freshness(ok, data, "2026-11-09", 8 * 60).state, "ok");
  assert.equal(freshness(ok, { ...data, entrySession: "2026-11-06" }, "2026-11-08", 600).state, "ok"); // weekend: Friday's ideas still current
  assert.equal(freshness(ok, { ...data, entrySession: "2026-11-06" }, "2026-11-09", 5 * 60).state, "ok"); // Monday before the run
  const stale = freshness(ok, { ...data, entrySession: "2026-11-06" }, "2026-11-09", 8 * 60);
  assert.equal(stale.state, "failed");
  assert.match(stale.message, /Last successful update: 2026-11-09 06:02 AM PT/);
  const failed = { lastRunResult: "failed", lastRunPT: "2026-11-10 06:00 AM PT", lastError: "boom", lastSuccessPT: "2026-11-09 06:02 AM PT" };
  const f = freshness(failed, data, "2026-11-10", 8 * 60);
  assert.equal(f.state, "failed");
  assert.match(f.message, /failed .*boom.*2026-11-09 06:02 AM PT.*out of date/);
  assert.equal(freshness(failed, { ...data, entrySession: "2026-11-10" }, "2026-11-10", 8 * 60).state, "warn");
  assert.equal(freshness(null, null, "2026-11-10", 0).state, "failed");
  assert.equal(prevTradingDay("2026-11-30"), "2026-11-27");
});

test("an empty day says why, at the top, and a full day does not", () => {
  const data = {
    generatedAtPT: "2026-11-08 10:34 PM PT", signalSession: "2026-11-06", entrySession: "2026-11-09", entryWindowPT: "7:00 AM – 12:30 PM PT",
    gate: { checks: [
      { name: "Market: SPY above a rising 50-day average", pass: false, value: "SPY 735.29, 50-day 765.15 (10 sessions ago 766.90)" },
      { name: "US Stock Box market_ok", pass: false, value: "market_ok = no on 31 buy setups" },
      { name: "US Stock Box prices are from the last completed session", pass: true, value: "same session" },
    ] },
    noTradeToday: true,
    funnel: { tickers: 515, withFreshCandles: 515, stockRulesPass: 0, earningsPass: 0, withContracts: 0 },
    suggestions: [], blocked: [], nearMisses: [],
  };
  const day = emptyDay(data);
  assert.match(day.lead, /every other rule/);
  assert.match(day.lead, /market is weak/);
  assert.match(day.reasons[0], /SPY \(735\.29\) is below its 50-day average \(765\.15\)/);
  assert.match(day.reasons[0], /that average is falling/);
  assert.match(day.reasons[1], /market_ok is no/);
  assert.equal(day.funnel, "0 of 515 stocks passed the stock rules.");
  assert.match(day.note, /not a broken page/);
  const html = R.renderToday(data, S, 1000, { state: "ok", message: "" });
  assert.ok(html.indexOf('id="empty-day"') < html.indexOf("Market check"));
  assert.match(html, /No ideas today/);
  assert.ok(!html.includes("Out of date"));
  assert.match(plainGateFailure({ value: "SPY 10, 50-day 9 (10 sessions ago 8)" }), /not above its 50-day average/);
  assert.match(plainGateFailure({ value: "SPY 10, 50-day 11 (10 sessions ago 10)" }), /not rising/);
  const stale = R.renderToday(data, S, 1000, { state: "failed", message: "Data is out of date." });
  assert.ok(stale.indexOf("Out of date") < stale.indexOf('id="empty-day"'));
  assert.equal(emptyDay({ noTradeToday: false, suggestions: [{}] }), null);
});

test("Setting up watch list is separate, has the alert note, and suggests no contract", () => {
  const row = {
    ticker: "CME", company: "CME Group", sector: "Financials", close: 270, pivot: 272.5,
    pctBelowPivot: 0.92, baseLength: 12, depthPct: 8.4, volumeNeeded: 1500000, status: "waiting",
    nextEarnings: "2027-02-01", earningsSoon: false, earningsNote: "ok",
    note: "Not a trade yet. Set a TradingView alert at $272.50.",
    marketAgainst: true, marketNote: "The market is weak, so breakouts fail more often.",
  };
  const html = R.renderSetups([row]);
  assert.match(html, /id="setting-up"/);
  assert.match(html, /Setting up \(1\)/);
  assert.match(html, /Set a TradingView alert at \$272\.50/);
  assert.match(html, /Not a trade until it closes above it on strong volume/);
  assert.match(html, /1,500,000 shares/);
  assert.match(html, /\$2\.50 below breakout/);
  assert.match(html, /Market against you/);
  assert.ok(!html.includes("log this trade"));
  assert.ok(!html.includes("data-log"));
  const unknown = R.renderSetup({ ...row, planNote: "No contract: earnings date unknown" }, S, 1000);
  assert.match(unknown, /None yet, because earnings date is unknown/);
  assert.match(unknown, /No contract yet: earnings date is unknown/);
  assert.match(unknown, /Waiting: earnings date is unknown/);
  const planned = {
    role: "Balanced", symbol: "CME261218C00300000", expiry: "2026-12-18", strike: 300, qty: 1, bid: 1.10, ask: 1.20,
    estimatedAskAtPivot: 1.55, maxLoss: 156.3, breakevenAtExpiry: 301.56, dte: 39, passes: true,
    greeks: { delta: 0.42, deltaDollarsPer1: 42, gamma: 0.02, thetaDollarsPerDay: -8.1, vegaDollarsPerPoint: 15 }, deltaEvaluatedAt: "pivot", optionProfitTargetBid: 4.66, expiryExitDate: "2026-11-27",
    whyThisContract: "Why this one: the $300 call is a middle-speed pick (delta 0.42).",
    greeksPlain: {
      delta: "If the stock rises $1, this option gains about $42.00.",
      gamma: "Delta grows about 0.02 per $1 move (about $2.00 more gain on the next dollar).",
      theta: "Loses about $8.10 per day just from time passing.",
      vega: "Gains or loses about $15.00 if expected volatility moves 1 point.",
    },
    howFar: "Needs the stock to rise $29.06 (10.7%) from the pivot by 2026-12-18 just to break even.",
    exitPlan: "Sell the whole position (the Floor does not scale out) if the option bid reaches $4.66 (about 3× the planned debit).",
    maxProfit: "No fixed limit (a long call can keep rising with the stock)",
    whatIf: [{ when: "About 1 week", stock: "Flat at the pivot", price: 272.5, value: 140, pnl: -16.3, estimate: true }],
  };
  const failer = {
    role: "Cheaper", symbol: "CME261218C00320000", expiry: "2026-12-18", strike: 320, qty: 1, bid: 0.40, ask: 0.80,
    estimatedAskAtPivot: 0.90, maxLoss: 91.3, breakevenAtExpiry: 320.91, passes: false,
    failedRule: "bid-ask spread too wide",
    failedRulePlain: "The delayed bid and ask are too far apart (more than 12% of the mid, or 5 cents).",
    howFar: "Needs the stock to rise $48.41 (17.8%) from the pivot by 2026-12-18 just to break even.",
    whyThisContract: "Cheaper: this lower-delta call",
    greeksPlain: { delta: "If the stock rises $1, this option gains about $30.00." },
  };
  const spread = {
    type: "CALL_DEBIT_SPREAD", passes: true, highlight: true, expiry: "2026-12-18",
    long: { strike: 300 }, short: { strike: 320 },
    netDebit: 0.80, todayNetDebit: 0.70, maxLoss: 82.6, maxProfit: 117.4, breakevenAtExpiry: 300.83,
    maxEntryNetDebit: 1.97,
    howFar: "Needs the stock to rise $28.33 (10.4%) from the pivot by 2026-12-18 just to break even.",
    whyThisContract: "Call debit spread: buy the 2026-12-18 $300.00 call and sell the $320.00 call",
    failedRulePlain: "",
  };
  const withC = R.renderSetup({
    ...row, planChoices: [planned, failer], planSpread: spread, earningsDisplay: "2027-02-01 · source: Yahoo Finance calendarEvents",
    planSignal: { stop: 260, pivot: 272.5, target: 285 }, levels: { timeStopDate: "2026-12-01" },
  }, S, 1000);
  assert.match(withC, /What's happening/);
  assert.match(withC, /Contract to consider/);
  assert.match(withC, /\$300\.00 call expiring Dec 18/);
  assert.match(withC, /Most you can lose \$156\.30/);
  assert.match(withC, /More details/);
  assert.match(withC, /If it breaks out: CME 2026-12-18 \$300\.00 CALL × 1/);
  assert.match(withC, /Today's delayed ask/);
  assert.match(withC, /Estimated ask at pivot/);
  assert.match(withC, /\$1\.55/);
  assert.match(withC, /Skip if live ask above \$1\.98/);
  assert.match(withC, /Why this one/);
  assert.match(withC, /Delta is the speedometer: if the stock goes up \$1, this option goes up about \$42\.00/);
  assert.match(withC, /Needs the stock to rise \$29\.06/);
  assert.match(withC, /melting ice cube/);
  assert.match(withC, /puffs up or shrinks/);
  assert.ok(!withC.includes("Cheaper"));
  assert.match(withC, /<details class="card card-fold setup"/);
  assert.ok(!/<details class="card card-fold[^>]*\sopen/.test(withC), "cards start collapsed");
  assert.match(withC, /class="sum-ticker">CME</);
  assert.match(withC, /class="sum-dist">\$2\.50 below breakout</);
  assert.match(withC, /class="badge status">Setting up</);
  const summaryBadges = (withC.split("</summary>")[0].match(/class="badge /g) || []).length;
  assert.ok(summaryBadges <= 2, `collapsed row has ${summaryBadges} tags`);
  assert.match(withC, /If it breaks out: \$300\.00 call Dec 18, most you can lose \$156\.30/);
  assert.match(withC, /2027-02-01 · source: Yahoo Finance calendarEvents/);
  assert.match(withC, /Option worth about/);
  assert.ok(!withC.includes("data-log"));
  assert.match(R.renderSetup({ ...row, earningsDisplay: "2026-12-15 (estimated)", earningsEstimated: true }, S, 1000), /\(estimated\)/);
  assert.match(R.renderSetup({ ...row, planNote: "Too expensive for $200 cap" }, S, 1000), /no contract fits your \$200 limit/);
  const light = R.renderSetup({ ...row, ticker: "DGX", status: "light_volume", pctBelowPivot: -0.4, pivot: 180, note: "Not a trade yet. Set a TradingView alert at $180.00." }, S, 1000);
  assert.match(light, /volume was lighter than we require/);
  assert.match(light, /above the breakout price/);
  assert.equal(R.renderSetups([]), "");
});

test("stock cards start collapsed with a simple first view and hide over-cap quotes", () => {
  assert.equal(R.shortExpiry("2026-12-18"), "Dec 18");
  assert.equal(R.breakoutDistance(19.79, 19.86), "7 cents below breakout");
  assert.equal(R.cardShouldOpen("CME"), false);
  const sln = {
    ticker: "SLN", company: "Silence", sector: "Health Care", close: 19.79, pivot: 19.86,
    pctBelowPivot: 0.35, baseLength: 12, depthPct: 8, volumeNeeded: 100000, status: "waiting",
    nextEarnings: "2026-11-12", earningsSoon: true, marketAgainst: true,
    planNote: "No contract: next earnings 2026-11-12; must be after 2026-12-03",
    planChoices: [{
      role: "Safer", expiry: "2026-12-18", strike: 2.5, qty: 1, ask: 20, maxLoss: 2001.3,
      breakevenAtExpiry: 22.51, passes: false, failedRulePlain: "earnings too soon",
    }],
  };
  assert.equal(R.collapsedLine(sln, "setup", S), "Waiting: earnings Nov 12 is too soon");
  assert.equal(R.contractLine(sln, S), "None yet, because earnings Nov 12 is too soon.");
  assert.equal(R.lossLine(sln, S), "Doesn't apply yet");
  assert.deepEqual(R.visiblePlanChoices(sln, S), []);
  const slnHtml = R.renderSetup(sln, S, 1000);
  assert.match(slnHtml, /7 cents below breakout/);
  assert.match(slnHtml, /Waiting: earnings Nov 12 is too soon/);
  assert.match(slnHtml, /Tight pattern, just 7 cents under the breakout price \$19\.86/);
  assert.match(slnHtml, /None yet, because earnings Nov 12 is too soon/);
  assert.match(slnHtml, /Doesn(?:&#39;|')t apply yet/);
  assert.match(slnHtml, /Delayed data, research only, education not advice/);
  assert.match(slnHtml, /More details/);
  assert.ok(!slnHtml.includes("$2,001.30") && !slnHtml.includes("$2001.30"));
  assert.ok(!slnHtml.includes("$2.50 CALL"));
  const unknown = R.renderSetup({
    ticker: "CME", company: "CME Group", sector: "Financials", close: 270, pivot: 272.5,
    pctBelowPivot: 0.92, baseLength: 12, depthPct: 8.4, volumeNeeded: 1500000, status: "waiting",
    nextEarnings: "unknown", earningsSoon: true, planNote: "No contract: earnings date unknown",
  }, S, 1000);
  assert.match(unknown, /class="sum-ticker">CME</);
  assert.match(unknown, /Setting up/);
  assert.match(unknown, /Waiting: earnings date is unknown/);
  assert.ok(!/<details class="card card-fold[^>]*\sopen/.test(unknown));
  const idea = R.renderIdea({
    ticker: "XYZ", company: "Example", sector: "Technology", nextEarnings: "2027-03-01",
    signal: { date: "2026-11-09", close: 51, pivot: 50, volRatio: 1.4, baseLength: 12, liveMin: 50, liveMax: 52.5, stop: 48, target: 55 },
    contracts: [{
      expiry: "2026-12-18", strike: 50, qty: 1, symbol: "XYZ261218C00050000", bid: 1.8, ask: 1.9,
      maxLoss: 191.3, maxProfit: "unlimited", dte: 39, iv: 0.3, hv20: 0.25, hv60: 0.22, ivRatio: 1.2,
      oi: 1000, volume: 200, breakevenAtExpiry: 51.9, optionProfitTargetBid: 5.7, expiryExitDate: "2026-11-27",
      speed: 0.4, greeks: { delta: 0.5, deltaDollarsPer1: 50, thetaDollarsPerDay: -5, vegaDollarsPerPoint: 10, rhoDollarsPerPoint: 2, gamma: 0.02 },
      scenarios: [], gate: { valueAtTargetDay10: 3, needed: 2 }, model: { name: "CRR" },
    }],
    levels: { timeStopDate: "2026-12-01" },
  }, S, 1000, true);
  assert.match(idea, /card-fold/);
  assert.ok(!/<details class="card card-fold[^>]*\sopen/.test(idea), "idea cards start collapsed");
  assert.match(idea, /class="sum-ticker">XYZ</);
  assert.match(idea, /\$1\.00 above breakout/);
  assert.match(idea, /Trade idea/);
  assert.match(idea, /\$50\.00 call Dec 18, most you can lose \$191\.30/);
  assert.match(idea, /I checked TradingView — log this trade/);
});


const D = { ...S, tito_simple_card: true };

test("tito_simple_card switch uses the designed card only when it is explicitly on", () => {
  assert.equal(R.simpleCardEnabled(undefined), false);
  assert.equal(R.simpleCardEnabled(S), false);
  assert.equal(R.simpleCardEnabled({ ...S, tito_simple_card: false }), false);
  assert.equal(R.simpleCardEnabled(D), true);
  const row = {
    ticker: "CME", company: "CME Group", sector: "Financials", close: 270, pivot: 272.5,
    pctBelowPivot: 0.92, baseLength: 12, depthPct: 8.4, volumeNeeded: 1500000, status: "waiting",
    nextEarnings: "2027-02-01", earningsSoon: false,
    note: "Not a trade yet. Set a TradingView alert at $272.50.",
    marketAgainst: true,
  };
  const off = R.renderSetup(row, S, 1000);
  assert.match(off, /class="sum-ticker">CME</);
  assert.ok(!off.includes("tito-card"));
  const planned = {
    role: "Balanced", symbol: "CME261218C00300000", expiry: "2026-12-18", strike: 300, qty: 1, bid: 1.10, ask: 1.20,
    estimatedAskAtPivot: 1.55, maxLoss: 156.3, breakevenAtExpiry: 301.56, dte: 39, passes: true,
    greeks: { delta: 0.42, deltaDollarsPer1: 42, gamma: 0.02, thetaDollarsPerDay: -8.1, vegaDollarsPerPoint: 15 },
    whyThisContract: "Why this one: the $300 call is a middle-speed pick (delta 0.42).",
    exitPlan: "Sell the whole position (the Floor does not scale out) if the option bid reaches $4.66 (about 3× the planned debit). Exit if the stock closes below $260.",
    whatIf: [{ when: "About 1 week", stock: "Flat at the pivot", price: 272.5, value: 140, pnl: -16.3, estimate: true }],
  };
  const failer = {
    role: "Cheaper", expiry: "2026-12-18", strike: 320, qty: 1, ask: 0.80, maxLoss: 91.3, passes: false,
    failedRulePlain: "The delayed bid and ask are too far apart.",
  };
  const spread = {
    type: "CALL_DEBIT_SPREAD", passes: true, expiry: "2026-12-18",
    long: { strike: 300 }, short: { strike: 320 }, netDebit: 0.80, maxLoss: 82.6, maxProfit: 117.4,
  };
  const html = R.renderSetup({
    ...row, planChoices: [planned, failer], planSpread: spread,
    quoteSource: { label: "Cboe delayed quotes", quoteTime: "16:15 ET", fileTimestamp: "2026-11-10", retrievedAtPT: "1:20 PM PT" },
  }, D, 1000);
  assert.match(html, /class="card card-fold tito-card/);
  assert.match(html, /class="ticker">CME</);
  assert.match(html, /class="dist">\$2\.50 below breakout</);
  assert.match(html, /class="tag setup">Setting up</);
  assert.match(html, /class="tag against">Market against you</);
  assert.match(html, /If it breaks out: \$300\.00 call Dec 18, most you can lose \$156\.30/);
  assert.match(html, /Plan only, not a trade/);
  assert.match(html, /What's happening/);
  assert.match(html, /Tight pattern, just \$2\.50 under the breakout price <strong>\$272\.50<\/strong>/);
  assert.match(html, /<strong>\$300\.00<\/strong> call expiring Dec 18/);
  assert.match(html, /Most you can lose <strong>\$156\.30<\/strong>/);
  assert.match(html, /Watch for a close above <strong>\$272\.50<\/strong> on strong volume/);
  assert.ok(!html.includes("TradingView"));
  assert.ok(!html.includes("Set a TradingView alert"));
  assert.ok(!html.includes("data-log"));
  assert.ok(!html.includes("Cheaper"));
  assert.ok(!html.includes("Safer"));
  assert.ok(!html.includes("CALL_DEBIT_SPREAD"));
  assert.ok(!html.includes("$320.00 call") && !html.includes("buy the 2026-12-18"));
  assert.match(html, /More details/);
  assert.match(html, /class="more-btn"/);
  assert.match(html, /CME \$300\.00 call, expiring Dec 18, 2026/);
  assert.match(html, /Think of delta as a speedometer/);
  assert.match(html, /If CME rises <strong>\$1<\/strong>, this call is worth about <strong>\$42\.00<\/strong> more/);
  assert.match(html, /melting ice cube/);
  assert.match(html, /Greeks, in plain dollars/);
  assert.match(html, /Money at stake/);
  assert.match(html, /What if the stock moves\?/);
  assert.match(html, /Estimated value/);
  assert.match(html, /Exit plan/);
  assert.match(html, /Sources and times/);
  assert.match(html, /Delayed data, research only, education not advice/);
  const listed = R.renderSetups([{ ...row, planChoices: [planned] }, { ...row, ticker: "SLN", marketAgainst: false }], D, 1000);
  assert.match(listed, /class="market-line"/);
  assert.match(listed, /The broad market is weak, so breakouts fail more often/);
  assert.match(listed, /Tap a row for four plain lines/);
  const sln = {
    ticker: "SLN", company: "Silence", sector: "Health Care", close: 19.79, pivot: 19.86,
    pctBelowPivot: 0.35, baseLength: 12, depthPct: 8, volumeNeeded: 100000, status: "waiting",
    nextEarnings: "2026-11-12", earningsSoon: true, marketAgainst: true,
    planNote: "No contract: next earnings 2026-11-12; must be after 2026-12-03",
    planChoices: [{ role: "Safer", expiry: "2026-12-18", strike: 2.5, qty: 1, ask: 20, maxLoss: 2001.3, passes: false }],
  };
  const slnHtml = R.renderSetup(sln, D, 1000);
  assert.match(slnHtml, /class="choice wait"/);
  assert.match(slnHtml, /Waiting: earnings Nov 12 is too soon/);
  assert.match(slnHtml, /<span class="amber">No contract yet,<\/span> because earnings Nov 12 is too soon/);
  assert.match(slnHtml, /Does not apply yet/);
  assert.match(slnHtml, /Watch for a close above <strong>\$19\.86<\/strong> on strong volume/);
  assert.match(slnHtml, /Wait until earnings are far enough away before planning a contract/);
  assert.match(slnHtml, /class="none-title">No contract yet</);
  assert.match(slnHtml, /The Nov 12 earnings date is too soon/);
  assert.match(slnHtml, /expire after earnings are at least 15 trading days away/);
  assert.ok(!slnHtml.includes("$2,001.30") && !slnHtml.includes("$2001.30"));
  assert.ok(!slnHtml.includes("TradingView"));
  const unknown = R.renderSetup({
    ticker: "PCRX", close: 20, pivot: 21, status: "waiting", earningsSoon: true,
    planNote: "No contract: earnings date unknown",
  }, D, 1000);
  assert.match(unknown, /earnings date is unknown/);
  assert.match(unknown, /The next earnings date is unknown/);
  const idea = R.renderIdea({
    ticker: "XYZ", company: "Example", sector: "Technology", nextEarnings: "2027-03-01",
    signal: { date: "2026-11-09", close: 51, pivot: 50, volRatio: 1.4, baseLength: 12, liveMin: 50, liveMax: 52.5, stop: 48, target: 55 },
    contracts: [{
      expiry: "2026-12-18", strike: 50, qty: 1, ask: 1.9, maxLoss: 191.3, dte: 39,
      breakevenAtExpiry: 51.9,
      greeks: { delta: 0.5, deltaDollarsPer1: 50, thetaDollarsPerDay: -5, vegaDollarsPerPoint: 10, gamma: 0.02 },
    }],
  }, D, 1000, true);
  assert.match(idea, /class="tag setup">Trade idea</);
  assert.match(idea, /Watch for a close above <strong>\$50\.00<\/strong> on strong volume/);
  assert.ok(!idea.includes("TradingView"));
  assert.ok(!idea.includes("data-log"));
  assert.ok(!idea.includes("I checked TradingView"));
});
