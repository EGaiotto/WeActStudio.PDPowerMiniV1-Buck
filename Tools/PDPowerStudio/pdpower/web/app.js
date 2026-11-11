/* PDPower Studio — front-end */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const C = { v: "#38bdf8", i: "#fbbf24", p: "#e879f9", muted: "#8796ae", grid: "#ffffff0f", ok: "#34d399", bad: "#f87171" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let S = null;                 // último estado do servidor
let cfg = null;
const BUF = { t: [], v: [], i: [], p: [], on: [] };
const MAXBUF = 250000;
let markers = [];
let winSec = 60, paused = false, needDraw = true;
let showSeries = { 1: true, 2: true, 3: false };
let chart = null, xy = null;
let xyMode = "set";
let lastSweepLen = -1, lastMeasLen = -1;

// ------------------------------------------------------------------ util
async function api(path, body, method) {
  const opt = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg, "ok"); return r; }
  catch (e) { toast(e.message, "error"); throw e; }
}
function toast(msg, kind = "info", ms = 4200) {
  const d = document.createElement("div");
  d.className = "toast " + kind; d.textContent = msg;
  $("#toasts").appendChild(d);
  setTimeout(() => d.remove(), ms);
}
function ask(title, bodyHtml, { ok = "OK", danger = false, expect = null, placeholder = "" } = {}) {
  return new Promise((res) => {
    const dlg = $("#dlg");
    $("#dlgTitle").textContent = title;
    $("#dlgBody").innerHTML = bodyHtml + (expect !== null || placeholder ? `<input id="dlgIn" placeholder="${esc(placeholder || expect)}" autocomplete="off">` : "");
    const okb = $("#dlgOk"); okb.textContent = ok; okb.className = danger ? "danger" : "primary";
    const inp = $("#dlgIn");
    if (expect) { okb.disabled = true; inp.oninput = () => { okb.disabled = inp.value.trim() !== expect; }; } else okb.disabled = false;
    dlg.onclose = () => res(dlg.returnValue === "ok" ? (inp ? inp.value.trim() : true) : null);
    dlg.showModal(); if (inp) inp.focus();
  });
}
const fmt = (x, d = 3) => (x === null || x === undefined || isNaN(x)) ? "–" : Number(x).toFixed(d);
const fmtP = (p) => p >= 10 ? p.toFixed(2) : p.toFixed(3);
function hms(s) { s = Math.floor(s || 0); return [s / 3600 | 0, (s % 3600) / 60 | 0, s % 60].map((x) => String(x).padStart(2, "0")).join(":"); }
function crc8(bytes) { let c = 0xff; for (const b of bytes) { c ^= b; for (let k = 0; k < 8; k++) c = c & 0x80 ? ((c << 1) ^ 0x31) & 0xff : (c << 1) & 0xff; } return c; }
async function saveText(name, content) {
  const r = await act(() => api("/api/save_file", { name, content }));
  toast(t("Salvo em {p}", { p: r.path }), "ok", 7000);
}

// ------------------------------------------------------------------ idioma
applyI18n();
$("#selLang").innerHTML = LANGS.map(([v, n]) => `<option value="${v}"${v === LANG ? " selected" : ""}>🌐 ${n}</option>`).join("");
$("#selLang").title = "Idioma · Language · 语言";
$("#selLang").onchange = async (e) => {
  // preserva aba e sequência editada (o exemplo intocado é regerado já traduzido)
  const seq = $("#seqEditor").value, untouched = seq === JSON.stringify(TEMPLATES[1].build(DEFAULT_SWEEP), null, 2);
  try { sessionStorage.setItem("pd_keep", JSON.stringify({ seq: untouched ? null : seq, tab: $(".tabs > button.active")?.dataset.tab })); } catch { }
  try { await api("/api/config", { ui: { lang: e.target.value } }); } catch (er) { toast(er.message, "error"); return; }
  location.reload();
};

// ------------------------------------------------------------------ tabs
$$(".tabs > button").forEach((b) => b.onclick = () => {
  $$(".tabs > button").forEach((x) => x.classList.toggle("active", x === b));
  history.replaceState(null, "", "#" + b.dataset.tab);   // link direto para a aba (ex.: /#auto)
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "dash") setTimeout(resizeCharts, 0);
  if (b.dataset.tab === "auto") { setTimeout(resizeCharts, 0); loadResults(true); }
  if (b.dataset.tab === "cfg") loadConfig();
});

// ------------------------------------------------------------------ conexão
async function loadPorts() {
  const ports = await api("/api/ports").catch(() => []);
  const sel = $("#selPort"); const prev = sel.value;
  sel.innerHTML = `<option value="">${t("Auto-detectar")}</option>` + ports.map((p) =>
    `<option value="${esc(p.port)}">${esc(p.port)}${p.pdpower ? " — PDPowerMini ✓" : " — " + esc(p.description)}</option>`).join("");
  if (prev && ports.some((p) => p.port === prev)) sel.value = prev;
  else { const pd = ports.find((p) => p.pdpower); if (pd) sel.value = pd.port; }
}
$("#btnPorts").onclick = loadPorts;
$("#selLink").onchange = () => { $("#selBaud").hidden = $("#selLink").value !== "uart"; };
$("#btnConnect").onclick = async () => {
  if (S && S.connected) return act(() => api("/api/disconnect", {}));
  $("#btnConnect").disabled = true;
  try {
    await act(() => api("/api/connect", { port: $("#selPort").value || null, link: $("#selLink").value, baud: +$("#selBaud").value }), t("Conectado"));
    await reloadHistory();
  } finally { $("#btnConnect").disabled = false; }
};
$("#btnSim").onclick = async () => { await act(() => api("/api/connect", { simulate: true, link: "usb" }), t("Simulador conectado")); };

// ------------------------------------------------------------------ websocket
function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    S = m.state;
    if (m.samples.length) appendSamples(m.samples);
    if (m.logs.length) appendLogs(m.logs);
    if (m.markers.length) { markers.push(...m.markers); if (markers.length > 500) markers.splice(0, markers.length - 500); needDraw = true; }
    renderState();
  };
  ws.onclose = () => { $("#stDot").className = "dot bad"; $("#stTxt").textContent = t("Servidor indisponível — reconectando…"); setTimeout(connectWS, 1000); };
}
function appendSamples(rows) {
  const last = BUF.t.length ? BUF.t[BUF.t.length - 1] : 0;
  for (const r of rows) {
    if (r[0] <= last) continue;
    BUF.t.push(r[0]); BUF.v.push(r[1]); BUF.i.push(r[2]); BUF.p.push(r[1] * r[2]); BUF.on.push(r[3]);
  }
  if (BUF.t.length > MAXBUF) { const n = BUF.t.length - MAXBUF; for (const k in BUF) BUF[k].splice(0, n); }
  needDraw = true;
}
async function reloadHistory() {
  const h = await api("/api/history?seconds=21600").catch(() => null);
  if (!h) return;
  BUF.t = h.t; BUF.v = h.v; BUF.i = h.i; BUF.p = h.p; BUF.on = h.on; needDraw = true;
}

