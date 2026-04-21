const $ = (s) => document.querySelector(s);

let selectedSymbol = null;

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function refreshRisk() {
  try {
    const r = await api("/risk/status");
    const el = $("#risk-summary");
    el.classList.toggle("halted", r.halted);
    const pnl = r.realized_pnl + r.unrealized_pnl;
    const pnlClass = pnl >= 0 ? "pos" : "neg";
    el.innerHTML = `
      equity $${r.account_equity.toLocaleString()} ·
      PnL <span class="${pnlClass}">${pnl.toFixed(2)}</span> ·
      open ${r.open_positions} ·
      loss left $${r.daily_loss_remaining.toFixed(0)}
      ${r.halted ? `· HALTED: ${r.halt_reasons.join("; ")}` : ""}
    `;
  } catch (e) {
    $("#risk-summary").textContent = `risk error: ${e.message}`;
  }
}

async function runScanner() {
  const gap = $("#min-gap").value;
  const rvol = $("#min-rvol").value;
  const rows = await api(`/scanner?min_gap_pct=${gap}&min_rvol=${rvol}&top_n=30`);
  const tbody = $("#scanner-table tbody");
  tbody.innerHTML = "";
  rows.forEach((h) => {
    const tr = document.createElement("tr");
    tr.dataset.symbol = h.symbol;
    const gapCls = h.gap_pct >= 0 ? "pos" : "neg";
    const rsCls = h.rs_flag ? `rs-${h.rs_flag}` : "";
    const liqCls = h.liquidity_tier ? `liq-${h.liquidity_tier}` : "";
    const rsPct = h.rs_pct == null ? "—" : `${h.rs_pct >= 0 ? "+" : ""}${h.rs_pct.toFixed(2)}%`;
    const pers = h.rs_persistence == null ? "—" : `${Math.round(h.rs_persistence * 100)}%`;
    const tier = h.liquidity_tier || "—";
    const spread = h.spread_pct == null ? "—" : `${h.spread_pct.toFixed(2)}%`;
    tr.innerHTML = `
      <td>${h.symbol}</td>
      <td>${h.price.toFixed(2)}</td>
      <td class="${gapCls}">${h.gap_pct.toFixed(2)}</td>
      <td>${h.relative_volume.toFixed(1)}x</td>
      <td class="${rsCls}">${rsPct}</td>
      <td class="${rsCls}">${pers}</td>
      <td class="${liqCls}">${tier}</td>
      <td>${spread}</td>
      <td>${h.atr_14d.toFixed(2)}</td>
      <td>${h.score.toFixed(1)}</td>
      <td>${h.side_bias || ""}</td>
      <td><button data-sym="${h.symbol}">Setups</button></td>
    `;
    tr.addEventListener("click", () => selectSymbol(h.symbol));
    tbody.appendChild(tr);
  });
}

async function refreshRegime() {
  try {
    const r = await api("/regime/current");
    const el = $("#regime-chip");
    el.className = `chip ${r.label}`;
    el.textContent = `regime: ${r.label.replace("_", " ")}`;
    el.title =
      `trend_score ${r.trend_score.toFixed(2)} · range_ratio ${r.range_ratio.toFixed(2)}`;
  } catch (e) {
    $("#regime-chip").textContent = `regime: err`;
  }
}

async function selectSymbol(sym) {
  selectedSymbol = sym;
  document.querySelectorAll("#scanner-table tbody tr").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.symbol === sym);
  });
  $("#selected-symbol").textContent = `(${sym})`;
  const body = $("#setups-body");
  body.textContent = "loading...";
  try {
    const signals = await api(`/setups/${sym}`);
    if (!signals.length) {
      body.textContent = "No setup triggered right now.";
      return;
    }
    body.innerHTML = signals
      .map(
        (s) => `
        <div class="setup-card">
          <h3>${s.setup.replaceAll("_", " ")} <span class="muted">(${s.side})</span></h3>
          <div>score: <b>${s.score.toFixed(0)}</b></div>
          <div class="grid">
            <span class="label">entry</span><span>${s.plan.entry.toFixed(2)}</span><span></span>
            <span class="label">stop</span><span>${s.plan.stop.toFixed(2)}</span><span></span>
            <span class="label">target1</span><span>${s.plan.target1.toFixed(2)}</span><span></span>
            <span class="label">risk/sh</span><span>${s.plan.risk_per_share.toFixed(2)}</span><span></span>
          </div>
          <div class="reasons">${s.reasons.join(" · ")}</div>
          <button data-action="use-plan"
            data-entry="${s.plan.entry}" data-stop="${s.plan.stop}" data-side="${s.side}">
            use in sizer →
          </button>
        </div>`
      )
      .join("");
    body.querySelectorAll('[data-action="use-plan"]').forEach((btn) => {
      btn.addEventListener("click", () => {
        $("#entry").value = btn.dataset.entry;
        $("#stop").value = btn.dataset.stop;
        $("#side").value = btn.dataset.side;
      });
    });
  } catch (e) {
    body.textContent = `error: ${e.message}`;
  }
}

$("#run-scanner").addEventListener("click", runScanner);
let lastSize = null;   // { shares } from /risk/size, used by Record trade

