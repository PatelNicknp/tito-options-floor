// Pure HTML builders (strings). Every price view carries the DELAYED label and its times.
import { emptyDay, maxEntryAsk } from "./logic.js";

export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "n/a" : `${Number(v) < 0 ? "−" : ""}$${Math.abs(Number(v)).toFixed(2)}`);
const signed = (v) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(2)}`;

export function delayedBadge() {
  return `<span class="badge delayed">DELAYED</span> <span class="badge research">Research only</span>`;
}

export function quoteTimes(src) {
  if (!src) return `<p class="times">Quote time not provided.</p>`;
  return `<p class="times">${delayedBadge()} Source: <a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.label)}</a> ·
    Quote time: ${esc(src.quoteTime || "quote time not provided")} · Cboe file stamp: ${esc(src.fileTimestamp || "not provided")} ·
    Retrieved: ${esc(src.retrievedAtPT || "not provided")}</p>`;
}

export function skipLine(contract, settings, cash) {
  const x = maxEntryAsk(settings.capDollars, settings.feePerContract, 1, cash);
  if (x === null) return `<p class="skip over">Your max loss setting leaves no room for a contract after fees.</p>`;
  const ask = contract.estimatedAskAtPivot != null ? contract.estimatedAskAtPivot : contract.ask;
  const over = ask > x;
  return `<p class="skip${over ? " over" : ""}"><strong>Skip if live ask above ${money(x)}</strong> (1 contract, max loss ${money(Math.min(settings.capDollars, cash))} incl. fees).
    ${over ? `The ask ${money(ask)} is already above this, so skip unless the live ask is lower.` : ""}</p>`;
}

export function renderContract(c, idea, settings, cash, canLog) {
  const g = c.greeks || {};
  const grid = (c.scenarios || []).filter((s) => s.ivShift === 0)
    .map((s) => `<tr><td>${esc(s.stock)} (${money(s.price)})</td><td>day ${s.tradingDay}</td><td>${money(s.value)}</td><td class="${s.pnl >= 0 ? "good" : "bad"}">${signed(s.pnl)}</td></tr>`).join("");
  return `<div class="contract">
    <h4>${esc(idea.ticker)} ${esc(c.expiry)} $${esc(c.strike)} CALL <span class="sym">${esc(c.symbol)}</span></h4>
    ${skipLine(c, settings, cash)}
    <dl class="facts">
      <div><dt>Delayed bid / ask</dt><dd>${money(c.bid)} / ${money(c.ask)}</dd></div>
      <div><dt>Days to expiry</dt><dd>${esc(c.dte)}</dd></div>
      <div><dt>Most you can lose</dt><dd>${money(c.maxLoss)} (premium + fees)</dd></div>
      <div><dt>Most you can make</dt><dd>${esc(c.maxProfit)}</dd></div>
      <div><dt>Break-even at expiry</dt><dd>${money(c.breakevenAtExpiry)} stock price</dd></div>
      <div><dt>Profit target (option)</dt><dd>Sell if bid reaches ${money(c.optionProfitTargetBid)}</dd></div>
      <div><dt>Price check</dt><dd>IV ${(c.iv * 100).toFixed(1)}% vs realized ${(Math.max(c.hv20 || 0, c.hv60 || 0) * 100).toFixed(1)}% (${c.ivRatio}x, limit 1.3x)</dd></div>
      <div><dt>Open interest / volume</dt><dd>${esc(c.oi)} / ${esc(c.volume)}</dd></div>
    </dl>
    <p class="greeks small"><strong>Greeks per contract (model, not market):</strong> delta ${g.delta} (about ${money(g.deltaDollarsPer1)} per $1 stock move) ·
      theta ${money(g.thetaDollarsPerDay)} per day · vega ${money(g.vegaDollarsPerPoint)} per 1 IV point · rho ${money(g.rhoDollarsPerPoint)} per 1 rate point · gamma ${g.gamma}</p>
    <details><summary>What if the stock moves? (estimates only)</summary>
      <div class="table-wrap"><table class="grid"><tr><th>Stock</th><th>When</th><th>Contract value</th><th>Profit / loss</th></tr>${grid}</table></div>
      <p class="small">Model estimates with IV unchanged. Speed check ${c.speed} (limit 1.0). Day-10 target check ${money(c.gate?.valueAtTargetDay10)} vs needed ${money(c.gate?.needed)}. ${esc(c.model?.name)}.</p>
    </details>
    <p class="small">Exit plan: sell the whole position on the first of: daily close below ${money(idea.signal.stop)} (stop) · close below the pivot ${money(idea.signal.pivot)} in the first 3 sessions · close at or above ${money(idea.signal.target)} (target) · close below the 20-day average · ${esc(idea.levels?.timeStopDate)} (15 trading days) · ${esc(c.expiryExitDate)} (21 days before expiry). Ex-dividend: not checked.</p>
    ${canLog ? `<button class="primary" data-log="${esc(idea.ticker)}|${esc(c.symbol)}">I checked TradingView — log this trade</button>` : ""}
  </div>`;
}

// Tweakable card copy and layout. Change these (or the helper functions below)
// if Nick wants fewer lines, different tags, or Greeks moved.
export const CARD_LAYOUT = {
  maxTags: 2,
  simpleFooter: "Delayed data, research only, education not advice",
  moreDetailsLabel: "More details",
  defaultCap: 200,
};

export function queryFlag(name) {
  try { return new URLSearchParams(location.search).get(name); } catch { return null; }
}

export function flagMatches(name, ticker) {
  const v = queryFlag(name);
  if (v == null || v === "" || v === "0" || v === "false") return false;
  if (v === "1" || v === "true" || v === "all") return true;
  return String(ticker || "").toUpperCase() === String(v).toUpperCase();
}

export function expandParam() { return queryFlag("expand"); }
export function cardShouldOpen(ticker) { return flagMatches("expand", ticker); }
export function detailsShouldOpen(ticker) { return flagMatches("details", ticker); }

export function shortExpiry(iso) {
  if (!iso) return "";
  const parts = String(iso).slice(0, 10).split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const mi = Number(parts[1]) - 1;
  if (!parts[2] || mi < 0 || mi > 11) return iso;
  return `${months[mi]} ${Number(parts[2])}`;
}

export function capDollars(settings) {
  const n = Number(settings && settings.capDollars);
  return Number.isFinite(n) ? n : CARD_LAYOUT.defaultCap;
}

export function isOverCap(c, settings) {
  return !!(c && c.maxLoss != null && Number(c.maxLoss) > capDollars(settings) + 1e-9);
}

export function isInsaneQuote(c, row) {
  if (!c || c.ask == null) return false;
  const spot = Number(row && (row.close != null ? row.close : row.signal && row.signal.close));
  return Number.isFinite(spot) && Number(c.ask) > spot + 0.01;
}

export function visiblePlanChoices(row, settings) {
  const raw = (row.planChoices && row.planChoices.length) ? row.planChoices : (row.plannedContracts || []);
  return raw.filter((c) => c && !isOverCap(c, settings) && !isInsaneQuote(c, row));
}

export function bestPassingChoice(row, settings) {
  const planned = visiblePlanChoices(row, settings).filter((c) => c.passes === true);
  if (planned.length) return planned.find((c) => c.role === "Balanced") || planned[0];
  const ideas = (row.contracts || []).filter((c) => c && c.passes !== false && !isOverCap(c, settings) && !isInsaneQuote(c, row));
  return ideas[0] || null;
}

export function breakoutDistance(close, pivot) {
  const diff = Number(pivot) - Number(close);
  if (!Number.isFinite(diff)) return "";
  if (Math.abs(diff) < 0.005) return "at the breakout price";
  const amt = Math.abs(diff) < 1 ? `${Math.round(Math.abs(diff) * 100)} cents` : money(Math.abs(diff));
  return diff > 0 ? `${amt} below breakout` : `${amt} above breakout`;
}

export function distanceText(close, pivot, pctBelowPivot) {
  return breakoutDistance(close, pivot) || "";
}

export function shortWaitingReason(row, settings) {
  const capTxt = `your $${capDollars(settings).toFixed(0)} limit`;
  const note = String((row && (row.planNote || row.contractNote)) || "");
  if (/earnings date unknown/i.test(note) || (row && !row.nextEarnings && row.earningsSoon)) {
    return "earnings date is unknown";
  }
  const iso = (row && row.nextEarnings) || (note.match(/20\d{2}-\d{2}-\d{2}/) || [])[0];
  if (/must be after|within 15|too soon|next earnings/i.test(note)) {
    return iso ? `earnings ${shortExpiry(iso)} is too soon` : "earnings are too soon";
  }
  if (/fits your|Too expensive|over the dollar cap|dollar cap/i.test(note)) {
    return `no contract fits ${capTxt}`;
  }
  if (/Bid-ask|too wide/i.test(note)) return "the delayed bid and ask are too far apart";
  if (/Open interest|volume < 50|not liquid/i.test(note)) return "the listed calls are not liquid enough";
  if (/Delta outside/i.test(note)) return "no listed call sits in the delta range we allow";
  if (/Speed check/i.test(note)) return "the move to the target is too large for these options";
  if (/ask above the stock|far above model|quotes look/i.test(note)) return "the delayed quotes look unusable";
  const cleaned = note.replace(/^No contract:\s*/i, "").replace(/^No listed call passed every rule\.?\s*/i, "");
  return cleaned || "no listed call passed every rule";
}

export function collapsedLine(row, kind, settings) {
  const pick = bestPassingChoice(row, settings);
  if (pick) {
    const exp = shortExpiry(pick.expiry);
    const bit = `${money(pick.strike)} call${exp ? ` ${exp}` : ""}`;
    return kind === "setup" ? `If it breaks out: ${bit}, most you can lose ${money(pick.maxLoss)}` : `${bit}, most you can lose ${money(pick.maxLoss)}`;
  }
  return `Waiting: ${shortWaitingReason(row, settings)}`;
}

export function collapsedTags(row, kind) {
  const tags = [];
  tags.push(`<span class="badge status">${kind === "setup" ? "Setting up" : "Trade idea"}</span>`);
  if (row && row.marketAgainst) tags.push(`<span class="badge market">Market against you</span>`);
  return tags.slice(0, CARD_LAYOUT.maxTags).join(" ");
}

export function cardStatusTags(row, kind) { return collapsedTags(row, kind); }

export function whatsHappening(row, kind) {
  const close = kind === "idea" ? row.signal && row.signal.close : row.close;
  const pivot = kind === "idea" ? row.signal && row.signal.pivot : row.pivot;
  const diff = Number(pivot) - Number(close);
  let move = "near the breakout price";
  if (Number.isFinite(diff) && Math.abs(diff) < 0.005) {
    move = `right at the breakout price ${money(pivot)}`;
  } else if (Number.isFinite(diff) && diff > 0) {
    const amt = diff < 1 ? `${Math.round(diff * 100)} cents` : money(diff);
    move = `just ${amt} under the breakout price ${money(pivot)}`;
  } else if (Number.isFinite(diff)) {
    const amt = Math.abs(diff) < 1 ? `${Math.round(Math.abs(diff) * 100)} cents` : money(Math.abs(diff));
    move = `${amt} above the breakout price ${money(pivot)}`;
  }
  let lead;
  if (kind === "idea") {
    const s = row.signal || {};
    const vol = s.volRatio != null ? ` on ${Number(s.volRatio).toFixed(1)}x volume` : "";
    lead = `It closed above the breakout price ${money(s.pivot)}${vol}${s.date ? ` on ${s.date}` : ""}.`;
  } else if (row.status === "light_volume") {
    lead = `It closed above the breakout price ${money(pivot)}, but volume was lighter than we require.`;
  } else {
    lead = `Tight pattern, ${move}.`;
  }
  if (row.marketAgainst) lead += " The broad market is weak, so breakouts fail more often.";
  return lead;
}

export function contractLine(row, settings) {
  const pick = bestPassingChoice(row, settings);
  if (pick) {
    const exp = shortExpiry(pick.expiry);
    return `${money(pick.strike)} call${exp ? ` expiring ${exp}` : ""}.`;
  }
  return `None yet, because ${shortWaitingReason(row, settings)}.`;
}

export function lossLine(row, settings) {
  const pick = bestPassingChoice(row, settings);
  if (pick && pick.maxLoss != null) {
    const be = pick.breakevenAtExpiry != null ? ` Break-even at expiry: ${money(pick.breakevenAtExpiry)}.` : "";
    return `Most you can lose ${money(pick.maxLoss)}.${be}`;
  }
  return "Doesn't apply yet";
}

export function whatToDo(row, kind) {
  if (kind === "idea") {
    const s = row.signal || {};
    return `Check the live price in TradingView. Skip if the stock is below ${money(s.liveMin)} or above ${money(s.liveMax)}.`;
  }
  return `Set a TradingView alert at ${money(row.pivot)}. Not a trade until it closes above it on strong volume.`;
}

export function simpleView(row, kind, settings) {
  return `<div class="simple-view">
    <p><strong>What's happening.</strong> ${esc(whatsHappening(row, kind))}</p>
    <p><strong>Contract to consider.</strong> ${esc(contractLine(row, settings))}</p>
    <p><strong>Most you can lose / breakeven.</strong> ${esc(lossLine(row, settings))}</p>
    <p><strong>What to do.</strong> ${esc(whatToDo(row, kind))}</p>
    <p class="simple-footer">${esc(CARD_LAYOUT.simpleFooter)}</p>
  </div>`;
}

function moreDetailsWrap(ticker, inner) {
  const open = detailsShouldOpen(ticker) ? " open" : "";
  return `<details class="more-details"${open}><summary>${esc(CARD_LAYOUT.moreDetailsLabel)}</summary>
    <div class="more-details-body">${inner}</div></details>`;
}

function foldCard(ticker, extraClass, summaryInner, body) {
  const open = cardShouldOpen(ticker) ? " open" : "";
  return `<details class="card card-fold ${extraClass}" data-ticker="${esc(ticker)}"${open}>
    <summary class="card-summary">${summaryInner}</summary>
    <div class="card-expanded">${body}</div>
  </details>`;
}

function collapsedSummary(ticker, close, pivot, row, kind, settings) {
  return `<span class="sum-ticker">${esc(ticker)}</span>
    <span class="sum-price">${money(close)}</span>
    <span class="sum-dist">${esc(breakoutDistance(close, pivot))}</span>
    <span class="sum-tags">${collapsedTags(row, kind)}</span>
    <span class="sum-choice">${esc(collapsedLine(row, kind, settings))}</span>`;
}

export function renderIdea(idea, settings, cash, canLog) {
  const s = idea.signal;
  const contracts = (idea.contracts || []).filter((c) => !isOverCap(c, settings) && !isInsaneQuote(c, idea))
    .map((c) => renderContract(c, idea, settings, cash, canLog)).join("") ||
    `<p>No contract passed every rule. ${esc(idea.contractNote || "")}</p>`;
  const summary = collapsedSummary(idea.ticker, s.close, s.pivot, idea, "idea", settings);
  const more = `
    <p class="muted">${esc(idea.company)} · ${esc(idea.sector)}</p>
    <p>Breakout on ${esc(s.date)}: closed ${money(s.close)} above the pivot ${money(s.pivot)} on ${s.volRatio.toFixed(1)}x normal volume after a ${s.baseLength}-day base.
      Live check: skip if the stock is below ${money(s.liveMin)} or above ${money(s.liveMax)}. Next earnings: ${esc(idea.nextEarnings || "unknown")}.</p>
    ${quoteTimes(idea.quoteSource)}
    ${contracts}`;
  const body = `${simpleView(idea, "idea", settings)}${moreDetailsWrap(idea.ticker, more)}`;
  return foldCard(idea.ticker, idea.marketAgainst ? "market-against" : "", summary, body);
}

export function renderSetup(row, settings, cash) {
  const shares = Number(row.volumeNeeded).toLocaleString("en-US");
  const earnShown = row.earningsDisplay || row.nextEarnings || "unknown";
  const shown = visiblePlanChoices(row, settings);
  const planned = shown.map((c) => renderPlannedContract(c, row, settings, cash)).join("");
  const spread = (row.planSpread && !isOverCap(row.planSpread, settings)) ? renderPlanSpread(row.planSpread, row, settings, cash) : "";
  const summary = collapsedSummary(row.ticker, row.close, row.pivot, row, "setup", settings);
  const more = `
    <p class="muted">${esc(row.company || "")} · ${esc(row.sector || "")}</p>
    <dl class="facts">
      <div><dt>Last close</dt><dd>${money(row.close)}</dd></div>
      <div><dt>Breakout price</dt><dd>${money(row.pivot)}</dd></div>
      <div><dt>Distance</dt><dd>${esc(breakoutDistance(row.close, row.pivot))}</dd></div>
      <div><dt>Base</dt><dd>${esc(row.baseLength)} sessions, depth ${esc(row.depthPct)}%</dd></div>
      <div><dt>Volume needed on breakout</dt><dd>${shares} shares (1.25× the 50-day average)</dd></div>
    </dl>
    <p>Next earnings: ${esc(earnShown)}.</p>
    ${row.quoteSource ? quoteTimes(row.quoteSource) : ""}
    ${planned}${spread}`;
  const body = `${simpleView(row, "setup", settings)}${moreDetailsWrap(row.ticker, more)}`;
  return foldCard(row.ticker, "setup", summary, body);
}

function whatIfTable(rows) {
  if (!rows || !rows.length) return "";
  const body = rows.map((s) => `<tr><td>${esc(s.when)}</td><td>${esc(s.stock)} (${money(s.price)})</td>
    <td>${money(s.value)}</td><td class="${s.pnl >= 0 ? "good" : "bad"}">${signed(s.pnl)}</td></tr>`).join("");
  return `<div class="table-wrap"><table class="grid"><tr><th>When</th><th>Stock</th><th>Estimated value</th><th>Estimated P&amp;L</th></tr>${body}</table></div>
    <p class="small">Estimates use Black-Scholes at this contract’s delayed IV, with no change in volatility. Not a forecast.</p>`;
}

export function renderPlannedContract(c, row, settings, cash) {
  const g = c.greeks || {};
  const gp = c.greeksPlain || {};
  const role = c.role ? `<span class="badge role">${esc(c.role)}</span> ` : "";
  const fail = c.passes === false
    ? `<p class="doesnt-pass"><strong>Doesn’t pass yet: not a trade.</strong> ${esc(c.failedRulePlain || c.failedRule || "This contract did not pass every rule.")}</p>`
    : "";
  const header = c.passes === false
    ? `${role}${esc(row.ticker)} ${esc(c.expiry)} ${money(c.strike)} CALL × ${esc(c.qty || 1)}`
    : `${role}If it breaks out: ${esc(row.ticker)} ${esc(c.expiry)} ${money(c.strike)} CALL × ${esc(c.qty || 1)}`;
  return `<div class="contract plan-contract${c.passes === false ? " not-yet" : ""}">
    <h4>${header} <span class="sym">${esc(c.symbol || "")}</span></h4>
    ${fail}
    ${c.passes === false ? "" : skipLine(c, settings, cash)}
    <dl class="facts">
      <div><dt>Contracts</dt><dd>${esc(c.qty || 1)}</dd></div>
      <div><dt>Today's delayed ask</dt><dd>${money(c.ask)}</dd></div>
      ${c.estimatedAskAtPivot != null ? `<div><dt>Estimated ask at pivot</dt><dd>${money(c.estimatedAskAtPivot)}</dd></div>` : ""}
      <div><dt>Most you can lose</dt><dd>${money(c.maxLoss)} (premium + fees)</dd></div>
      <div><dt>Break-even at expiry</dt><dd>${money(c.breakevenAtExpiry)} stock price</dd></div>
      <div><dt>Most you can make</dt><dd>${esc(c.maxProfit || "No fixed limit (a long call can keep rising with the stock)")}</dd></div>
      <div><dt>Expiry / strike</dt><dd>${esc(c.expiry)} / ${money(c.strike)}</dd></div>
    </dl>
    ${c.howFar ? `<p class="how-far">${esc(c.howFar)}</p>` : ""}
    <p class="small">${esc(c.exitPlan || "")}</p>
    ${c.whyThisContract ? `<p><strong>Why this contract.</strong> ${esc(c.whyThisContract)}</p>` : ""}
    <p class="greeks-easy"><strong>Greeks, in easy words</strong> (model at the ${c.deltaEvaluatedAt === "current price" ? "current price" : "pivot"}, not the live market):
      ${esc(gp.delta || `If the stock rises $1, this option gains about ${money(g.deltaDollarsPer1)}.`)}
      ${esc(gp.gamma || "")}
      ${esc(gp.theta || "")}
      ${esc(gp.vega || "")}</p>
    ${whatIfTable(c.whatIf)}
  </div>`;
}

export function renderPlanSpread(sp, row, settings, cash) {
  if (!sp) return "";
  const fail = sp.passes === false
    ? `<p class="doesnt-pass"><strong>Doesn’t pass yet: not a trade.</strong> ${esc(sp.failedRulePlain || sp.failedRule || "")}</p>`
    : "";
  const hi = sp.highlight ? `<p class="spread-note">Highlighted because the single call is over the dollar cap (or failed that cap). A spread can cost less.</p>` : "";
  const longK = sp.long?.strike, shortK = sp.short?.strike;
  return `<div class="contract plan-contract spread${sp.highlight ? " highlight" : ""}${sp.passes === false ? " not-yet" : ""}">
    <h4>Call debit spread alternative${longK != null && shortK != null ? `: buy ${money(longK)} / sell ${money(shortK)} ${esc(sp.expiry || "")}` : ""}</h4>
    ${hi}${fail}
    <dl class="facts">
      <div><dt>Estimated net debit at pivot</dt><dd>${money(sp.netDebit)}</dd></div>
      <div><dt>Today's delayed net debit</dt><dd>${money(sp.todayNetDebit)}</dd></div>
      <div><dt>Most you can lose</dt><dd>${money(sp.maxLoss)} (debit + fees on both legs)</dd></div>
      <div><dt>Most you can make</dt><dd>${money(sp.maxProfit)} (width − debit − fees)</dd></div>
      <div><dt>Break-even at expiry</dt><dd>${money(sp.breakevenAtExpiry)} stock price</dd></div>
      <div><dt>Skip if live net debit above</dt><dd>${money(sp.maxEntryNetDebit)}</dd></div>
    </dl>
    ${sp.howFar ? `<p class="how-far">${esc(sp.howFar)}</p>` : ""}
    <p class="small">${esc(sp.exitPlan || "")}</p>
    ${sp.whyThisContract ? `<p><strong>Why this spread.</strong> ${esc(sp.whyThisContract)}</p>` : ""}
    ${whatIfTable(sp.whatIf)}
  </div>`;
}

export function renderSetups(list, settings, cash) {
  if (!list || !list.length) return "";
  return `<section class="setups" id="setting-up">
    <h2>Setting up (${list.length})</h2>
    <p>These stocks have a qualifying tight base but are not trade ideas. Each card starts as a short row — tap once for a plain-English snapshot, then More details if you want the numbers. Nothing is filled from this list. Sorted closest to the pivot first.</p>
    ${list.map((row) => renderSetup(row, settings, cash)).join("")}
  </section>`;
}

export function renderFreshness(f) {
  if (!f || f.state === "ok") return "";
  return `<div class="stale ${f.state === "failed" ? "stale-bad" : "stale-warn"}" role="alert"><strong>${f.state === "failed" ? "Out of date" : "Update problem"}.</strong> ${esc(f.message)}</div>`;
}

export function renderEmptyDay(data) {
  const day = emptyDay(data);
  if (!day) return "";
  const items = day.reasons.map((r) => `<li>${esc(r)}</li>`).join("");
  return `<section class="empty-day" id="empty-day"><h2>${esc(day.title)}</h2><p>${esc(day.lead)}</p>
    ${items ? `<ul>${items}</ul>` : ""}<p><strong>${esc(day.funnel)}</strong></p><p>${esc(day.note)}</p></section>`;
}

export function renderToday(data, settings, cash, fresh) {
  if (!data) return `${renderFreshness(fresh)}<p>No data file yet.</p>`;
  const head = renderFreshness(fresh) + `<div class="banner">${delayedBadge()} Built ${esc(data.generatedAtPT)} from the ${esc(data.signalSession)} daily candles. Ideas are for the ${esc(data.entrySession)} session, ${esc(data.entryWindowPT)}.</div>`;
  const gate = data.gate.checks.map((c) => `<li class="${c.pass ? "good" : "bad"}">${c.pass ? "✓" : "✗"} ${esc(c.name)}: ${esc(c.value)}</li>`).join("");
  const empty = renderEmptyDay(data);
  const okIdeas = data.suggestions || [];
  const tagged = data.marketAgainst || [];
  let body = "";
  if (okIdeas.length) {
    body += `<h2>Ideas to check in TradingView</h2>` + okIdeas.map((i) => renderIdea(i, settings, cash, true)).join("");
  }
  if (tagged.length) {
    body += `<section class="against" id="market-against">
      <h2><span class="badge market">Market against you</span> Still passed every other rule</h2>
      <p class="market-note">The market is weak, so breakouts fail more often.</p>
      ${tagged.map((i) => renderIdea(i, settings, cash, true)).join("")}
    </section>`;
  }
  if (!okIdeas.length && !tagged.length) {
    const f = data.funnel || {};
    body = `<p class="small">Checked ${f.tickers} stocks: ${f.withFreshCandles} with fresh candles, ${f.stockRulesPass} passed the chart rules, ${f.earningsPass} also cleared earnings, ${f.withContracts} had a contract that passed.</p>`;
  }
  const blocked = (data.blocked || []).length
    ? `<details class="blocked"><summary>Blocked today (${data.blocked.length}) (for learning — you can't log these)</summary><p class="bad">The market check above failed, so these are not suggestions.</p>${data.blocked.map((i) => renderIdea(i, settings, cash, false)).join("")}</details>` : "";
  const near = (data.nearMisses || []).length
    ? `<details><summary>Close, but one rule failed (${data.nearMisses.length})</summary><ul>${data.nearMisses.map((n) => `<li><strong>${esc(n.ticker)}</strong> — ${esc(n.failed)}: ${esc(n.detail)}</li>`).join("")}</ul></details>` : "";
  const setups = renderSetups(data.settingUp, settings, cash);
  return `${head}${empty}<section><h2>Market check</h2><ul class="checks">${gate}</ul></section>${body}${setups}${blocked}${near}`;
}

