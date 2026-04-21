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
  } catch (e) {
    $("#risk-output").textContent = `error: ${e.message}`;
  }
});

runScanner().catch(() => {});
refreshRisk();
refreshRegime();
setInterval(refreshRisk, 15000);
setInterval(refreshRegime, 30000);