// ------------------------------------------------------------------ renderização do estado
const dirty = { v: false, i: false };
function renderState() {
  if (!S) return;
  const con = S.connected;
  $("#btnConnect").textContent = con ? t("Desconectar") : t("Conectar");
  $("#btnConnect").className = con ? "" : "primary";
  ["#selPort", "#selLink", "#selBaud"].forEach((s) => $(s).disabled = con);
  if (con) {
    const sel = $("#selPort");
    if (![...sel.options].some((o) => o.value === S.port)) sel.insertAdjacentHTML("beforeend", `<option value="${esc(S.port)}">${S.simulated ? t("Simulador") : esc(S.port)}</option>`);
    if (sel.value !== S.port) sel.value = S.port;
    if ($("#selLink").value !== S.link) $("#selLink").value = S.link;
  } else if ($("#selPort").value === "SIM") { $("#selPort").querySelector('option[value="SIM"]')?.remove(); }
  const info = S.info || {}, inp = S.input || {}, lim = S.limits || {};
  $("#devName").textContent = info.name || "WeAct PDPowerMini";
  const chips = [];
  if (con) {
    if (S.simulated) chips.push(`<span class="chip pd"><b>${t("SIMULADOR")}</b></span>`);
    chips.push(`<span class="chip ok">${t("Modelo")} <b>${esc(info.model)}</b></span>`);
    chips.push(`<span class="chip">FW <b>${esc((info.version || "").split("_")[0])}</b></span>`);
    chips.push(`<span class="chip">SN <b>${esc(info.serial)}</b></span>`);
    if (inp.supported && inp.label) chips.push(`<span class="chip pd">${t("Entrada")} <b>${esc(inp.label)} ${fmt(inp.v, 2)} V</b></span>`);
  }
  const chipsHtml = chips.join("");
  if ($("#chips").innerHTML !== chipsHtml) $("#chips").innerHTML = chipsHtml;

  // saída
  const out = S.output || {}, on = con && out.on, mode = out.mode;
  const pw = $("#btnPower");
  pw.className = "power" + (on ? " on" : "") + (on && mode === "CC" ? " cc" : "") + (mode === "OC" ? " oc" : "");
  pw.disabled = !con;
  $("#outState").textContent = !con ? "—" : on ? t("LIGADA") : t("DESLIGADA");
  const mb = $("#modeBadge");
  if (con && mode === "OC") { mb.className = "mode OC"; mb.textContent = t("SOBRECORRENTE"); }
  else if (on) { mb.className = "mode " + mode; mb.textContent = mode === "CV" ? t("CV · tensão constante") : t("CC · corrente limitada"); }
  else { mb.className = "mode"; mb.textContent = con ? t("em espera") : t("offline"); }

  // ajuste
  const pid = S.preset_id ?? 0, pr = (S.presets || [])[pid] || {};
  $("#activePresetLbl").textContent = t("preset ativo M{n}", { n: pid });
  for (const k of ["v", "i"]) {
    const inpEl = $(k === "v" ? "#inV" : "#inI");
    if (!dirty[k] && document.activeElement !== inpEl && pr[k] != null) inpEl.value = pr[k].toFixed(k === "v" ? 2 : 3);
    const rg = $(k === "v" ? "#rgV" : "#rgI");
    rg.min = k === "v" ? lim.v_min ?? 0.5 : lim.i_min ?? 0.005; rg.max = k === "v" ? lim.v_max ?? 20 : lim.i_max ?? 3;
    if (!dirty[k] && document.activeElement !== rg && pr[k] != null) rg.value = pr[k];
  }
  // presets
  const ph = (S.presets || []).map((p, n) => `<div class="preset${n === pid ? " active" : ""}" data-id="${n}">
     <span class="ed" data-ed="${n}" title="${t("Editar")}">✎</span><div class="pid">M${n}</div>
     <div class="pv">${fmt(p.v, 2)} V</div><div class="pi">${fmt(p.i, 3)} A</div></div>`).join("");
  if ($("#presets").innerHTML !== ph) $("#presets").innerHTML = ph;

  // ajustes
  const st = S.settings || {};
  setToggle("#tgOcp", st.ocp); setToggle("#tgCal", st.offset); setToggle("#tgDis", st.discharge);
  $("#disNote").textContent = st.discharge === null || st.discharge === undefined ? t("(estado desconhecido — protocolo não lê)") : "";
  if (document.activeElement !== $("#rgBright") && st.brightness != null) $("#rgBright").value = st.brightness;
  $("#lblBright").textContent = st.brightness != null ? st.brightness + "%" : "–";

  // entrada PD
  $("#pdCard").hidden = !(con && inp.supported);
  $("#inType").textContent = inp.label || "–";
  $("#inVolt").textContent = inp.v != null ? fmt(inp.v, 3) + " V" : "–";
  const inPd = $("#inPd");
  inPd.min = lim.pd_min ?? 8; inPd.max = lim.pd_max ?? 24;
  if (document.activeElement !== inPd && inp.pd_req != null) inPd.value = inp.pd_req.toFixed(1);

  // leituras
  const m = S.meas || {};
  const v = m.v || 0, i = m.i || 0, p = v * i;
  $("#roV").textContent = v.toFixed(3); $("#roI").textContent = i.toFixed(3); $("#roP").textContent = fmtP(p);
  $$(".ro").forEach((e) => e.classList.toggle("off", !on));
  $("#roVs").textContent = t("ajuste {v} V", { v: fmt(pr.v, 2) });
  $("#roIs").textContent = t("limite {i} A", { i: fmt(pr.i, 3) });
  const pmax = (pr.v || 0) * (pr.i || 0);
  $("#roPs").textContent = t("máx {p} W", { p: fmt(pmax, 2) });
  $("#barV").style.width = Math.min(100, pr.v ? v / pr.v * 100 : 0) + "%";
  $("#barI").style.width = Math.min(100, pr.i ? i / pr.i * 100 : 0) + "%";
  $("#barP").style.width = Math.min(100, pmax ? p / pmax * 100 : 0) + "%";
  const c = S.counters || {};
  $("#mWh").textContent = fmt(c.wh, 4) + " Wh";
  $("#mAh").textContent = fmt(c.mah, 1) + " mAh";
  $("#mTon").textContent = hms(c.on_time);
  $("#mR").textContent = on && i > 0.003 ? (v / i >= 1000 ? (v / i / 1000).toFixed(2) + " kΩ" : (v / i).toFixed(2) + " Ω") : "–";

  // automação
  const a = S.automation || {};
  $("#autoBadge").hidden = !a.running;
  if (a.running) $("#autoBadge").textContent = `▶ ${a.name} — ${a.step || ""}`;
  const pct = a.total ? Math.min(100, (a.done || 0) / a.total * 100) : 0;
  $("#progBar").style.width = (a.running ? pct : a.result ? 100 : 0) + "%";
  $("#progTxt").textContent = a.running ? `${a.name}: ${a.step || ""} ${a.loop ? "· " + t("ciclo {n}", { n: a.loop }) : ""}` :
    a.result ? t("Última execução: {name} — {result}", { name: a.name, result: a.result }) : t("Parado");
  $("#btnRun").disabled = !!a.running || !con; $("#btnStop").disabled = !a.running;
  if (a.running && !renderState._wasRunning) loadResults(true);
  if (!a.running && renderState._wasRunning) loadResults(true);
  renderState._wasRunning = !!a.running;

  // rodapé
  $("#stDot").className = "dot " + (con ? "ok" : S.last_error ? "bad" : "");
  $("#stTxt").textContent = con ? `${S.port} · ${S.link.toUpperCase()}${S.link === "uart" ? " " + S.baud : ""}` : t("Desconectado");
  $("#stRate").textContent = con ? t("{rate} amostras/s · {err} erros", { rate: S.stats.rate_hz, err: S.stats.errors }) : "";
  $("#stCsv").textContent = S.csv ? t("● gravando CSV") : "";
  $("#btnRec").classList.toggle("rec", !!S.csv);
  $("#btnRec").textContent = S.csv ? t("■ Parar CSV") : t("● Gravar CSV");
  $("#lcdCur").textContent = info.lcd ? `${info.lcd_raw} — ${info.lcd}` : "–";
}
function setToggle(sel, val) { const e = $(sel); if (document.activeElement !== e) e.checked = !!val; }