$("#risk-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    account_equity: parseFloat($("#equity").value),
    risk_per_trade_pct: parseFloat($("#risk-pct").value),
    entry: parseFloat($("#entry").value),
    stop: parseFloat($("#stop").value),
    side: $("#side").value,
  };
  try {
    const size = await api("/risk/size", { method: "POST", body: JSON.stringify(body) });
    const check = await api("/risk/check", {
      method: "POST",
      body: JSON.stringify({ proposed_dollar_risk: size.dollar_risk }),
    });
    $("#risk-output").textContent = JSON.stringify({ size, check }, null, 2);
    lastSize = size;
    $("#record-trade").disabled = !check.allowed || size.shares <= 0;
  } catch (e) {
    $("#risk-output").textContent = `error: ${e.message}`;
    lastSize = null;
    $("#record-trade").disabled = true;
  }
});

$("#record-trade").addEventListener("click", async () => {
  if (!lastSize || !selectedSymbol) {
    alert("Size a trade and select a symbol first.");
    return;
  }
  const t1 = parseFloat($("#target1").value);
  let regime = null;
  try {
    const r = await api("/regime/current");
    regime = r.label;
  } catch {}
  const body = {
    symbol: selectedSymbol,
    side: $("#side").value,
    shares: lastSize.shares,
    entry: parseFloat($("#entry").value),
    stop: parseFloat($("#stop").value),
    target1: Number.isFinite(t1) ? t1 : null,
    regime_at_open: regime,
  };
  try {
    await api("/trades/open", { method: "POST", body: JSON.stringify(body) });
    $("#record-trade").disabled = true;
    await Promise.all([refreshJournal(), refreshRisk()]);
  } catch (e) {
    alert(`open failed: ${e.message}`);
  }
});

async function refreshJournal() {
  try {
    const [trades, summary, bySetup] = await Promise.all([
      api("/journal/today"),
      api("/journal/summary"),
      api("/journal/by-setup"),
    ]);
    const tbody = $("#journal-table tbody");
    tbody.innerHTML = "";
    trades.forEach((t) => {
      const tr = document.createElement("tr");
      const pnl = t.pnl == null ? "—" : t.pnl.toFixed(2);
      const pnlCls = t.pnl == null ? "" : (t.pnl >= 0 ? "pos" : "neg");
      const r = t.realized_r == null ? "—" : t.realized_r.toFixed(2);
      const mistakes = t.mistakes.map(
        (m) => `<span class="mistake">${m.replaceAll("_", " ")}</span>`
      ).join("");
      const actions = t.closed_at
        ? ""
        : `<button class="close-trade" data-id="${t.id}" data-sym="${t.symbol}">close</button>`;
      tr.innerHTML = `
        <td>${t.id}</td>
        <td>${t.symbol}</td>
        <td>${t.side}</td>
        <td>${t.shares}</td>
        <td>${t.entry.toFixed(2)}</td>
        <td>${t.stop.toFixed(2)}</td>
        <td>${t.target1 == null ? "—" : t.target1.toFixed(2)}</td>
        <td>${t.exit_price == null ? "—" : t.exit_price.toFixed(2)}</td>
        <td class="${pnlCls}">${pnl}</td>
        <td class="${pnlCls}">${r}</td>
        <td>${t.setup || "—"}</td>
        <td>${t.regime_at_open || "—"}</td>
        <td>${mistakes}</td>
        <td>${actions}</td>
      `;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll(".close-trade").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const exit = prompt(`Exit price for ${btn.dataset.sym}?`);
        if (!exit) return;
        await api("/trades/close", {
          method: "POST",
          body: JSON.stringify({
            trade_id: parseInt(btn.dataset.id),
            exit_price: parseFloat(exit),
          }),
        });
        await Promise.all([refreshJournal(), refreshRisk()]);
      });
    });
    const s = summary;
    const pnlCls = s.total_pnl >= 0 ? "pos" : "neg";
    $("#journal-summary").innerHTML = `
      ${s.n_total} trades · ${s.n_closed} closed ·
      win ${(s.win_rate * 100).toFixed(0)}% ·
      PnL <span class="${pnlCls}">${s.total_pnl.toFixed(2)}</span> ·
      avg ${s.avg_r.toFixed(2)}R
    `;
    const bs = $("#by-setup-table tbody");
    bs.innerHTML = "";
    bySetup.forEach((row) => {
      const tr = document.createElement("tr");
      const exCls = row.expectancy_r >= 0 ? "pos" : "neg";
      tr.innerHTML = `
        <td>${row.setup.replaceAll("_", " ")}</td>
        <td>${row.n}</td>
        <td>${(row.win_rate * 100).toFixed(0)}%</td>
        <td>${row.avg_r.toFixed(2)}</td>
        <td class="${exCls}">${row.expectancy_r.toFixed(2)}</td>
        <td>${row.total_pnl.toFixed(2)}</td>
      `;
      bs.appendChild(tr);
    });
  } catch (e) {
    $("#journal-summary").textContent = `journal error: ${e.message}`;
  }
}

runScanner().catch(() => {});
refreshRisk();
refreshRegime();
refreshJournal();
setInterval(refreshRisk, 15000);
setInterval(refreshRegime, 30000);
setInterval(refreshJournal, 20000);
