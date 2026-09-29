import { Scene, signals } from "./scene.js";

const $ = (id) => document.getElementById(id);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const usd = (value) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      }).format(value);
const pct = (value) => (value == null ? "—" : (value * 100).toFixed(1) + "%");
const num = (value) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("en-US", { maximumFractionDigits: 3 });
const date = (value) =>
  new Date(value * 1000).toISOString().replace("T", " ").slice(0, 19);
let snapshot = null,
  csrf = "",
  selected = "",
  tab = "portfolio",
  fills = [],
  decisions = [],
  logs = [],
  busy = false,
  sceneValues = {};
const mini = new Scene($("mini-scene"), true);
const scene = new Scene($("network-scene"), false, (id) => {
  $("node-detail").textContent =
    `${signals.find((s) => s[0] === id)[1]}: ${sceneValues[id] ?? "No observation yet"}. Values come from the latest persisted decision.`;
});
const titles = {
  portfolio: [
    "Portfolio overview",
    "A clear view of capital, exposure, and every decision behind it.",
  ],
  decisions: [
    "Intelligence lab",
    "Train on evidence. Inspect decisions. Earn the next stage.",
  ],
  network: [
    "Neural space",
    "Explore the signals and constraints behind the latest decision.",
  ],
  logs: [
    "Event journal",
    "A durable record of observations, outcomes, and interventions.",
  ],
};

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  setTimeout(() => ($("toast").hidden = true), 5500);
}
async function api(path, body) {
  const response = await fetch(
    path,
    body
      ? {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-QST-Token": csrf,
          },
          body: JSON.stringify(body),
        }
      : {},
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
function options(select, items, value, label) {
  const previous = select.value;
  select.replaceChildren(
    ...items.map((item) => {
      const option = document.createElement("option");
      option.value = value(item);
      option.textContent = label(item);
      return option;
    }),
  );
  if (items.some((item) => value(item) === previous)) select.value = previous;
}
function setTab(next) {
  tab = next;
  document
    .querySelectorAll(".tab-panel")
    .forEach((p) => (p.hidden = p.id !== tab));
  document.querySelectorAll(".nav").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
    if (button.dataset.tab === tab) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("page-title").replaceChildren(
    document.createTextNode(titles[tab][0]),
    Object.assign(document.createElement("span"), { textContent: "." }),
  );
  $("page-description").textContent = titles[tab][1];
  if (tab === "network") scene.draw();
  if (tab === "portfolio") chart();
}
document
  .querySelectorAll(".nav")
  .forEach((button) =>
    button.addEventListener("click", () => setTab(button.dataset.tab)),
  );
function render() {
  const s = snapshot.state,
    m = snapshot.metrics,
    t = snapshot.telemetry,
    job = snapshot.job;
  $("connection").textContent = "ENGINE ONLINE";
  $("notice").classList.toggle("error", job.stage === "failed");
  $("cpu").textContent =
    t.cpu_percent == null ? "N/A" : t.cpu_percent.toFixed(0) + "%";
  $("cpu").title = t.cpu_note || "Host CPU utilization";
  $("ram").textContent =
    t.ram_used == null
      ? "N/A"
      : (t.ram_used / 2 ** 30).toFixed(1) +
        " / " +
        (t.ram_total / 2 ** 30).toFixed(0) +
        " GB";
  $("disk").textContent =
    (t.disk_used / 2 ** 30).toFixed(0) +
    " / " +
    (t.disk_total / 2 ** 30).toFixed(0) +
    " GB";
  $("uptime").textContent = [
    Math.floor(t.uptime / 3600),
    Math.floor(t.uptime / 60) % 60,
    Math.floor(t.uptime) % 60,
  ]
    .map((x) => String(x).padStart(2, "0"))
    .join(":");
  const provenance =
    s?.mode === "demo"
      ? "SYNTHETIC DEMO"
      : s?.mode === "replay"
        ? "HISTORICAL REPLAY"
        : s?.mode === "paper"
          ? "LIVE PAPER"
          : s?.mode?.toUpperCase() || "NO SESSION";
  $("mode").textContent = provenance;
  $("notice").textContent =
    job.stage === "failed"
      ? job.message
      : s?.mode === "demo"
        ? "Synthetic stress data • This run tests software behavior. Its performance is not evidence of a trading edge."
        : s
          ? `${provenance} • ${s.source} • ${job.message}`
          : "Start in Intelligence lab: run a synthetic demo or download historical SOL/USDC data. No wallet is required.";
  options(
    $("session"),
    snapshot.sessions,
    (x) => x.id,
    (x) => `${x.mode.toUpperCase()} / ${x.id}`,
  );
  if (snapshot.session) $("session").value = snapshot.session;
  options(
    $("dataset"),
    snapshot.datasets,
    (x) => x.source,
    (x) => `${x.source} · ${num(x.count)} bars`,
  );
  $("equity").textContent = usd(m?.equity);
  $("pnl").textContent = m
    ? `${usd(m.pnl)} total P&L · ${usd(Number(s.cash))} cash`
    : "No performance data";
  $("pnl").className = m?.pnl < 0 ? "negative" : "positive";
  $("realized").textContent = usd(m?.realized);
  $("fees").textContent = m
    ? `${usd(m.fees)} fees + gas assumptions`
    : "After execution costs";
  $("win-rate").textContent = pct(m?.win_rate);
  $("loss-rate").textContent = m
    ? `${pct(m.loss_rate)} loss rate · ${m.closed} closed`
    : "No closed trades";
  $("drawdown").textContent = pct(m?.max_drawdown);
  $("agent-stage").textContent = s?.killed
    ? "Kill switch latched"
    : s?.halt_reason
      ? "Risk halt"
      : s?.paused
        ? "Entries paused"
        : s
          ? `${num(s.model.n)} learned outcomes`
          : "Awaiting first run";
  $("agent-detail").textContent =
    s?.last_decision?.reasons.join(" · ") ||
    "CPU-native learning. Persistent state.";
  const pos = s?.position;
  $("position").className = "position-body" + (pos ? "" : " empty");
  $("position").innerHTML = pos
    ? `<span><small>ASSET</small>SOL / USDC</span><span><small>QUANTITY</small>${escape(num(pos.quantity))} SOL</span><span><small>ENTRY</small>${escape(usd(pos.entry))}</span><span><small>UNREALIZED</small>${escape(usd(Number(pos.quantity) * s.latest_price - Number(pos.cost)))}</span>`
    : "No open position. Cash is a valid decision.";
  const policy = s?.policy || {
    allocation: 0.1,
    daily_loss: 0.02,
    max_drawdown: 0.08,
  };
  $("risk-limits").innerHTML =
    `<span>Position cap <b>${pct(policy.allocation)}</b></span><span>Daily loss limit <b>${pct(policy.daily_loss)}</b></span><span>Drawdown halt <b>${pct(policy.max_drawdown)}</b></span>`;
  $("pause").textContent = s?.paused ? "Resume entries" : "Pause entries";
  $("pause").disabled =
    !s ||
    !snapshot.active_engine ||
    (selected && selected !== snapshot.active_session) ||
    Boolean(s.killed || s.halt_reason);
  $("kill").disabled =
    !s ||
    !snapshot.active_engine ||
    (selected && selected !== snapshot.active_session) ||
    Boolean(s.killed);
  document
    .querySelectorAll("[data-run]")
    .forEach((button) => (button.disabled = snapshot.worker_running));
  $("stop-worker").disabled = !snapshot.worker_running;
  $("use-jev").disabled = !snapshot.providers.jev;
  $("use-laya").disabled = !snapshot.providers.laya;
  $("use-jev").title = snapshot.providers.jev
    ? "Hosted Jev reviewer enabled per run"
    : "Paid hosted AI is disabled by default; a key alone does not enable calls";
  $("cost-policy").textContent = snapshot.providers.hosted_ai_allowed
    ? "Hosted AI explicitly allowed; Jev is still optional per run. Provider charges may apply."
    : "ZERO PAID AI: local learning + keyless public DEX data. Jev calls are blocked. Network and future trading fees still apply.";
  const scan = snapshot.dex_scan?.data;
  $("dex-scan").innerHTML = scan
    ? `<div><strong>${scan.allow ? "Research checks passed" : "Entries blocked"}</strong><p>${escape(scan.reasons.join(" · ") || "No monitored anomaly detected; not a safety guarantee.")}</p><p>Raw spread: ${pct(scan.raw_spread)} · Checked ${date(scan.checked_at)} UTC</p>${scan.pools.map((p) => `<p>${escape(p.dex)}: ${usd(p.price)} · liquidity ${usd(p.liquidity_usd)}</p>`).join("")}<p>${escape(scan.warning)}</p></div>`
    : "No DEX scan yet. Start a scan or live DEX paper session.";
  $("mainnet-review").textContent = snapshot.readiness?.eligible_for_review
    ? "Research gates passed: operator review requested. Mainnet is still OFF. Complete independent validation, then explicitly authorize in the local CLI; never paste a private key here."
    : "Mainnet stays OFF until research gates and operator review pass. This browser never accepts wallet secrets.";
  const hasEvaluation = snapshot.evaluations?.some(
    (e) => e.source === s?.source,
  );
  const stageData = [
    [
      "01",
      "Historical data",
      `${num(snapshot.datasets.reduce((a, b) => a + b.count, 0))} stored candles`,
      snapshot.datasets.length ? 1 : 0,
    ],
    [
      "02",
      "Learn + evaluate",
      s
        ? `${num(s.model.n)} labels · ${job.eta == null ? "ETA: not estimated" : Math.ceil(job.eta) + " seconds remaining"}`
        : "No trained state",
      job.stage === "training" || job.stage === "replay"
        ? job.progress
        : s?.model.n
          ? hasEvaluation
            ? 1
            : 0.5
          : 0,
    ],
    [
      "03",
      "Live paper",
      s?.mode === "paper"
        ? `${num(s.observation_count)} live observations`
        : "Collect actual market observations",
      s?.mode === "paper" ? Math.min(1, s.observation_count / 10000) : 0,
    ],
    [
      "04",
      "Mainnet review",
      "Operator review required; never auto-promoted",
      snapshot.readiness?.eligible_for_review ? 1 : 0,
    ],
  ];
  $("stages").innerHTML = stageData
    .map(
      ([n, title, text, p]) =>
        `<article class="stage"><small>STAGE ${n}</small><h3>${title}</h3><p>${escape(text)}</p><progress value="${p ?? 0}" max="1" aria-label="${title} progress"></progress></article>`,
    )
    .join("");
  $("model-metrics").innerHTML =
    `<div><small>Accuracy</small><strong>${pct(m?.accuracy)}</strong></div><div><small>Brier loss ↓</small><strong>${m?.brier == null ? "—" : m.brier.toFixed(3)}</strong></div><div><small>Total signals</small><strong>${num(s?.bars)}</strong></div><div><small>Drift</small><strong>${s?.model.drift ? "Detected" : "—"}</strong></div>`;
  $("baseline-metrics").textContent =
    m?.no_opportunity_brier == null
      ? "Baseline comparison needs observed outcomes."
      : `Always predicting no opportunity: ${pct(m.no_opportunity_accuracy)} accuracy, ${m.no_opportunity_brier.toFixed(4)} Brier. Model Brier skill: ${pct(m.brier_skill_vs_no_opportunity)} (positive is better).`;
  const evaluation = snapshot.evaluations?.[0];
  $("evaluations").innerHTML = evaluation
    ? `<span><small>DATASET</small>${escape(evaluation.source)}${evaluation.synthetic ? " (SYNTHETIC)" : ""}</span>` +
      evaluation.folds
        .map(
          (f) =>
            `<span><small>FOLD ${f.fold + 1} · ${f.test_bars} TEST BARS</small>${pct(f.strategy_return)} net return<br>${f.closed} closed trades · Brier ${f.brier == null ? "—" : f.brier.toFixed(3)}</span>`,
        )
        .join("")
    : "No completed evaluation. Training alone does not establish performance.";
  $("gates").innerHTML = snapshot.readiness
    ? snapshot.readiness.checks
        .map(
          (g) =>
            `<div class="gate ${g.passed ? "pass" : ""}"><span>${g.passed ? "✓" : "○"}</span>${escape(g.name)}</div>`,
        )
        .join("")
    : '<p class="empty">Live paper evidence will appear here.</p>';
  const d = s?.last_decision,
    f = d?.features;
  sceneValues = {
    market: s?.latest_price ? usd(s.latest_price) : "No data",
    trend: f ? num(f.trend) : "—",
    reversion: f ? num(f.reversion) : "—",
    breakout: f ? num(f.breakout) : "—",
    model: pct(d?.probability),
    experts: pct(d?.expert_vote),
    risk: s?.halt_reason ? "HALTED" : s ? "LIMITS ON" : "WAITING",
    action: d?.side || "WAIT",
    memory: s ? `${num(s.model.n)} labels` : "Empty",
    jev: d?.risk_review?.choice || "Not evaluated",
  };
  scene.update(sceneValues);
  mini.update(sceneValues);
  $("network-decision").textContent = d?.side || "WAIT";
  $("signal-values").innerHTML = signals
    .map(
      ([id, label]) =>
        `<div class="signal-item"><span>${label}</span><strong>${escape(sceneValues[id])}</strong></div>`,
    )
    .join("");
  $("footer-status").textContent = s?.last_ts
    ? `Last bar ${date(s.last_ts)} UTC · Paper funds only`
    : "No wallet connected · No live orders";
  chart();
  renderFills();
  renderDecisions();
  renderLogs();
}
function chart() {
  const canvas = $("equity-chart"),
    rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  const ctx = canvas.getContext("2d"),
    w = rect.width,
    h = rect.height,
    dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  const points = snapshot?.state?.equity_curve || [];
  $("chart-empty").hidden = points.length > 1;
  const values = points.map((p) => p.equity);
  let min = Math.min(...values),
    max = Math.max(...values);
  if (!values.length) {
    min = 0;
    max = 1;
  }
  const pad = Math.max((max - min) * 0.15, max * 0.0003);
  min -= pad;
  max += pad;
  const x = (i) => 12 + (i / Math.max(1, points.length - 1)) * (w - 82),
    y = (v) => 12 + ((max - v) / (max - min)) * (h - 30);
  ctx.font = '9px "Segoe UI"';
  ctx.textAlign = "right";
  for (let i = 0; i < 5; i++) {
    const v = min + ((max - min) * i) / 4,
      Y = y(v);
    ctx.strokeStyle = "#2a3330";
    ctx.setLineDash([2, 5]);
    ctx.beginPath();
    ctx.moveTo(8, Y);
    ctx.lineTo(w - 72, Y);
    ctx.stroke();
    ctx.fillStyle = "#98a59a";
    ctx.fillText(usd(v), w - 2, Y + 3);
  }
  ctx.setLineDash([]);
  if (points.length > 1) {
    ctx.beginPath();
    points.forEach((p, i) =>
      i ? ctx.lineTo(x(i), y(p.equity)) : ctx.moveTo(x(i), y(p.equity)),
    );
    ctx.lineWidth = 1.8;
    ctx.strokeStyle = "#b9efa0";
    ctx.stroke();
    ctx.lineTo(x(points.length - 1), h - 15);
    ctx.lineTo(x(0), h - 15);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, "#b9efa025");
    gradient.addColorStop(1, "#b9efa000");
    ctx.fillStyle = gradient;
    ctx.fill();
    const last = points.at(-1);
    ctx.beginPath();
    ctx.arc(x(points.length - 1), y(last.equity), 3, 0, Math.PI * 2);
    ctx.fillStyle = "#c1f7a3";
    ctx.fill();
    $("chart-start").textContent = date(points[0].ts).slice(5, 16);
    $("chart-end").textContent = date(last.ts).slice(5, 16);
  }
  canvas.setAttribute(
    "aria-label",
    points.length
      ? `Equity changed from ${usd(points[0].equity)} to ${usd(points.at(-1).equity)} over the last ${points.length} bars`
      : "No equity observations yet",
  );
}
function renderFills() {
  const query = $("trade-search").value.toLowerCase(),
    side = $("trade-side").value,
    day = $("trade-date").value;
  const rows = fills.filter(
    (e) =>
      (!side || e.data.side === side) &&
      (!day || date(e.ts).startsWith(day)) &&
      JSON.stringify(e.data).toLowerCase().includes(query),
  );
  $("trade-count").textContent = `/ ${fills.length} LOADED`;
  $("trades").innerHTML = rows.length
    ? rows
        .map(
          ({ ts, data: d }) =>
            `<tr><td>${date(ts)}</td><td>SOL / USDC</td><td><span class="side ${d.side === "SELL" ? "sell" : ""}">${escape(d.side)}</span></td><td>${usd(d.price)}</td><td>${num(d.quantity)}</td><td>${usd(d.fee)}</td><td class="${d.pnl < 0 ? "negative" : "positive"}">${usd(d.pnl)}</td><td title="${escape(d.reason)}">${escape(d.reason)}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="8" class="empty">No matching fills. Trades appear only when the model and risk policy agree.</td></tr>';
}
function renderDecisions() {
  const opened = new Set(
    [...$("decision-list").querySelectorAll("details[open]")].map(
      (e) => e.querySelector("summary").textContent,
    ),
  );
  $("decision-list").innerHTML = decisions.length
    ? decisions
        .map(
          ({ ts, data: d }) =>
            `<details class="decision"><summary><span class="side ${d.side === "SELL" ? "sell" : ""}">${escape(d.side)}</span><span>${date(ts)} UTC</span><span>P(net hurdle) ${pct(d.probability)}</span></summary><p>${escape(d.reasons?.join(" · "))}</p><pre>${escape(JSON.stringify(d, null, 2))}</pre></details>`,
        )
        .join("")
    : '<p class="empty">No decisions yet. Start a run to inspect the model.</p>';
  $("decision-list")
    .querySelectorAll("details")
    .forEach((e) => {
      e.open = opened.has(e.querySelector("summary").textContent);
    });
}
function renderLogs() {
  const opened = new Set(
    [...$("log-list").querySelectorAll("details[open]")].map(
      (e) => e.parentElement.textContent,
    ),
  );
  const q = $("log-search").value.toLowerCase(),
    kind = $("log-kind").value;
  const rows = logs.filter(
    (e) =>
      (!kind || e.kind === kind) && JSON.stringify(e).toLowerCase().includes(q),
  );
  $("log-list").innerHTML = rows.length
    ? rows
        .map(
          (e) =>
            `<div class="log-row"><time>${date(e.ts)}</time><span>${escape(e.kind.toUpperCase())}</span><details><summary>${escape(e.data.reason || e.data.error || e.data.reasons?.join(" · ") || JSON.stringify(e.data))}</summary><pre>${escape(JSON.stringify(e.data, null, 2))}</pre></details></div>`,
        )
        .join("")
    : '<p class="empty">No matching events. The journal is saved in SQLite and exportable as JSONL.</p>';
  $("log-list")
    .querySelectorAll("details")
    .forEach((e) => {
      e.open = opened.has(e.parentElement.textContent);
    });
}
async function loadEvents(type, older = false) {
  if (!snapshot?.session) return;
  const collection =
    type === "fill" ? fills : type === "decision" ? decisions : logs;
  const params = new URLSearchParams({
    session: snapshot.session,
    limit: "100",
  });
  if (type) params.set("kind", type);
  if (older && collection.length)
    params.set("before", String(Math.min(...collection.map((e) => e.id))));
  const rows = await api("/api/events?" + params);
  const merged = [
    ...new Map([...collection, ...rows].map((e) => [e.id, e])).values(),
  ]
    .sort((a, b) => b.id - a.id)
    .slice(0, 3000);
  if (type === "fill") fills = merged;
  else if (type === "decision") decisions = merged;
  else logs = merged;
  if (older && !rows.length) toast("No older events.");
}
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const previous = snapshot?.session;
    snapshot = await api(
      "/api/status" +
        (selected ? "?session=" + encodeURIComponent(selected) : ""),
    );
    csrf = snapshot.csrf;
    if (previous !== snapshot.session) {
      fills = [];
      decisions = [];
      logs = [];
    }
    await Promise.all([
      loadEvents("fill"),
      loadEvents("decision"),
      loadEvents(""),
    ]);
    render();
  } catch (error) {
    $("connection").textContent = "DISCONNECTED";
    $("notice").textContent = "Cannot reach the local engine. " + error.message;
    $("notice").classList.add("error");
  } finally {
    busy = false;
  }
}
$("session").addEventListener("change", () => {
  selected = $("session").value;
  fills = [];
  decisions = [];
  logs = [];
  refresh();
});
for (const id of ["trade-search", "trade-side", "trade-date"])
  $(id).addEventListener("input", renderFills);
for (const id of ["log-search", "log-kind"])
  $(id).addEventListener("input", renderLogs);
for (const [id, kind] of [
  ["more-trades", "fill"],
  ["more-decisions", "decision"],
  ["more-logs", ""],
])
  $(id).addEventListener("click", async () => {
    try {
      await loadEvents(kind, true);
      render();
    } catch (error) {
      toast(error.message);
    }
  });
document.querySelectorAll("[data-run]").forEach((button) =>
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await api("/api/run", {
        operation: button.dataset.run,
        source: $("dataset").value,
        days: 7,
        jev: $("use-jev").checked,
        laya: $("use-laya").checked,
        session: $("paper-session").value,
        checkpoint:
          snapshot?.state?.mode === "replay" &&
          snapshot?.state?.source?.startsWith("dex:")
            ? snapshot.session
            : undefined,
      });
      selected = "";
      toast("Worker started. Progress and events update automatically.");
      await refresh();
    } catch (error) {
      toast(error.message);
      button.disabled = false;
    }
  }),
);
async function control(action) {
  try {
    await api("/api/control", { action });
    toast(
      action === "kill"
        ? "Kill switch latched; paper exit waits for the next valid bar."
        : "Control applied: " + action,
    );
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}
$("pause").addEventListener("click", () =>
  control(snapshot?.state?.paused ? "resume" : "pause"),
);
$("stop-worker").addEventListener("click", () => control("stop"));
$("kill").addEventListener("click", () => $("kill-dialog").showModal());
$("cancel-kill").addEventListener("click", () => $("kill-dialog").close());
$("confirm-kill").addEventListener("click", () => {
  $("kill-dialog").close();
  control("kill");
});
$("export").addEventListener("click", () => {
  if (!snapshot?.session) return toast("Choose a session first.");
  const a = document.createElement("a");
  a.href = "/api/export?session=" + encodeURIComponent(snapshot.session);
  a.download = "quantum-solana-trader-events.jsonl";
  a.click();
});
$("scene-reset").addEventListener("click", () => scene.reset());
$("scene-motion").addEventListener("click", () => {
  scene.motion = !scene.motion;
  $("scene-motion").textContent = scene.motion
    ? "Pause motion"
    : "Resume motion";
});
new ResizeObserver(chart).observe($("equity-chart").parentElement);
refresh();
setInterval(refresh, 5000);