// ------------------------------------------------------------------ controles
$("#btnPower").onclick = () => S && act(() => api("/api/output", { on: !S.output.on }));
const STEPS = [[0.01, 0.001], [0.1, 0.01], [1, 0.1]];
function markDirty(k, d = true) { dirty[k] = d; $(k === "v" ? "#inV" : "#inI").classList.toggle("dirty", d); }
async function applySet(only) {
  const body = {};
  if (!only || only === "v") body.v = parseFloat($("#inV").value.replace(",", "."));
  if (!only || only === "i") body.i = parseFloat($("#inI").value.replace(",", "."));
  for (const k in body) if (isNaN(body[k])) delete body[k];
  try { await act(() => api("/api/setpoint", body)); markDirty("v", false); markDirty("i", false); } catch { }
}
let applyTimer = null;
function applySoon(k) { clearTimeout(applyTimer); applyTimer = setTimeout(() => applySet(k), 350); }
$$(".step").forEach((b) => b.onclick = () => {
  const k = b.dataset.k, d = +b.dataset.d, st = STEPS[+$("#selStep").value][k === "v" ? 0 : 1];
  const el = $(k === "v" ? "#inV" : "#inI");
  const lim = S?.limits || {};
  let x = (parseFloat(el.value) || 0) + d * st;
  x = Math.max(k === "v" ? lim.v_min ?? 0.5 : lim.i_min ?? 0.005, Math.min(k === "v" ? lim.v_max ?? 20 : lim.i_max ?? 3, x));
  el.value = x.toFixed(k === "v" ? 2 : 3); markDirty(k); applySoon(k);
});
["v", "i"].forEach((k) => {
  const inp = $(k === "v" ? "#inV" : "#inI"), rg = $(k === "v" ? "#rgV" : "#rgI");
  inp.oninput = () => markDirty(k);
  inp.onkeydown = (e) => { if (e.key === "Enter") applySet(); if (e.key === "Escape") { markDirty(k, false); inp.blur(); renderState(); } };
  rg.oninput = () => { inp.value = (+rg.value).toFixed(k === "v" ? 2 : 3); markDirty(k); };
  rg.onchange = () => applySet(k);
});
$("#btnApply").onclick = () => applySet();
$("#presets").onclick = async (e) => {
  const ed = e.target.closest("[data-ed]");
  if (ed) {
    const n = +ed.dataset.ed, p = S.presets[n];
    const r = await ask(t("Editar preset M{n}", { n }), `<div class="fields"><label>${t("Tensão (V)")}</label><input id="pdV" type="number" step="0.01" value="${p.v}">
      <label>${t("Corrente (A)")}</label><input id="pdI" type="number" step="0.001" value="${p.i}"></div>`, { ok: t("Salvar") });
    if (r) act(() => api("/api/setpoint", { preset: n, v: +$("#pdV").value, i: +$("#pdI").value }), t("Preset M{n} gravado", { n }));
    return;
  }
  const t = e.target.closest(".preset");
  if (t) act(() => api("/api/preset/select", { id: +t.dataset.id }));
};
const setting = (name, value) => act(() => api("/api/setting", { name, value }));
$("#tgOcp").onchange = (e) => setting("ocp", e.target.checked);
$("#tgDis").onchange = (e) => setting("discharge", e.target.checked);
$("#tgCal").onchange = (e) => setting("offset", e.target.checked);
$("#rgBright").oninput = (e) => { $("#lblBright").textContent = e.target.value + "%"; };
$("#rgBright").onchange = (e) => setting("brightness", +e.target.value);
$("#btnPd").onclick = () => setting("pd_voltage", parseFloat($("#inPd").value)).then(() => toast(t("Tensão PD solicitada"), "ok"));
$("#btnSave").onclick = () => act(() => api("/api/system/save", {}), t("Configuração salva na flash"));
$("#btnRefresh").onclick = () => act(() => api("/api/refresh", {}), t("Dados relidos"));
$("#btnReset").onclick = async () => { if (await ask(t("Reiniciar dispositivo?"), `<p>${t("A saída será desligada e a placa reinicia. A conexão é refeita automaticamente.")}</p>`, { ok: t("Reiniciar"), danger: true })) act(() => api("/api/system/reset", {}), t("Reset enviado")); };
$("#btnFactory").onclick = async () => {
  const r = await ask(t("Restaurar padrões de fábrica"), `<p>${t("Todos os ajustes e presets voltam ao padrão (a calibração é mantida).")} ${t("Digite <b>{w}</b> para confirmar.", { w: t("FABRICA") })}</p>`, { ok: t("Restaurar"), danger: true, expect: t("FABRICA") });
  if (r) act(() => api("/api/system/factory_reset", { confirm: "FACTORY_RESET" }), t("Restauração enviada"));
};
$("#btnCountersReset").onclick = () => act(() => api("/api/counters/reset", {}));
async function estop() {
  try { await api("/api/automation/stop", {}); } catch { }
  if (S && S.connected) await act(() => api("/api/output", { on: false }), t("Saída desligada"));
}
$("#btnEStop").onclick = estop;
document.addEventListener("keydown", (e) => {
  const tag = (document.activeElement?.tagName || "").toLowerCase();
  if ($("#dlg").open || ["input", "textarea", "select"].includes(tag)) return;
  if (e.code === "Space" && $("#tab-dash").classList.contains("active")) { e.preventDefault(); $("#btnPower").click(); }
  if (e.key === "Escape") estop();
});

