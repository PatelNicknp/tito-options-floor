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
  const over = contract.ask > x;
  return `<p class="skip${over ? " over" : ""}"><strong>Skip if live ask above ${money(x)}</strong> (1 contract, max loss ${money(Math.min(settings.capDollars, cash))} incl. fees).
    ${over ? `The delayed ask ${money(contract.ask)} is already above this, so skip unless the live ask is lower.` : ""}</p>`;
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
  return `<article class="card">
    <h3>${esc(idea.ticker)} <span class="muted">${esc(idea.company)} · ${esc(idea.sector)}</span></h3>
    <p>Breakout on ${esc(s.date)}: closed ${money(s.close)} above the pivot ${money(s.pivot)} on ${s.volRatio.toFixed(1)}x normal volume after a ${s.baseLength}-day base.
      Live check: skip if the stock is below ${money(s.liveMin)} or above ${money(s.liveMax)}. Next earnings: ${esc(idea.nextEarnings || "unknown")}.</p>
    ${quoteTimes(idea.quoteSource)}
    ${contracts}
  </article>`;
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
  let body;
  if (data.noTradeToday) {
    const f = data.funnel;
    body = `<p class="small">Checked ${f.tickers} stocks: ${f.withFreshCandles} with fresh candles, ${f.stockRulesPass} passed the chart rules, ${f.earningsPass} also cleared earnings, ${f.withContracts} had a contract that passed.</p>`;
  } else {
    body = `<h2>Ideas to check in TradingView</h2>` + data.suggestions.map((i) => renderIdea(i, settings, cash, true)).join("");
  }
  const blocked = (data.blocked || []).length
    ? `<details class="blocked"><summary>Blocked today (${data.blocked.length}) (for learning — you can't log these)</summary><p class="bad">The market check above failed, so these are not suggestions.</p>${data.blocked.map((i) => renderIdea(i, settings, cash, false)).join("")}</details>` : "";
  const near = (data.nearMisses || []).length
    ? `<details><summary>Close, but one rule failed (${data.nearMisses.length})</summary><ul>${data.nearMisses.map((n) => `<li><strong>${esc(n.ticker)}</strong> — ${esc(n.failed)}: ${esc(n.detail)}</li>`).join("")}</ul></details>` : "";
  return `${head}${empty}<section><h2>Market check</h2><ul class="checks">${gate}</ul></section>${body}${blocked}${near}`;
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
