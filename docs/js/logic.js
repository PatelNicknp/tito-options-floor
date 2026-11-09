// Pure rules for the Tito Options Floor page. No network, no storage, no trading.
// Every fill in this app is typed by Nick from TradingView. Nothing here creates a fill by itself.

export const ENTRY_START_MIN = 7 * 60;        // 7:00 AM PT
export const ENTRY_END_MIN = 12 * 60 + 30;    // 12:30 PM PT
export const MARKET_OPEN_MIN = 6 * 60 + 30;   // 6:30 AM PT
export const MARKET_CLOSE_MIN = 13 * 60;      // 1:00 PM PT
export const MAX_OPEN = 3;
export const EARNINGS_TRADING_DAYS = 15;
export const LIVE_CHASE_MAX = 0.05;
export const FILL_LABEL = "Nick-confirmed fill (TradingView)";
export const DEFAULT_SETTINGS = Object.freeze({ capDollars: 200, feePerContract: 0.65, startingCash: 1000 });

const HOLIDAYS = new Set([
  "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
  "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
  "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
]);

const iso = (d) => d.toISOString().slice(0, 10);
const parseDay = (s) => new Date(`${s}T00:00:00Z`);
const cents = (x) => Math.round(x * 100);

export function isTradingDay(day) {
  const d = parseDay(day);
  const wd = d.getUTCDay();
  return wd !== 0 && wd !== 6 && !HOLIDAYS.has(day);
}

export function nextTradingDay(day) {
  const d = parseDay(day);
  do { d.setUTCDate(d.getUTCDate() + 1); } while (!isTradingDay(iso(d)));
  return iso(d);
}

export function addTradingDays(day, n) {
  let out = day;
  for (let i = 0; i < n; i++) out = nextTradingDay(out);
  return out;
}

export function tradingDaysBetween(start, end) {
  let count = 0, cur = start;
  while (cur < end) { cur = nextTradingDay(cur); if (cur <= end) count++; }
  return count;
}

export function calendarDaysBetween(start, end) {
  return Math.round((parseDay(end) - parseDay(start)) / 86400000);
}

/** X = (cap - open fee - close fee) / (100 * qty), rounded down to the cent; cap never above cash. */
export function maxEntryAsk(cap, feePerContract, qty, cash) {
  if (!Number.isInteger(qty) || qty < 1) return null;
  const room = Math.min(cap, cash) - 2 * feePerContract * qty;
  if (!(room > 0)) return null;
  const x = Math.floor((room / (100 * qty)) * 100 + 1e-9) / 100;
  return x > 0 ? x : null;
}

/** "2026-11-09T07:45" typed as Pacific Time. */
export function parsePT(value) {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})$/.exec(value || "");
  if (!m) return null;
  const minutes = Number(m[2]) * 60 + Number(m[3]);
  if (Number(m[2]) > 23 || Number(m[3]) > 59) return null;
  return { date: m[1], minutes };
}

export function openPositions(log) {
  return (log || []).filter((e) => e.status === "open");
}

export function cashAvailable(settings, log) {
  let cash = Number(settings.startingCash);
  for (const e of log || []) {
    cash -= e.entry.fill * 100 * e.entry.qty + e.entry.fees;
    if (e.exit) cash += e.exit.fill * 100 * e.exit.qty - e.exit.fees;
  }
  return Math.round(cash * 100) / 100;
}

const num = (v) => (v === "" || v === null || v === undefined ? NaN : Number(v));