// ------------------------------------------------------------------ gráfico principal
function grad(col) {
  return (u) => {
    if (!u.bbox || !isFinite(u.bbox.top) || !isFinite(u.bbox.height)) return col + "20";
    const g = u.ctx.createLinearGradient(0, u.bbox.top, 0, u.bbox.top + u.bbox.height);
    g.addColorStop(0, col + "40"); g.addColorStop(1, col + "00"); return g;
  };
}
function yRange(u, min, max) {
  if (min == null || max == null || !isFinite(min) || !isFinite(max)) return [0, 1];
  const nonneg = min >= 0;
  if (max - min < 0.02) { const c = (max + min) / 2; min = c - 0.01 - Math.abs(c) * 0.01; max = c + 0.01 + Math.abs(c) * 0.01; }
  const pad = (max - min) * 0.12;
  let lo = min - pad, hi = max + pad;
  if (nonneg && lo < 0) { hi -= lo * 0; lo = 0; }
  return [lo, hi];
}
// casas decimais conforme o passo das marcações do eixo
const tickFmt = (u, vals, ai, space, incr) => {
  const d = Math.min(4, Math.max(0, -Math.floor(Math.log10(incr || 1) + 1e-9)));
  return vals.map((x) => x == null ? "" : x.toFixed(d));
};
const timeFmt = (u, vals, ai, space, incr) => vals.map((t) => {
  const d = new Date(t * 1000);
  return incr >= 60 ? d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }) : d.toLocaleTimeString(LOCALE);
});
function shadeOff(u) {
  // sombreia períodos com a saída desligada
  const d = u.data, t = d[0], on = u._on; if (!t || !t.length || !on) return;
  const { ctx } = u, { top, height } = u.bbox;
  ctx.save(); ctx.fillStyle = "#ffffff07";
  let s = null;
  for (let k = 0; k <= t.length; k++) {
    const off = k < t.length && !on[k];
    if (off && s === null) s = k;
    if (!off && s !== null) {
      const x0 = u.valToPos(t[s], "x", true), x1 = u.valToPos(t[Math.min(k, t.length - 1)], "x", true);
      ctx.fillRect(x0, top, Math.max(1, x1 - x0), height); s = null;
    }
  }
  ctx.restore();
}
function drawMarkers(u) {
  const { ctx } = u, { left, top, width, height } = u.bbox;
  const xmin = u.scales.x.min, xmax = u.scales.x.max;
  ctx.save(); ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
  ctx.font = `${11 * devicePixelRatio}px Segoe UI`; ctx.textBaseline = "top";
  for (const m of markers) {
    if (m.t < xmin || m.t > xmax) continue;
    const x = u.valToPos(m.t, "x", true);
    ctx.strokeStyle = "#a78bfaaa"; ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]); ctx.lineWidth = devicePixelRatio;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + height); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = "#c4b5fd";
    ctx.fillText(m.label.slice(0, 40), x + 4 * devicePixelRatio, top + 4 * devicePixelRatio);
  }
  ctx.restore();
}
function mkChart() {
  const el = $("#chart");
  if (chart) chart.destroy();
  const axis = (scale, col, side, dec, show = true) => ({
    scale, side, stroke: col, show, size: 62, gap: 4, font: "12px Cascadia Mono, Consolas",
    grid: { show: side === 3, stroke: C.grid, width: 1 }, ticks: { stroke: C.grid },
    values: tickFmt,
  });
  const opts = {
    width: el.clientWidth || 600, height: Math.max(200, el.clientHeight - 30), pxAlign: false,
    cursor: { drag: { x: true, y: false, setScale: true }, points: { size: 7 } },
    legend: { live: true },
    scales: { x: { time: true }, V: { range: yRange }, A: { range: yRange }, W: { range: yRange } },
    axes: [
      { stroke: C.muted, font: "12px Segoe UI", grid: { stroke: C.grid }, ticks: { stroke: C.grid }, values: timeFmt, space: 70 },
      axis("V", C.v, 3, 2, showSeries[1]),
      axis("A", C.i, 1, 3, showSeries[2]),
      axis("W", C.p, 1, 2, showSeries[3]),
    ],
    series: [
      { label: t("Hora"), value: (u, t) => t == null ? "–" : new Date(t * 1000).toLocaleTimeString(LOCALE) },
      { label: t("Tensão"), scale: "V", stroke: C.v, width: 2, fill: grad(C.v), show: showSeries[1], value: (u, x) => x == null ? "–" : x.toFixed(3) + " V" },
      { label: t("Corrente"), scale: "A", stroke: C.i, width: 2, fill: grad(C.i), show: showSeries[2], value: (u, x) => x == null ? "–" : x.toFixed(3) + " A" },
      { label: t("Potência"), scale: "W", stroke: C.p, width: 1.6, show: showSeries[3], value: (u, x) => x == null ? "–" : x.toFixed(3) + " W" },
    ],
    hooks: {
      drawAxes: [shadeOff], draw: [drawMarkers],
      setSelect: [(u) => { if (u.select.width > 5) setPaused(true); }],
    },
  };
  chart = new uPlot(opts, [[], [], [], []], el);
  chart.over.addEventListener("dblclick", () => setPaused(false));
  needDraw = true;
}
function lowerBound(arr, x) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; } return lo; }
function drawChart() {
  if (!chart || paused || !needDraw || !$("#tab-dash").classList.contains("active")) return;
  needDraw = false;
  const n = BUF.t.length;
  let k = 0;
  if (winSec && n) k = lowerBound(BUF.t, BUF.t[n - 1] - winSec);
  chart._on = BUF.on.slice(k);
  chart.setData([BUF.t.slice(k), BUF.v.slice(k), BUF.i.slice(k), BUF.p.slice(k)], true);
  if (winSec && n) chart.setScale("x", { min: BUF.t[n - 1] - winSec, max: BUF.t[n - 1] });
  renderStats(k);
}
function renderStats(k) {
  const out = [];
  for (const [key, cls, u, d] of [["v", "v", "V", 3], ["i", "i", "A", 3], ["p", "p", "W", 3]]) {
    const a = BUF[key]; const n = a.length - k;
    if (n <= 0) { out.push(`<span>–</span>`); continue; }
    let mn = Infinity, mx = -Infinity, s = 0;
    for (let j = k; j < a.length; j++) { const x = a[j]; if (x < mn) mn = x; if (x > mx) mx = x; s += x; }
    out.push(`<span><b class="${cls}">${u}</b> ${t("mín {a} · máx {b} · méd {c} · pp {d}", { a: mn.toFixed(d), b: mx.toFixed(d), c: (s / n).toFixed(d), d: (mx - mn).toFixed(d) })}</span>`);
  }
  $("#statsbar").innerHTML = out.join("");
}
function setPaused(p) {
  paused = p; $("#btnPause").classList.toggle("paused", p);
  $("#btnPause").textContent = p ? t("▶ Continuar") : t("⏸ Pausar"); needDraw = true;
}
$("#btnPause").onclick = () => setPaused(!paused);
$$("#segWin button").forEach((b) => b.onclick = () => {
  $$("#segWin button").forEach((x) => x.classList.toggle("active", x === b));
  winSec = +b.dataset.w; setPaused(false); needDraw = true;
});
$$("#segSeries button").forEach((b) => b.onclick = () => {
  const s = +b.dataset.s; showSeries[s] = !showSeries[s]; b.classList.toggle("active", showSeries[s]); mkChart();
});
$("#btnMark").onclick = async () => { const l = await ask(t("Adicionar marcador"), `<p>${t("Texto que aparecerá no gráfico:")}</p>`, { ok: t("Marcar"), placeholder: t("ex.: carga conectada") }); if (l) act(() => api("/api/mark", { label: l })); };
$("#btnRec").onclick = () => act(() => api("/api/csv/" + (S?.csv ? "stop" : "start"), {})).then((r) => r.path && toast(t(S?.csv ? "Arquivo: {p}" : "Gravando em {p}", { p: r.path }), "ok", 7000));
$("#btnExport").onclick = async () => { const r = await act(() => api("/api/export", { seconds: winSec || null })); toast(t("Exportado: {p}", { p: r.path }), "ok", 7000); };
$("#btnClear").onclick = async () => { if (await ask(t("Limpar histórico?"), `<p>${t("Apaga os dados do gráfico (não afeta CSV em gravação).")}</p>`, { ok: t("Limpar"), danger: true })) { await api("/api/history/clear", {}); for (const k in BUF) BUF[k] = []; markers = []; needDraw = true; } };

function resizeCharts() {
  const el = $("#chart");
  if (chart && el.clientWidth) { chart.setSize({ width: el.clientWidth, height: Math.max(200, el.clientHeight - 30) }); needDraw = true; }
  const x = $("#xyChart");
  if (xy && x.clientWidth) xy.setSize({ width: x.clientWidth, height: 230 });
}
new ResizeObserver(() => resizeCharts()).observe($("#chart"));
(function loop() { drawChart(); requestAnimationFrame(loop); })();

