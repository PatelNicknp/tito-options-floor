// Wires the page. Saves only to this browser (localStorage). No network writes, no trading.
import * as L from "./logic.js";
import * as R from "./render.js";

const SETTINGS_KEY = "tito-floor-settings-v1";
const LOG_KEY = "tito-floor-log-v1";
const $ = (s) => document.querySelector(s);
let data = null;
let status = null;

const loadSettings = () => ({ ...L.DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") });
const loadLog = () => JSON.parse(localStorage.getItem(LOG_KEY) || "[]");
const saveLog = (log) => localStorage.setItem(LOG_KEY, JSON.stringify(log));
const todayPT = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles" }).format(new Date());

function show(tab) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll("main > section.tab").forEach((s) => (s.hidden = s.id !== tab));
  render();
}

function render() {
  const settings = loadSettings();
  const log = loadLog();
  const cash = L.cashAvailable(settings, log);
  $("#cashline").textContent = `Paper cash: $${cash.toFixed(2)} · Max loss per trade: $${Math.min(settings.capDollars, cash).toFixed(2)} incl. fees · Open positions: ${L.openPositions(log).length} of ${L.MAX_OPEN}`;
  const now = new Date();
  const hm = new Intl.DateTimeFormat("en-GB", { timeZone: "America/Los_Angeles", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now).split(":");
  const fresh = L.freshness(status, data, todayPT(), Number(hm[0]) * 60 + Number(hm[1]));
  $("#today-body").innerHTML = R.renderToday(data, settings, cash, fresh);
  $("#sources-body").innerHTML = R.renderStatus(status) + R.renderSources(data);
  renderPositions(settings, log);
  $("#set-cap").value = settings.capDollars;
  $("#set-fee").value = settings.feePerContract;
  $("#set-cash").value = settings.startingCash;
  const sel = $("#exit-position");
  sel.innerHTML = `<option value="">Choose…</option>` + L.openPositions(log).map((e) => `<option value="${R.esc(e.id)}">${R.esc(e.ticker)} ${R.esc(e.contract.expiry)} $${e.contract.strike} CALL × ${e.entry.qty}</option>`).join("");
}

function renderPositions(settings, log) {
  const today = todayPT();
  const open = L.openPositions(log);
  const rows = open.map((p) => {
    const mark = data?.marks?.[p.ticker];
    const flags = L.exitFlags(p, mark, today, null, settings.feePerContract);
    return `<article class="card"><h3>${R.esc(p.ticker)} ${R.esc(p.contract.expiry)} $${p.contract.strike} CALL × ${p.entry.qty}</h3>
      <p><span class="badge you">${R.esc(p.fillLabel)}</span> Bought at $${p.entry.fill.toFixed(2)} on ${R.esc(p.entry.timeSeenPT.replace("T", " "))} PT. Cost $${(p.entry.fill * 100 * p.entry.qty + p.entry.fees).toFixed(2)}.</p>
      <p class="small">Latest daily close in the data file: ${mark ? `$${mark.close} on ${mark.date}` : "not in the data file"} ${R.delayedBadge()}</p>
      ${flags.length ? `<p class="small">Exit reminders only. You decide and log any exit yourself.</p><ul class="flags">${flags.map((f) => `<li>${R.esc(f)}</li>`).join("")}</ul>` : `<p class="good">No exit rule is flagged right now.</p>`}
      <label class="bidcheck">Live option bid you see now (optional, to check the 3x option target): <input type="number" step="0.01" data-bid="${R.esc(p.id)}"></label>
      <p class="flagout" id="bidflag-${R.esc(p.id)}"></p></article>`;
  }).join("") || "<p>No open positions.</p>";
  const closed = log.filter((e) => e.status === "closed").map((e) => `<tr><td>${R.esc(e.ticker)}</td><td>${R.esc(e.contract.expiry)} $${e.contract.strike}</td><td>$${e.entry.fill.toFixed(2)}</td><td>$${e.exit.fill.toFixed(2)}</td><td>${R.esc(e.exit.reason)}</td><td class="${e.realizedPnl >= 0 ? "good" : "bad"}">$${e.realizedPnl.toFixed(2)}</td></tr>`).join("");
  $("#positions-body").innerHTML = `<h2>Open positions</h2>${rows}<h2>Closed (Nick-confirmed fills only)</h2>` +
    (closed ? `<div class="table-wrap"><table class="grid"><tr><th>Ticker</th><th>Contract</th><th>Bought</th><th>Sold</th><th>Why</th><th>Profit / loss</th></tr>${closed}</table></div>` : `<p class="muted">None yet.</p>`);
  document.querySelectorAll("[data-bid]").forEach((inp) => inp.addEventListener("input", () => {
    const p = open.find((x) => x.id === inp.dataset.bid);
    const f = L.exitFlags(p, data?.marks?.[p.ticker], today, inp.value, settings.feePerContract).filter((x) => x.startsWith("Option target"));
    $(`#bidflag-${p.id}`).textContent = f[0] || "";
  }));
}

function formValues(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function prefill(ticker, symbol) {
  const idea = data.suggestions.find((i) => i.ticker === ticker);
  const c = idea?.contracts.find((x) => x.symbol === symbol);
  if (!c) return;
  const f = $("#entry-form");
  f.ticker.value = ticker; f.expiry.value = c.expiry; f.strike.value = c.strike; f.sector.value = idea.sector;
  f.nextEarnings.value = idea.nextEarnings || ""; f.pivot.value = idea.signal.pivot; f.stop.value = idea.signal.stop;
  f.target.value = idea.signal.target; f.signalSession.value = idea.signal.date; f.suggestionId.value = symbol;
  f.bid.value = ""; f.ask.value = ""; f.fill.value = "";
  show("log");
  $("#entry-msg").textContent = "Filled in from the suggestion. Now type the LIVE bid, ask and stock price you see in TradingView.";
}

function init() {
  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  document.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-log]");
    if (t) { const [tk, sym] = t.dataset.log.split("|"); prefill(tk, sym); }
  });
  const ef = $("#entry-form");
  ef.qty.addEventListener("input", () => { ef.fees.value = (loadSettings().feePerContract * (Number(ef.qty.value) || 0)).toFixed(2); });
  ef.ask.addEventListener("input", () => { ef.fill.value = ef.ask.value; });
  ef.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const settings = loadSettings(), log = loadLog(), v = formValues(ef);
    const errors = L.checkEntry(v, settings, log);
    if (errors.length) { $("#entry-msg").innerHTML = `<ul class="bad">${errors.map((e) => `<li>${R.esc(e)}</li>`).join("")}</ul>`; return; }
    log.push(L.buildEntry(v, settings, log, `t${Date.now()}`));
    saveLog(log); ef.reset(); ef.fees.value = settings.feePerContract.toFixed(2);
    $("#entry-msg").innerHTML = `<p class="good">Saved in this browser as a ${L.FILL_LABEL}.</p>`; render();
  });
  const xf = $("#exit-form");
  xf.bid.addEventListener("input", () => { xf.fill.value = xf.bid.value; });
  xf.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const log = loadLog(), v = formValues(xf), pos = log.find((e) => e.id === v.position);
    const errors = L.checkExit(v, pos);
    if (errors.length) { $("#exit-msg").innerHTML = `<ul class="bad">${errors.map((e) => `<li>${R.esc(e)}</li>`).join("")}</ul>`; return; }
    log[log.indexOf(pos)] = L.buildExit(v, pos);
    saveLog(log); xf.reset(); $("#exit-msg").innerHTML = `<p class="good">Exit saved.</p>`; render();
  });
  $("#exit-position").addEventListener("change", (ev) => {
    const pos = loadLog().find((e) => e.id === ev.target.value);
    if (pos) { xf.qty.value = pos.entry.qty; xf.fees.value = (loadSettings().feePerContract * pos.entry.qty).toFixed(2); }
  });
  $("#settings-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const cap = Number($("#set-cap").value), fee = Number($("#set-fee").value), cash = Number($("#set-cash").value);
    if (!(cap > 0) || !(fee >= 0) || !(cash > 0)) { $("#settings-msg").textContent = "Use positive dollar amounts."; return; }
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ capDollars: cap, feePerContract: fee, startingCash: cash }));
    $("#settings-msg").textContent = "Saved. The new max loss applies to future trades only."; render();
  });
  $("#export").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify({ app: "tito-options-floor", exportedAt: new Date().toISOString(), settings: loadSettings(), log: loadLog() }, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `tito-floor-log-${todayPT()}.json`; a.click();
  });
  $("#import").addEventListener("change", async (ev) => {
    const file = ev.target.files[0]; if (!file) return;
    let obj; try { obj = JSON.parse(await file.text()); } catch { $("#settings-msg").textContent = "That file is not valid JSON."; return; }
    const errors = L.checkImport(obj);
    if (errors.length) { $("#settings-msg").textContent = errors.join(" "); return; }
    if (!confirm("Replace the log in this browser with the file's log?")) return;
    saveLog(obj.log); if (obj.settings) localStorage.setItem(SETTINGS_KEY, JSON.stringify(obj.settings));
    $("#settings-msg").textContent = `Imported ${obj.log.length} record(s).`; render();
  });
  ef.fees.value = loadSettings().feePerContract.toFixed(2);
  const get = (u) => fetch(u, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  Promise.all([get("data/latest.json"), get("data/status.json")])
    .then(([d, s]) => { data = d; status = s; show(location.hash.slice(1) || "today"); });
}

init();