/** Check an opening (buy to open) entry. Returns a list of plain-language problems; empty means OK. */
export function checkEntry(form, settings, log) {
  const errors = [];
  const ticker = String(form.ticker || "").trim().toUpperCase();
  const qty = num(form.qty), bid = num(form.bid), ask = num(form.ask), fill = num(form.fill), fees = num(form.fees);
  const strike = num(form.strike), stock = num(form.stockPrice), pivot = num(form.pivot);
  if (!/^[A-Z][A-Z.\-]{0,6}$/.test(ticker)) errors.push("Enter the stock ticker.");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.expiry || "")) errors.push("Enter the contract expiry date.");
  if (!(strike > 0)) errors.push("Enter the strike price.");
  if (form.optionType !== "CALL") errors.push("Only long calls are allowed in this module.");
  if (form.side !== "BUY_TO_OPEN") errors.push("Opening trades must be 'Buy to open'.");
  if (!Number.isInteger(qty) || qty < 1) errors.push("Quantity must be a whole number of contracts (1 or more).");
  if (!(bid > 0)) errors.push("Enter the live bid you saw. It must be above $0.");
  if (!(ask >= bid)) errors.push("The ask you saw must be at or above the bid.");
  if (!(fill >= 0) || cents(fill) !== cents(ask)) errors.push("Buy price must equal the ask you saw (rule: buy at the ask).");
  if (!(fees >= 0)) errors.push("Enter the fees (0 or more).");
  if (form.source !== "TradingView") errors.push("Source must be TradingView.");
  if (form.dataStatus === "delayed") errors.push("TradingView showed delayed data. Delayed quotes can't be logged as fills.");
  else if (form.dataStatus !== "realtime") errors.push("Confirm that TradingView showed real-time data (no 'Data is delayed' message).");
  const seen = parsePT(form.timeSeen);
  if (!seen) errors.push("Enter the time you saw the price (Pacific Time).");
  else {
    if (!isTradingDay(seen.date)) errors.push("That date is not a US market trading day.");
    if (seen.minutes < ENTRY_START_MIN || seen.minutes > ENTRY_END_MIN) errors.push("Entries are only allowed between 7:00 AM and 12:30 PM PT.");
    if (/^\d{4}-\d{2}-\d{2}$/.test(form.expiry || "") && form.expiry <= seen.date) errors.push("The expiry must be after the entry date.");
  }
  if (!(pivot > 0)) errors.push("Enter the pivot (base high) so the chase limit can be checked.");
  if (!(stock > 0)) errors.push("Enter the live stock price you saw.");
  else if (pivot > 0) {
    if (stock < pivot) errors.push(`Skip: the stock (${stock}) is below the pivot (${pivot}).`);
    if (stock > pivot * (1 + LIVE_CHASE_MAX) + 1e-9) errors.push(`Skip: the stock is more than 5% above the pivot (limit ${(pivot * 1.05).toFixed(2)}).`);
  }
  const cash = cashAvailable(settings, log);
  if (qty >= 1 && fill >= 0 && fees >= 0) {
    const cost = fill * 100 * qty + fees;
    const risk = cost + settings.feePerContract * qty;
    const cap = Math.min(settings.capDollars, cash);
    if (risk > cap + 1e-9) errors.push(`Over the limit: premium plus fees $${risk.toFixed(2)} is above your max loss of $${cap.toFixed(2)}.`);
    if (cost > cash + 1e-9) errors.push(`Not enough paper cash: needs $${cost.toFixed(2)}, you have $${cash.toFixed(2)}.`);
  }
  const open = openPositions(log);
  if (open.length >= MAX_OPEN) errors.push("You already have 3 open positions (the limit).");
  const sector = String(form.sector || "").trim();
  if (!sector) errors.push("Enter the stock's sector.");
  else if (open.some((e) => e.sector === sector)) errors.push(`You already hold a ${sector} position (1 per sector).`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.nextEarnings || "")) errors.push("Next earnings date is unknown, so this trade is not allowed.");
  else if (seen) {
    const limit = addTradingDays(seen.date, EARNINGS_TRADING_DAYS);
    if (form.nextEarnings <= limit) errors.push(`Earnings on ${form.nextEarnings} are within 15 trading days (must be after ${limit}).`);
  }
  return errors;
}

/** Build the stored record. Throws if any check fails, so no unchecked fill can be saved. */
export function buildEntry(form, settings, log, id) {
  const errors = checkEntry(form, settings, log);
  if (errors.length) throw new Error(errors.join(" "));
  const seen = parsePT(form.timeSeen);
  return {
    id,
    status: "open",
    fillType: "nick-confirmed",
    fillLabel: FILL_LABEL,
    ticker: String(form.ticker).trim().toUpperCase(),
    contract: { type: "CALL", expiry: form.expiry, strike: num(form.strike) },
    sector: String(form.sector).trim(),
    nextEarnings: form.nextEarnings,
    plan: {
      pivot: num(form.pivot), stop: num(form.stop) || null, target: num(form.target) || null,
      signalSession: form.signalSession || null, suggestionId: form.suggestionId || null,
    },
    capAtEntry: settings.capDollars,
    entry: {
      side: "BUY_TO_OPEN", qty: num(form.qty), bid: num(form.bid), ask: num(form.ask), fill: num(form.fill),
      fees: num(form.fees), stockPrice: num(form.stockPrice), timeSeenPT: form.timeSeen, date: seen.date,
      source: "TradingView", dataStatus: "realtime", screenshotNote: String(form.screenshotNote || ""),
    },
    exit: null,
  };
}