// ------------------------------------------------------------------ automação: modelos
const TEMPLATES = [
  {
    id: "soft", name: t("Partida suave"), desc: t("Sobe a tensão em rampa até o alvo e mantém a saída ligada."),
    fields: [["v", t("Tensão final (V)"), 12], ["i", t("Limite de corrente (A)"), 1], ["t", t("Duração da rampa (s)"), 5], ["n", t("Nº de degraus"), 50]],
    build: (f) => ({
      name: t("Partida suave {v} V", { v: f.v }), on_end: "keep", steps: [
        { op: "set", v: 0.5, i: f.i }, { op: "output", on: true },
        { op: "ramp", param: "v", from: 0.5, to: f.v, step: +((f.v - 0.5) / f.n).toFixed(3), dwell: +(f.t / f.n).toFixed(3), record: false },
        { op: "log", msg: t("Tensão final {v} V atingida", { v: f.v }) }]
    }),
  },
  {
    id: "ivsweep", name: t("Varredura de tensão (curva I×V)"), desc: t("Varre a tensão medindo a corrente — LEDs, diodos, motores, cargas."),
    fields: [["v0", t("Tensão inicial (V)"), 1], ["v1", t("Tensão final (V)"), 5], ["dv", t("Passo (V)"), 0.1], ["i", t("Limite de corrente (A)"), 0.1], ["dw", t("Tempo por ponto (s)"), 1.0]],
    build: (f) => ({
      name: t("Curva I×V {a}–{b} V", { a: f.v0, b: f.v1 }), on_end: "off",
      guards: [{ cond: `i > ${(f.i * 1.2).toFixed(3)}`, action: "stop", msg: t("corrente acima do esperado") }],
      steps: [{ op: "set", v: f.v0, i: f.i }, { op: "output", on: true }, { op: "wait", s: 0.5 },
      { op: "ramp", param: "v", from: f.v0, to: f.v1, step: f.dv, dwell: f.dw, record: true }]
    }),
  },
  {
    id: "isweep", name: t("Varredura de corrente"), desc: t("Varre o limite de corrente com tensão fixa (ex.: teste de carga/LED em corrente)."),
    fields: [["v", t("Tensão (V)"), 5], ["i0", t("Corrente inicial (A)"), 0.05], ["i1", t("Corrente final (A)"), 1], ["di", t("Passo (A)"), 0.05], ["dw", t("Tempo por ponto (s)"), 1.2]],
    build: (f) => ({
      name: t("Varredura I {a}–{b} A", { a: f.i0, b: f.i1 }), on_end: "off",
      steps: [{ op: "set", v: f.v, i: f.i0 }, { op: "output", on: true }, { op: "wait", s: 0.5 },
      { op: "ramp", param: "i", from: f.i0, to: f.i1, step: f.di, dwell: f.dw, record: true }]
    }),
  },
  {
    id: "cccv", name: t("Carga de bateria CC/CV"), desc: t("Corrente constante até a tensão de carga, depois tensão constante até a corrente cair ao corte."),
    fields: [["chem", t("Química"), "liion", [["liion", t("Li-ion/LiPo 4,20 V/célula")], ["lihv", t("Li-HV 4,35 V/célula")], ["lfp", t("LiFePO4 3,65 V/célula")], ["pb", t("Chumbo-ácido 2,45 V/célula")], ["custom", t("Personalizado")]]],
    ["cells", t("Células em série"), 1], ["vc", t("Tensão de carga (V) — personalizado"), 4.2], ["ic", t("Corrente de carga (A)"), 0.5],
    ["cut", t("Corrente de corte (A)"), 0.05], ["h", t("Tempo máximo (h)"), 4]],
    build: (f) => {
      const per = { liion: 4.2, lihv: 4.35, lfp: 3.65, pb: 2.45 }[f.chem];
      const vc = +(per ? per * f.cells : f.vc).toFixed(3);
      return {
        name: t("Carga CC/CV {v} V {i} A", { v: vc, i: f.ic }), on_end: "off",
        guards: [{ cond: `v > ${(vc + 0.15).toFixed(2)}`, action: "stop", msg: t("sobretensão na bateria") },
        { cond: `t_on > ${f.h * 3600}`, action: "stop", msg: t("tempo máximo de carga") }],
        steps: [{ op: "reset_counters" }, { op: "set", v: vc, i: f.ic }, { op: "output", on: true },
        { op: "mark", label: t("Carga {v} V", { v: vc }) }, { op: "wait", s: 3 },
        { op: "wait_until", cond: "cc", timeout: 5, on_timeout: "continue" },
        { op: "log", msg: t("Fase CC (corrente constante)") },
        { op: "wait_until", cond: "cv", timeout: f.h * 3600, hold: 3 },
        { op: "log", msg: t("Fase CV (tensão constante)") }, { op: "mark", label: t("início CV") },
        { op: "wait_until", cond: `i < ${f.cut}`, timeout: f.h * 3600, hold: 10 },
        { op: "log", msg: t("Carga concluída: corrente abaixo do corte") }, { op: "mark", label: t("carga concluída") }]
      };
    },
  },
  {
    id: "cycle", name: t("Ciclagem liga/desliga"), desc: t("Liga e desliga repetidamente (burn-in, teste de partida)."),
    fields: [["v", t("Tensão (V)"), 5], ["i", t("Corrente (A)"), 1], ["ton", t("Tempo ligado (s)"), 5], ["toff", t("Tempo desligado (s)"), 3], ["n", t("Ciclos"), 10]],
    build: (f) => ({
      name: t("Ciclagem {n}×", { n: f.n }), on_end: "off", steps: [{ op: "set", v: f.v, i: f.i },
      { op: "loop", count: f.n, steps: [{ op: "output", on: true }, { op: "wait", s: f.ton }, { op: "measure", s: 0.5, label: t("fim do ON") }, { op: "output", on: false }, { op: "wait", s: f.toff }] }]
    }),
  },
  {
    id: "timer", name: t("Temporizador"), desc: t("Liga a saída por um tempo determinado."),
    fields: [["v", t("Tensão (V)"), 5], ["i", t("Corrente (A)"), 1], ["min", t("Duração (min)"), 10]],
    build: (f) => ({ name: t("Temporizador {m} min", { m: f.min }), on_end: "off", steps: [{ op: "set", v: f.v, i: f.i }, { op: "output", on: true }, { op: "wait", s: f.min * 60 }] }),
  },
  {
    id: "stairs", name: t("Escada de tensões"), desc: t("Aplica uma lista de tensões e mede cada uma."),
    fields: [["list", t("Tensões (V, separadas por vírgula)"), "3.3, 5, 9, 12"], ["i", t("Corrente (A)"), 1], ["dw", t("Tempo em cada (s)"), 3]],
    build: (f) => {
      const vs = String(f.list).split(/[;,\s]+/).map(Number).filter((x) => x > 0);
      return {
        name: t("Escada {list} V", { list: vs.join("/") }), on_end: "off", steps: [{ op: "set", v: vs[0], i: f.i }, { op: "output", on: true },
        ...vs.flatMap((v) => [{ op: "set", v }, { op: "wait", s: Math.max(0, f.dw - 1) }, { op: "measure", s: 1, label: `${v} V` }])]
      };
    },
  },
  {
    id: "energy", name: t("Limite de carga/energia"), desc: t("Mantém a saída até entregar uma quantidade de mAh ou Wh."),
    fields: [["v", t("Tensão (V)"), 5], ["i", t("Corrente (A)"), 1], ["what", t("Limite por"), "mah", [["mah", "mAh"], ["wh", "Wh"]]], ["lim", t("Valor"), 500]],
    build: (f) => ({
      name: t("Até {lim} {what}", { lim: f.lim, what: f.what }), on_end: "off", steps: [{ op: "reset_counters" }, { op: "set", v: f.v, i: f.i }, { op: "output", on: true },
      { op: "wait_until", cond: `${f.what} >= ${f.lim}` }, { op: "log", msg: t("{lim} {what} entregues", { lim: f.lim, what: f.what }) }]
    }),
  },
];
const DEFAULT_SWEEP = { v0: 1, v1: 5, dv: 0.25, i: 0.1, dw: 1.0 };
let curTpl = null;
$("#tplList").innerHTML = TEMPLATES.map((t) => `<button class="tpl" data-t="${t.id}"><b>${esc(t.name)}</b><small>${esc(t.desc)}</small></button>`).join("");
$("#tplList").onclick = (e) => {
  const b = e.target.closest(".tpl"); if (!b) return;
  $$(".tpl").forEach((x) => x.classList.toggle("active", x === b));
  curTpl = TEMPLATES.find((t) => t.id === b.dataset.t);
  $("#tplForm").hidden = false; $("#tplTitle").textContent = curTpl.name; $("#tplDesc").textContent = curTpl.desc;
  $("#tplFields").innerHTML = curTpl.fields.map(([k, l, d, opts]) => `<label>${esc(l)}</label>` + (opts
    ? `<select data-f="${k}">${opts.map(([v, t]) => `<option value="${v}"${v === d ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>`
    : `<input data-f="${k}" value="${esc(d)}" ${typeof d === "number" ? 'type="number" step="any"' : ""}>`)).join("");
};
$("#btnTplGen").onclick = () => {
  const f = {};
  $$("[data-f]", $("#tplFields")).forEach((e) => { const v = e.value; f[e.dataset.f] = e.type === "number" ? parseFloat(v) : v; });
  $("#seqEditor").value = JSON.stringify(curTpl.build(f), null, 2);
  toast(t("Sequência gerada — revise e clique em Executar"), "info");
};
function readSeq() { try { return JSON.parse($("#seqEditor").value); } catch (e) { throw new Error(t("JSON inválido: {e}", { e: e.message })); } }
$("#btnSeqValidate").onclick = () => act(async () => { const r = await api("/api/automation/validate", { sequence: readSeq() }); toast(t("Sequência válida ({n} passos)", { n: r.steps }), "ok"); });
$("#btnRun").onclick = () => act(async () => { await api("/api/automation/run", { sequence: readSeq() }); lastSweepLen = -1; });
$("#btnStop").onclick = () => act(() => api("/api/automation/stop", {}));
$("#btnSeqHelp").onclick = () => ask(t("Referência de passos"), `<pre class="code">${esc(tl(SEQ_HELP))}</pre>`, { ok: t("Fechar") });
const SEQ_HELP = {
  pt: `{"name": "...", "on_end": "off"|"keep",
 "guards": [{"cond": "p > 15", "action": "off"|"stop", "msg": "..."}],
 "steps": [ ... ]}

Passos:
  {"op":"set", "v":5.0, "i":1.0}           ajusta o preset ativo
  {"op":"output", "on":true}
  {"op":"wait", "s":2}
  {"op":"preset", "id":2}                   (saída desligada)
  {"op":"setting", "name":"ocp|offset|discharge|brightness|pd_voltage", "value":1}
  {"op":"ramp", "param":"v"|"i", "from":1, "to":12, "step":0.5, "dwell":1, "settle":0.65, "record":true}
  {"op":"wait_until", "cond":"i < 0.05", "timeout":3600, "hold":5, "on_timeout":"fail"|"continue"}
  {"op":"measure", "s":1, "label":"ponto A"}
  {"op":"loop", "count":3, "steps":[ ... ]}
  {"op":"log", "msg":"..."}   {"op":"mark", "label":"..."}
  {"op":"reset_counters"}     {"op":"save_config"}

Variáveis nas condições:
  v, i, p (medidas) · vset, iset (ajuste) · on, cv, cc, oc
  t (s desde o início) · t_on (s ligada) · wh, mah · vin
  funções: abs, min, max, round, sqrt — operadores and/or/not`,
  en: `{"name": "...", "on_end": "off"|"keep",
 "guards": [{"cond": "p > 15", "action": "off"|"stop", "msg": "..."}],
 "steps": [ ... ]}

Steps:
  {"op":"set", "v":5.0, "i":1.0}           sets the active preset
  {"op":"output", "on":true}
  {"op":"wait", "s":2}
  {"op":"preset", "id":2}                   (output off)
  {"op":"setting", "name":"ocp|offset|discharge|brightness|pd_voltage", "value":1}
  {"op":"ramp", "param":"v"|"i", "from":1, "to":12, "step":0.5, "dwell":1, "settle":0.65, "record":true}
  {"op":"wait_until", "cond":"i < 0.05", "timeout":3600, "hold":5, "on_timeout":"fail"|"continue"}
  {"op":"measure", "s":1, "label":"point A"}
  {"op":"loop", "count":3, "steps":[ ... ]}
  {"op":"log", "msg":"..."}   {"op":"mark", "label":"..."}
  {"op":"reset_counters"}     {"op":"save_config"}

Condition variables:
  v, i, p (measured) · vset, iset (setpoint) · on, cv, cc, oc
  t (s since start) · t_on (s with output on) · wh, mah · vin
  functions: abs, min, max, round, sqrt — operators and/or/not`,
  zh: `{"name": "...", "on_end": "off"|"keep",
 "guards": [{"cond": "p > 15", "action": "off"|"stop", "msg": "..."}],
 "steps": [ ... ]}

步骤：
  {"op":"set", "v":5.0, "i":1.0}           设置当前预设
  {"op":"output", "on":true}
  {"op":"wait", "s":2}
  {"op":"preset", "id":2}                   （需关闭输出）
  {"op":"setting", "name":"ocp|offset|discharge|brightness|pd_voltage", "value":1}
  {"op":"ramp", "param":"v"|"i", "from":1, "to":12, "step":0.5, "dwell":1, "settle":0.65, "record":true}
  {"op":"wait_until", "cond":"i < 0.05", "timeout":3600, "hold":5, "on_timeout":"fail"|"continue"}
  {"op":"measure", "s":1, "label":"A 点"}
  {"op":"loop", "count":3, "steps":[ ... ]}
  {"op":"log", "msg":"..."}   {"op":"mark", "label":"..."}
  {"op":"reset_counters"}     {"op":"save_config"}

条件变量：
  v, i, p（实测）· vset, iset（设定）· on, cv, cc, oc
  t（开始后的秒数）· t_on（输出开启秒数）· wh, mah · vin
  函数：abs, min, max, round, sqrt — 运算符 and/or/not`,
};

async function loadSeqs() {
  const s = await api("/api/sequences").catch(() => ({}));
  $("#selSeq").innerHTML = Object.keys(s).map((k) => `<option>${esc(k)}</option>`).join("") || `<option value="">${t("(nenhuma)")}</option>`;
  loadSeqs.cache = s;
}
$("#btnSeqSave").onclick = async () => {
  let seq; try { seq = readSeq(); } catch (e) { return toast(e.message, "error"); }
  const name = await ask(t("Salvar sequência"), `<p>${t("Nome:")}</p>`, { ok: t("Salvar"), placeholder: seq.name || t("minha sequência") });
  if (name) { await act(() => api("/api/sequences", { name, sequence: seq }), t("Sequência salva")); loadSeqs(); }
};
$("#btnSeqLoad").onclick = () => { const s = loadSeqs.cache?.[$("#selSeq").value]; if (s) $("#seqEditor").value = JSON.stringify(s, null, 2); };
$("#btnSeqDel").onclick = async () => { const n = $("#selSeq").value; if (n && await ask(t("Excluir sequência?"), `<p>${esc(n)}</p>`, { ok: t("Excluir"), danger: true })) { await act(() => api("/api/sequences", { name: n, delete: true })); loadSeqs(); } };

// ------------------------------------------------------------------ automação: resultados
let results = { sweep: [], measures: [] };
async function loadResults(force) {
  const r = await api("/api/automation").catch(() => null); if (!r) return;
  results = r.results || { sweep: [], measures: [] };
  const sw = results.sweep || [], me = results.measures || [];
  if (force || sw.length !== lastSweepLen) { lastSweepLen = sw.length; drawXY(); }
  if (force || me.length !== lastMeasLen) {
    lastMeasLen = me.length;
    $("#measTable").innerHTML = me.length ? `<table><tr><th>${t("Rótulo")}</th><th>V</th><th>A</th><th>W</th></tr>${me.map((m) =>
      `<tr><td style="text-align:left">${esc(m.label)}</td><td>${fmt(m.v)}</td><td>${fmt(m.i, 4)}</td><td>${fmt(m.p)}</td></tr>`).join("")}</table>` : "";
  }
}
setInterval(() => { if (S?.automation?.running || $("#tab-auto").classList.contains("active")) loadResults(); }, 1000);
function drawXY() {
  const el = $("#xyChart");
  const sw = [...(results.sweep || [])];
  const param = sw[0]?.param || "v";
  const xk = xyMode === "set" ? "set" : "v";
  sw.sort((a, b) => a[xk] - b[xk]);
  const xs = sw.map((p) => p[xk]);
  const ys2 = param === "v" ? sw.map((p) => p.i) : sw.map((p) => p.v);
  const data = [xs, ys2, sw.map((p) => p.p)];
  const yLbl = param === "v" ? [t("Corrente"), "A", C.i, 4] : [t("Tensão"), "V", C.v, 3];
  const xLbl = xyMode === "set" ? (param === "v" ? t("V ajuste") : t("A ajuste")) : t("V medida");
  if (xy) xy.destroy();
  xy = new uPlot({
    width: el.clientWidth || 380, height: 230, pxAlign: false,
    scales: { x: { time: false }, Y: { range: yRange }, W: { range: yRange } },
    cursor: { points: { size: 8 } },
    axes: [
      { stroke: C.muted, grid: { stroke: C.grid }, ticks: { stroke: C.grid }, label: xLbl, labelSize: 18, font: "11px Cascadia Mono", labelFont: "12px Segoe UI", values: tickFmt },
      { scale: "Y", stroke: yLbl[2], grid: { stroke: C.grid }, size: 56, font: "11px Cascadia Mono", values: tickFmt },
      { scale: "W", side: 1, stroke: C.p, grid: { show: false }, size: 50, font: "11px Cascadia Mono", values: tickFmt },
    ],
    series: [
      { label: xLbl, value: (u, x) => x == null ? "–" : x.toFixed(3) },
      { label: yLbl[0], scale: "Y", stroke: yLbl[2], width: 2, fill: grad(yLbl[2]), points: { show: true, size: 5, fill: yLbl[2] }, value: (u, x) => x == null ? "–" : x.toFixed(yLbl[3]) + " " + yLbl[1] },
      { label: t("Potência"), scale: "W", stroke: C.p, width: 1.5, dash: [5, 4], points: { show: false }, value: (u, x) => x == null ? "–" : x.toFixed(3) + " W" },
    ],
  }, data, el);
}
$$("#segXY button").forEach((b) => b.onclick = () => { $$("#segXY button").forEach((x) => x.classList.toggle("active", x === b)); xyMode = b.dataset.x; drawXY(); });
$("#btnSweepCsv").onclick = () => {
  const sw = results.sweep || [];
  if (!sw.length) return toast(t("Sem dados de varredura"), "warn");
  saveText(`${t("varredura")}_${Date.now()}.csv`, "param,set,V,A,W,mode,n\n" + sw.map((p) => [p.param, p.set, p.v, p.i, p.p, p.mode, p.n].join(",")).join("\n") + "\n");
};

// ------------------------------------------------------------------ log
function appendLogs(logs) {
  const box = $("#logBox");
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
  for (const l of logs) {
    const d = document.createElement("div"); d.className = "l " + l.level;
    d.innerHTML = `<time>${new Date(l.t * 1000).toLocaleTimeString(LOCALE)}</time>${esc(l.msg)}`;
    box.appendChild(d);
    if (l.level === "error" && Date.now() / 1000 - l.t < 5) toast(l.msg, "error", 8000);
  }
  while (box.childElementCount > 600) box.firstChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
  const last = logs[logs.length - 1]; $("#stLast").textContent = last.msg;
}

// ------------------------------------------------------------------ guardas
function renderGuards(list, enabled) {
  $("#tgGuards").checked = enabled;
  $("#guardList").innerHTML = list.map((g, n) => guardRow(g)).join("") || `<p class="hint">${t("Nenhuma guarda.")}</p>`;
}
const guardRow = (g) => `<div class="guard">
  <label class="toggle inline"><input type="checkbox" class="g-en" ${g.enabled !== false ? "checked" : ""}><span></span></label>
  <input class="cond" value="${esc(g.cond)}" placeholder="${t("ex.: p > 30")}">
  <select class="g-act"><option value="off" ${g.action !== "stop" ? "selected" : ""}>${t("desligar")}</option><option value="stop" ${g.action === "stop" ? "selected" : ""}>${t("deslig.+parar")}</option></select>
  <input class="g-msg" value="${esc(g.msg || "")}" placeholder="${t("mensagem")}">
  <button class="ghost g-del" title="${t("Remover")}">✕</button></div>`;
$("#btnGuardAdd").onclick = () => { $("#guardList").querySelector(".hint")?.remove(); $("#guardList").insertAdjacentHTML("beforeend", guardRow({ cond: "", action: "off" })); };
$("#guardList").onclick = (e) => { if (e.target.closest(".g-del")) e.target.closest(".guard").remove(); };
$("#btnGuardSave").onclick = async () => {
  const guards = $$(".guard", $("#guardList")).map((r) => ({ cond: $(".cond", r).value.trim(), action: $(".g-act", r).value, msg: $(".g-msg", r).value.trim(), enabled: $(".g-en", r).checked })).filter((g) => g.cond);
  const c = await act(() => api("/api/config", { guards, guards_enabled: $("#tgGuards").checked }), t("Guardas salvas"));
  renderGuards(c.guards, c.guards_enabled);
};
$("#tgGuards").onchange = (e) => act(() => api("/api/config", { guards_enabled: e.target.checked }));

// ------------------------------------------------------------------ avançado: calibração
let fdData = null;
function renderFd(d) {
  fdData = d;
  const row = (lbl, unit, dk, sk, n) => `<tr><td>${lbl} ${n}</td><td><input type="number" data-k="${dk}" data-n="${n}" value="${d[dk][n]}"></td>
    <td><input type="number" data-k="${sk}" data-n="${n}" value="${d[sk][n]}"></td><td class="err">${d[dk][n] - d[sk][n] >= 0 ? "+" : ""}${d[dk][n] - d[sk][n]} ${unit}</td></tr>`;
  let h = `<tr><th>${t("Ponto")}</th><th>${t("Exibido (d)")}</th><th>${t("Ajuste (s)")}</th><th>d − s</th></tr>`;
  for (let n = 0; n < 7; n++) h += row(t("Tensão"), "mV", "d_v", "s_v", n);
  for (let n = 0; n < 7; n++) h += row(t("Corrente"), "mA", "d_a", "s_a", n);
  h += `<tr><td>Offset 0 A (s1)</td><td colspan="3"><input type="number" data-k="s_0a_s1" value="${d.s_0a_s1}"></td></tr>
        <tr><td>Offset 0 A (s0)</td><td colspan="3"><input type="number" data-k="s_0a_s0" value="${d.s_0a_s0}"></td></tr>
        <tr><td>${t("Tipo LCD")}</td><td colspan="3"><input type="number" data-k="lcd_type" value="${d.lcd_type}" min="0" max="1"></td></tr>`;
  $("#fdTable").innerHTML = h;
}
function readFd() {
  const d = JSON.parse(JSON.stringify(fdData));
  $$("#fdTable input").forEach((e) => { const k = e.dataset.k; if (e.dataset.n !== undefined) d[k][+e.dataset.n] = parseInt(e.value); else d[k] = parseInt(e.value); });
  return d;
}
$("#btnFdRead").onclick = () => act(async () => renderFd(await api("/api/factory")), t("Calibração lida"));
$("#btnFdExport").onclick = () => { if (!fdData) return toast(t("Leia a tabela primeiro"), "warn"); saveText(`${t("calibracao")}_${S?.info?.serial || "pdpower"}.json`, JSON.stringify({ serial: S?.info?.serial, ...readFd() }, null, 2)); };
$("#fdFile").onchange = async (e) => { const f = e.target.files[0]; if (!f) return; try { renderFd(JSON.parse(await f.text())); toast(t("Arquivo carregado na tabela (ainda não gravado)"), "info"); } catch (er) { toast(t("JSON inválido"), "error"); } e.target.value = ""; };
$("#btnFdWrite").onclick = async () => {
  if (!fdData) return toast(t("Leia a tabela primeiro"), "warn");
  const d = readFd();
  const r = await ask(t("Gravar calibração no dispositivo"), `<p>${t("Valores errados deixam as leituras e a saída imprecisas. Um backup da tabela atual será salvo antes.")}</p><p>${t("Digite <b>{w}</b> para confirmar.", { w: t("GRAVAR") })}</p>`, { ok: t("Gravar"), danger: true, expect: t("GRAVAR") });
  if (r) { const res = await act(() => api("/api/factory", { confirm: "WRITE_CALIBRATION", data: d })); renderFd(res.data); toast(t("Gravado e verificado. Backup: {p}", { p: res.backup }), "ok", 9000); }
};
$("#btnLcd").onclick = async () => {
  const lt = +$("#selLcd").value;
  const r = await ask(t("Gravar tipo de painel LCD"), `<p>${t("Gravar <b>{t}</b> na memória não volátil?", { t: lt === 0 ? "HAN" : "BOE" })} ${t("Digite <b>{w}</b> para confirmar.", { w: "LCD" })}</p>`, { ok: t("Gravar"), danger: true, expect: "LCD" });
  if (r) act(() => api("/api/lcd", { type: lt, confirm: "LCD" }), t("Tipo de LCD gravado"));
};

// ------------------------------------------------------------------ console
const QUICK = [["81", "WHO_AM_I"], ["C2", "VERSION"], ["C3", "SERIAL"], ["82", "OUT_STATE"], ["83", "OUT_ID"], ["84 00", "DATA M0"],
["85", "DISPLAY"], ["86", "OCP"], ["87", "OFFSET"], ["88", "BRILHO"], ["8A", "ENTRADA"], ["C6", "LCD"], ["C7", "FACTORY"]];
$("#quickCmds").innerHTML = QUICK.map(([h, l]) => `<button class="ghost" data-h="${h}" title="${h}">${t(l)}</button>`).join("");
$("#quickCmds").onclick = (e) => { const b = e.target.closest("[data-h]"); if (b) { $("#rawHex").value = b.dataset.h; sendRaw(); } };
async function sendRaw() {
  let bytes = $("#rawHex").value.replace(/0x/gi, "").split(/[\s,]+/).filter(Boolean).map((x) => parseInt(x, 16));
  if (!bytes.length || bytes.some(isNaN)) return toast(t("Hex inválido"), "error");
  if ($("#rawTerm").checked) bytes.push(S?.link === "uart" ? crc8(bytes) : 0x0a);
  const hex = bytes.map((b) => b.toString(16).padStart(2, "0")).join(" ");
  const r = await act(() => api("/api/raw", { hex }));
  const box = $("#rawLog");
  const asc = r.ascii.replace(/[^\x20-\x7e]/g, "·");
  box.insertAdjacentHTML("beforeend", `<div class="l"><span class="info">→ ${esc(r.sent)}</span>\n<span class="ok">← ${esc(r.received || t("(sem resposta)"))}</span>  <span class="muted">${esc(asc)}</span></div>`);
  box.scrollTop = box.scrollHeight;
}
$("#btnRaw").onclick = sendRaw;
$("#rawHex").onkeydown = (e) => { if (e.key === "Enter") sendRaw(); };
const REF = [
  ["0x02 / 0x82", "OUTPUT_EN", "Liga/desliga saída · leitura: bit0 = ligada, bits2..1 = 00 CV / 01 CC / 10 sobrecorrente"],
  ["0x03 / 0x83", "OUTPUT_ID", "Preset ativo 0–4"],
  ["0x04 / 0x84", "OUTPUT_DATA", "id, tensão (mV, u16 LE), corrente (mA, u16 LE)"],
  ["0x85", "OUTPUT_DISPLAY", "Tensão e corrente medidas (mV, mA)"],
  ["0x06 / 0x86", "OUTPUT_OCP_EN", "Proteção de sobrecorrente"],
  ["0x07 / 0x87", "OUTPUT_OFFSET_EN", "Aplicar calibração"],
  ["0x08 / 0x88", "BRIGHTNESS", "Brilho 0–100 %"],
  ["0x09", "OUTPUT_DISCHARGE_EN", "Descarga ativa (sem leitura)"],
  ["0x0A / 0x8A", "INPUT_STATE / PD_VOLTAGE", "Escrita: tensão PD pedida (0,1 V) · leitura: tipo de entrada, tensão (mV), PD pedida"],
  ["0x40", "SYSTEM_RESET", "Reinicia"],
  ["0xC2", "SYSTEM_VERSION", "Versão do firmware"],
  ["0xC3", "SYSTEM_SERIAL_NUM", "Número de série (AC = V1, B3 = V1.1)"],
  ["0x44", "SYSTEM_CONFIG_SAVE", "Salva preset ativo, presets, OCP, calibração, brilho, PD na flash"],
  ["0x45", "SYSTEM_FACTORY_RESET", "Padrões de fábrica (mantém calibração)"],
  ["0x46 / 0xC6", "SYSTEM_LCD_PANEL_TYPE", "0 = HAN, 1 = BOE (não volátil)"],
  ["0x47 / 0xC7", "SYSTEM_FACTORY_DATA", "Tabela de calibração (64 bytes)"],
  ["0x81", "WHO_AM_I", "Nome do dispositivo"],
];
$("#refTable").innerHTML = `<tr><th>${t("Código")}</th><th>${t("Comando")}</th><th>${t("Descrição")}</th></tr>` + REF.map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${esc(t(r[2]))}</td></tr>`).join("") +
  `<tr><td colspan="3" class="hint">${t("USB: quadro termina em 0x0A · UART: termina em CRC-8 (poli 0x31, init 0xFF). Leitura = comando | 0x80. Escritas não têm resposta.")}</td></tr>`;

// ------------------------------------------------------------------ config
async function loadConfig() {
  cfg = await api("/api/config").catch(() => null); if (!cfg) return;
  $("#limV").value = cfg.limits.v_max ?? ""; $("#limI").value = cfg.limits.i_max ?? "";
  $("#limVm").textContent = t("modelo: {x}", { x: (S?.limits?.model_v_max ?? 20) + " V" }); $("#limIm").textContent = t("modelo: {x}", { x: (S?.limits?.model_i_max ?? 3) + " A" });
  $("#pollMs").value = Math.round(cfg.poll_interval * 1000);
  $("#tgAutoConn").checked = cfg.connection.autoconnect !== false;
  $("#tgMcpWrite").checked = cfg.mcp?.allow_write !== false;
  $("#pathCfg").textContent = cfg.paths.config; $("#pathLogs").textContent = cfg.paths.logs;
  renderGuards(cfg.guards || [], cfg.guards_enabled !== false);
}
$("#btnLimSave").onclick = () => act(() => api("/api/config", { limits: { v_max: $("#limV").value === "" ? null : +$("#limV").value, i_max: $("#limI").value === "" ? null : +$("#limI").value } }), t("Limites salvos"));
$("#btnAcqSave").onclick = () => act(() => api("/api/config", { poll_interval: +$("#pollMs").value / 1000, connection: { autoconnect: $("#tgAutoConn").checked } }), t("Salvo"));
$("#tgMcpWrite").onchange = (e) => act(() => api("/api/config", { mcp: { allow_write: e.target.checked } }), e.target.checked ? t("MCP pode controlar a fonte") : t("MCP somente leitura"));
const mcpUrl = `${location.origin}/mcp`;
$("#mcpUrl").textContent = mcpUrl; $("#stMcp").textContent = mcpUrl;
$("#mcpCmd").textContent = `claude mcp add --transport http --scope user pdpower ${mcpUrl}`;
$("#btnMcpCopy").onclick = () => { navigator.clipboard.writeText($("#mcpCmd").textContent).then(() => toast(t("Copiado"), "ok")); };

// ------------------------------------------------------------------ init
(async function init() {
  mkChart();
  await Promise.all([loadPorts(), reloadHistory(), loadSeqs(), loadConfig()]);
  try {
    const st = await api("/api/state"); S = st; renderState();
    if (st.link) $("#selLink").value = st.link;
  } catch { }
  $("#seqEditor").value = JSON.stringify(TEMPLATES[1].build(DEFAULT_SWEEP), null, 2);
  try {
    const keep = JSON.parse(sessionStorage.getItem("pd_keep") || "null");
    sessionStorage.removeItem("pd_keep");
    if (keep?.seq) $("#seqEditor").value = keep.seq;
    const tab = keep?.tab || location.hash.slice(1);
    if (tab) $(`.tabs > button[data-tab="${CSS.escape(tab)}"]`)?.click();
  } catch { }
  connectWS();
  loadResults(true);
})();