export function renderStatus(status) {
  if (!status) return `<h2>Automatic updates</h2><p class="small">No status file yet.</p>`;
  const row = (k, v) => `<tr><th>${k}</th><td>${v}</td></tr>`;
  const result = status.lastRunResult === "success" ? `<span class="good">✓ success</span>` : `<span class="bad">✗ ${esc(status.lastRunResult)}</span>`;
  return `<h2>Automatic updates</h2>
    <div class="table-wrap"><table class="grid" id="status-table">
    ${row("Last run", `${esc(status.lastRunPT)} (${esc(status.trigger)})${status.runUrl ? ` · <a href="${esc(status.runUrl)}" target="_blank" rel="noopener noreferrer">run log</a>` : ""}`)}
    ${row("Result", result + (status.lastError ? ` — ${esc(status.lastError)}` : ""))}
    ${row("Last success", `${esc(status.lastSuccessPT)} (candles from ${esc(status.lastSuccessSession)})`)}
    ${row("Next run", esc(status.nextRunPT))}
    ${row("Schedule", esc(status.schedule))}
    </table></div>`;
}


export function renderRules() {
  return `<h2>How ideas are chosen</h2>
    <ul id="rules-text">
      <li>A suggestion must pass the stock rules (breakout, volume, moving averages, chase limit, sector, relative strength) and the contract rules (35–70 days to expiry, delta 0.35–0.60, open interest at least 500, volume at least 50, IV at most 1.3× realized volatility, skew, earnings at least 15 trading days away, and the price ceiling X). At most 3 open positions and 1 per sector.</li>
      <li><strong>Test rule:</strong> breakout-day volume must be at least 1.25× the prior 50-day average (was 1.50×).</li>
      <li><strong>Test rule:</strong> the tight base may be 5–40 sessions (was 10–40). Depth still ≤ 25%. For bases of 10 or more sessions the last-10 range must still be ≤ 60% of the base. For a shorter 5–9 session base the last half of the base is used instead (rounded up, at least 2 sessions), because a last-10 window would be longer than the base, and using the whole short base would ask a range to be 60% of itself. Volume dry-up uses that same trailing slice. No other rule was loosened.</li>
      <li><strong>Setting up</strong> is a watch list, not a trade list: stocks with a qualifying tight base that have not yet closed above the pivot, plus stocks that broke out but failed only the volume bar (1.25× the 50-day average). Each row can show up to three planned calls (Safer: higher delta, closer in-the-money; Balanced: near 0.47 delta; Cheaper: lower delta) plus a call debit spread (buy the balanced call, sell a higher strike in the same expiry). The same contract rules apply (35–70 days to expiry, delta 0.35–0.60 at the pivot when the model allows, open interest at least 500, volume at least 50, IV at most 1.3× realized volatility, skew, the $200 cap including fees; the spread uses fees on both legs). Each choice shows cost, max loss, break-even, max profit, how far the stock must move from the pivot, the Floor’s whole-position exit plan (3× debit or stock targets/stops, 15 trading days, 21 days before expiry — no scaling out), easy-words Greeks, and a small Black-Scholes what-if table (estimate only). A choice that fails is labelled “Doesn’t pass yet: not a trade” with the rule in plain words. Earnings dates come from US Stock Box first, then Yahoo calendarEvents and the Nasdaq earnings calendar; if sources disagree by more than 3 days the earlier date is used; estimated dates are labelled estimated; unknown dates still block a trade; the 15-trading-day rule is unchanged. Every card says “Plan only — not a trade until it closes above the pivot on volume; recheck live price in TradingView.” Nothing is filled from this list.</li>
      <li>When the S&amp;P 500 / market_ok check is the only rule that fails (SPY below a falling 50-day average, or Stock Box market_ok is no), ideas that passed everything else are still shown, tagged <span class="badge market">Market against you</span>. The market is weak, so breakouts fail more often. They are shown because you said you will manage the risk.</li>
      <li>Your adjustable max loss per trade stays at the amount you set (default $200 including fees). It is not lowered on tagged ideas, and no extra relative-strength filter is added.</li>
      <li>If something else in the market check fails (for example the interest rate or the session date), those ideas stay in a learning-only list and cannot be logged from this page.</li>
    </ul>`;
}