/** Check a closing (sell to close) entry for an open position. Whole position only. */
export function checkExit(form, position) {
  const errors = [];
  const qty = num(form.qty), bid = num(form.bid), ask = num(form.ask), fill = num(form.fill), fees = num(form.fees);
  if (!position || position.status !== "open") errors.push("Pick an open position.");
  if (form.side !== "SELL_TO_CLOSE") errors.push("Exits must be 'Sell to close'.");
  if (position && qty !== position.entry.qty) errors.push(`Sell the whole position: ${position.entry.qty} contract(s).`);
  if (!(bid > 0)) errors.push("Enter the live bid you saw. It must be above $0.");
  if (!(ask >= bid)) errors.push("The ask you saw must be at or above the bid.");
  if (!(fill >= 0) || cents(fill) !== cents(bid)) errors.push("Sell price must equal the bid you saw (rule: sell at the bid).");
  if (!(fees >= 0)) errors.push("Enter the fees (0 or more).");
  if (form.source !== "TradingView") errors.push("Source must be TradingView.");
  if (form.dataStatus === "delayed") errors.push("TradingView showed delayed data. Delayed quotes can't be logged as fills.");
  else if (form.dataStatus !== "realtime") errors.push("Confirm that TradingView showed real-time data (no 'Data is delayed' message).");
  if (!form.reason) errors.push("Pick the exit reason.");
  const seen = parsePT(form.timeSeen);
  if (!seen) errors.push("Enter the time you saw the price (Pacific Time).");
  else {
    if (!isTradingDay(seen.date)) errors.push("That date is not a US market trading day.");
    if (seen.minutes < MARKET_OPEN_MIN || seen.minutes > MARKET_CLOSE_MIN) errors.push("Exits must be during market hours, 6:30 AM to 1:00 PM PT.");
    if (position && form.timeSeen <= position.entry.timeSeenPT) errors.push("The exit time must be after the entry time.");
  }
  return errors;
}

export function buildExit(form, position) {
  const errors = checkExit(form, position);
  if (errors.length) throw new Error(errors.join(" "));
  const exit = {
    side: "SELL_TO_CLOSE", qty: num(form.qty), bid: num(form.bid), ask: num(form.ask), fill: num(form.fill),
    fees: num(form.fees), timeSeenPT: form.timeSeen, source: "TradingView", dataStatus: "realtime",
    reason: form.reason, screenshotNote: String(form.screenshotNote || ""),
  };
  const pnl = exit.fill * 100 * exit.qty - exit.fees - (position.entry.fill * 100 * position.entry.qty + position.entry.fees);
  return { ...position, status: "closed", exit, realizedPnl: Math.round(pnl * 100) / 100 };
}

/** Reminders only. The page never sells anything; Nick decides and logs the exit himself. */
export function exitFlags(position, mark, today, optionBid, feePerContract) {
  const flags = [];
  const p = position.plan || {};
  if (mark && mark.date < position.entry.date) flags.push("Waiting for the first daily close after your entry.");
  else if (mark) {
    if (p.stop && mark.close < p.stop) flags.push("Stop: daily close below the breakout-day low.");
    if (p.pivot && tradingDaysBetween(position.entry.date, mark.date) <= 3 && mark.close < p.pivot) flags.push("Failed breakout: close back below the pivot in the first 3 sessions.");
    if (p.target && mark.close >= p.target) flags.push("Target: daily close at or above the target.");
    const half = p.target ? position.entry.stockPrice + (p.target - position.entry.stockPrice) / 2 : null;
    if (half && mark.close >= half) { if (mark.sma10 && mark.close < mark.sma10) flags.push("Trend trail: close below the 10-day average (past halfway to target)."); }
    else if (mark.sma20 && mark.close < mark.sma20) flags.push("Trend trail: close below the 20-day average.");
  }
  if (tradingDaysBetween(position.entry.date, today) >= 15) flags.push("Time stop: 15 trading days since entry.");
  if (calendarDaysBetween(today, position.contract.expiry) <= 21) flags.push("Expiry: 21 calendar days or fewer left. Sell; never exercise.");
  if (position.nextEarnings && position.nextEarnings <= nextTradingDay(today)) flags.push("Earnings: sell in the last session before the report.");
  const bid = Number(optionBid);
  if (bid > 0) {
    const cost = position.entry.fill * 100 * position.entry.qty + position.entry.fees;
    if (bid * 100 * position.entry.qty - feePerContract * position.entry.qty >= 3 * cost) flags.push("Option target: bid value is at least 3x your entry cost.");
  }
  return flags;
}

