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

export function renderIdea(idea, settings, cash, canLog) {
  const s = idea.signal;
  const contracts = (idea.contracts || []).map((c) => renderContract(c, idea, settings, cash, canLog)).join("") ||
    `<p>No contract passed every rule. ${esc(idea.contractNote || "")}</p>`;
  const tag = idea.marketAgainst ? `<span class="badge market">Market against you</span> ` : "";
  const note = idea.marketAgainst ? `<p class="market-note">${esc(idea.marketNote || "The market is weak, so breakouts fail more often.")}</p>` : "";
  return `<article class="card${idea.marketAgainst ? " market-against" : ""}">
    <h3>${tag}${esc(idea.ticker)} <span class="muted">${esc(idea.company)} · ${esc(idea.sector)}</span></h3>
    ${note}
    <p>Breakout on ${esc(s.date)}: closed ${money(s.close)} above the pivot ${money(s.pivot)} on ${s.volRatio.toFixed(1)}x normal volume after a ${s.baseLength}-day base.
      Live check: skip if the stock is below ${money(s.liveMin)} or above ${money(s.liveMax)}. Next earnings: ${esc(idea.nextEarnings || "unknown")}.</p>
    ${quoteTimes(idea.quoteSource)}
    ${contracts}
  </article>`;
}


export function renderSetup(row, settings, cash) {
  const tag = row.marketAgainst ? `<span class="badge market">Market against you</span> ` : "";
  const market = row.marketAgainst ? `<p class="market-note">${esc(row.marketNote || "The market is weak, so breakouts fail more often.")}</p>` : "";
  const below = row.pctBelowPivot > 0
    ? `${Math.abs(row.pctBelowPivot).toFixed(2)}% below pivot`
    : row.pctBelowPivot < 0
      ? `${Math.abs(row.pctBelowPivot).toFixed(2)}% above pivot`
      : "at the pivot";
  const why = row.status === "light_volume"
    ? "Broke out above the pivot, but volume was below 1.25× the 50-day average."
    : "Has a tight base but has not yet closed above the pivot.";
  const earnClass = row.earningsSoon ? "setup-earn warn" : "setup-earn";
  const earn = row.earningsSoon
    ? `Earnings warning: ${esc(row.earningsNote || row.nextEarnings || "date unknown")} (within 15 trading days or unknown).`
    : `Next earnings: ${esc(row.nextEarnings || "unknown")}.`;
  const shares = Number(row.volumeNeeded).toLocaleString("en-US");
  const disclaimer = `<p class="setup-plan-label">Plan only — not a trade until it closes above the pivot on volume; recheck live price in TradingView.</p>`;
  const planned = (row.plannedContracts || []).map((c) => renderPlannedContract(c, row, settings, cash)).join("");
  const noPlan = planned ? "" : `<p class="setup-no-contract">${esc(row.planNote || "No listed call passed every rule.")}</p>`;
  return `<article class="card setup" data-ticker="${esc(row.ticker)}">
    <h3>${tag}${esc(row.ticker)} <span class="muted">${esc(row.company)} · ${esc(row.sector)}</span></h3>
    ${market}
    <p>${why}</p>
    <dl class="facts">
      <div><dt>Last close</dt><dd>${money(row.close)}</dd></div>
      <div><dt>Pivot / breakout price</dt><dd>${money(row.pivot)}</dd></div>
      <div><dt>Distance to pivot</dt><dd>${esc(below)}</dd></div>
      <div><dt>Base</dt><dd>${esc(row.baseLength)} sessions, depth ${esc(row.depthPct)}%</dd></div>
      <div><dt>Volume needed on breakout</dt><dd>${shares} shares (1.25× the 50-day average)</dd></div>
    </dl>
    <p class="${earnClass}">${earn}</p>
    <p class="setup-note">Not a trade yet. Set a TradingView alert at ${money(row.pivot)}.</p>
    ${disclaimer}
    ${row.quoteSource ? quoteTimes(row.quoteSource) : ""}
    ${planned}${noPlan}
  </article>`;
}

export function renderPlannedContract(c, row, settings, cash) {
  const g = c.greeks || {};
  const sig = row.planSignal || { stop: row.baseLow, pivot: row.pivot, target: row.pivot };
  const deltaAt = c.deltaEvaluatedAt === "current price"
    ? `evaluated at today's price ${money(row.close)} (the model at the pivot was not available)`
    : `evaluated at the pivot ${money(row.pivot)} using current IV`;
  return `<div class="contract plan-contract">
    <h4>If it breaks out: ${esc(row.ticker)} ${esc(c.expiry)} $${esc(c.strike)} CALL × ${esc(c.qty || 1)} <span class="sym">${esc(c.symbol)}</span></h4>
    ${skipLine(c, settings, cash)}
    <dl class="facts">
      <div><dt>Contracts</dt><dd>${esc(c.qty || 1)}</dd></div>
      <div><dt>Today's delayed ask</dt><dd>${money(c.ask)}</dd></div>
      <div><dt>Estimated ask at pivot</dt><dd>${money(c.estimatedAskAtPivot)}</dd></div>
      <div><dt>Most you can lose</dt><dd>${money(c.maxLoss)} (premium + fees)</dd></div>
      <div><dt>Break-even at expiry</dt><dd>${money(c.breakevenAtExpiry)} stock price</dd></div>
      <div><dt>Delta</dt><dd>${esc(g.delta)} (${deltaAt})</dd></div>
      <div><dt>Expiry / strike</dt><dd>${esc(c.expiry)} / ${money(c.strike)}</dd></div>
      <div><dt>Profit target (option)</dt><dd>Sell if bid reaches ${money(c.optionProfitTargetBid)}</dd></div>
    </dl>
    <p class="small">Exit plan: sell the whole position on the first of: daily close below ${money(sig.stop)} (planned stop, the base low until a breakout-day low prints) · close below the pivot ${money(sig.pivot)} in the first 3 sessions · close at or above ${money(sig.target)} (target) · close below the 20-day average · ${esc(row.levels?.timeStopDate)} (15 trading days) · ${esc(c.expiryExitDate)} (21 days before expiry). Ex-dividend: not checked.</p>
  </div>`;
}

export function renderSetups(list, settings, cash) {
  if (!list || !list.length) return "";
  return `<section class="setups" id="setting-up">
    <h2>Setting up (${list.length})</h2>
    <p>These stocks have a qualifying tight base but are not trade ideas. A planned contract is shown only for if the stock breaks out. Nothing is filled from this list. Sorted closest to the pivot first.</p>
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
      <li><strong>Setting up</strong> is a watch list, not a trade list: stocks with a qualifying tight base that have not yet closed above the pivot, plus stocks that broke out but failed only the volume bar (1.25× the 50-day average). Each row can include a planned call to buy if it breaks out, using the same contract rules (35–70 days to expiry, delta 0.35–0.60 evaluated at the pivot when the model allows, otherwise at today's price and labelled as such, open interest at least 500, volume at least 50, IV at most 1.3× realized volatility, skew, the $200 cap including fees). The planned card shows today's delayed ask, the estimated ask at the pivot, “Skip if live ask above $X”, max loss, break-even, delta, expiry, strike, and the usual profit-target and exit notes. If no contract passes, the reason is shown (for example “Too expensive for $200 cap” or “No contract: earnings date unknown”). Every card says “Plan only — not a trade until it closes above the pivot on volume; recheck live price in TradingView.” Nothing is filled from this list.</li>
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