export function renderSources(data) {
  if (!data) return "";
  const prov = (data.provenance || []).map((p) => `<tr><td>${esc(p.field)}</td><td><strong>${esc(p.from)}</strong></td><td>${esc(p.how)}</td></tr>`).join("");
  const provTable = prov ? `<h3>Which fields come from US Stock Box and which are fetched directly</h3>
    <div class="table-wrap"><table class="grid" id="provenance-table"><tr><th>Field</th><th>From</th><th>Details</th></tr>${prov}</table></div>` : "";
  const rows = data.sources.map((s) => `<tr><td>${s.ok ? "✓" : "✗"}</td><td><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a></td><td>${esc(s.detail)}</td><td>${esc(s.sourceTime)}</td><td>${esc(s.retrievedAtPT)}</td></tr>`).join("");
  const warn = data.warnings.map((w) => `<li>${esc(w)}</li>`).join("");
  const nc = (data.notChecked || []).map((w) => `<li>${esc(w)}</li>`).join("");
  return `<h2>Where the data comes from ${delayedBadge()}</h2>
    <div class="table-wrap"><table class="grid"><tr><th></th><th>Source</th><th>Result</th><th>Source time</th><th>Retrieved</th></tr>${rows}</table></div>
    ${provTable}<h3>Notes</h3><ul>${warn}${nc}</ul>`;
}