/** Validate an imported log file. Only Nick-confirmed TradingView records are accepted. */
export function checkImport(obj) {
  if (!obj || obj.app !== "tito-options-floor" || !Array.isArray(obj.log)) return ["This is not a Tito Options Floor export file."];
  const errors = [];
  obj.log.forEach((e, i) => {
    if (e.fillType !== "nick-confirmed" || e.entry?.source !== "TradingView") errors.push(`Row ${i + 1} is not a Nick-confirmed TradingView fill.`);
    if (e.exit && e.exit.source !== "TradingView") errors.push(`Row ${i + 1} exit is not from TradingView.`);
  });
  return errors;
}

export function prevTradingDay(day) {
  const d = parseDay(day);
  do { d.setUTCDate(d.getUTCDate() - 1); } while (!isTradingDay(iso(d)));
  return iso(d);
}

/** One failed market check, in a sentence. The numbers come from the data file; the rules are unchanged. */
export function plainGateFailure(check) {
  const value = String(check?.value || "");
  const spy = value.match(/SPY ([\d.]+), 50-day ([\d.]+) \(10 sessions ago ([\d.]+)\)/);
  if (spy) {
    const px = Number(spy[1]);
    const sma = Number(spy[2]);
    const prev = Number(spy[3]);
    const where = px < sma
      ? `SPY (${spy[1]}) is below its 50-day average (${spy[2]})`
      : `SPY (${spy[1]}) is not above its 50-day average (${spy[2]})`;
    const slope = sma < prev
      ? `that average is falling (it was ${spy[3]} ten sessions ago)`
      : `that average is not rising (it was ${spy[3]} ten sessions ago)`;
    return `${where}, and ${slope}.`;
  }
  if (/market_ok\s*=\s*no/i.test(value)) return "US Stock Box market_ok is no.";
  const name = check?.name || "A check";
  return value ? `${name}: ${value}.` : `${name}.`;
}

/** Why today's list is empty, or null when there are ideas. */
export function emptyDay(data) {
  if (!data?.noTradeToday) return null;
  const failed = (data.gate?.checks || []).filter((c) => !c.pass);
  const funnel = data.funnel || {};
  const checked = Number(funnel.tickers || 0);
  const passed = Number(funnel.stockRulesPass || 0);
  const session = data.entrySession || "this";
  return {
    title: "No ideas today",
    lead: failed.length
      ? `No ideas for the ${session} session because the market check failed, so this page suggests no trades.`
      : `No ideas for the ${session} session. No stock and contract passed every rule.`,
    reasons: failed.map(plainGateFailure),
    funnel: `${passed} of ${checked} stocks passed the stock rules.`,
    note: "No trade today. This is a finished result, not a broken page. Waiting is a valid result.",
  };
}

// Is the published data current? today = PT date "YYYY-MM-DD", minutes = PT minutes since midnight.
// The daily run is about 6:00 AM PT; after 7:00 AM PT on a trading day the ideas must be for today.
export function freshness(status, data, today, minutes) {
  const last = status?.lastSuccessPT || data?.generatedAtPT || "unknown";
  if (!data) return { state: "failed", message: `No data file. Last successful update: ${last}.` };
  const need = isTradingDay(today) && minutes >= 7 * 60 ? today : prevTradingDay(today);
  const stale = data.entrySession < need;
  if (status?.lastRunResult === "failed") {
    return { state: stale ? "failed" : "warn", message: `The latest automatic update failed (${status.lastRunPT}): ${(status.lastError || "unknown error").replace(/\.$/, "")}. Showing the last good data from ${last}${stale ? ", which is out of date — do not use these ideas for today" : ""}. It retries automatically.` };
  }
  if (stale) return { state: "failed", message: `Data is out of date: ideas are for ${data.entrySession}, but today's update has not arrived. Last successful update: ${last}. Do not use these ideas for today.` };
  return { state: "ok", message: "" };
}
