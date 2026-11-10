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
  assert.ok(html.includes('class="badge market"') && html.includes('class="card market-against"'));
  assert.ok(!html.includes("No trade today"));
  const rules = R.renderRules();
  assert.match(rules, /default \$200/);
  assert.match(rules, /not lowered/);
  assert.match(rules, /no extra relative-strength filter/);
  assert.match(rules, /Market against you/);
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
