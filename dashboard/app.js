const state = {
  artifacts: [],
  currentArtifactPath: null,
  packageData: null,
};

function formatCurrency(value) {
  const number = Number(value || 0);
  const sign = number > 0 ? "+" : number < 0 ? "-" : "";
  return `${sign}$${Math.abs(number).toFixed(2)}`;
}

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatRate(value) {
  return Number(value || 0).toFixed(3);
}

function cssSignClass(value) {
  return Number(value || 0) >= 0 ? "gain" : "loss";
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

async function loadArtifactList() {
  const payload = await fetchJson("/api/artifacts");
  state.artifacts = payload.artifacts || [];
  const select = document.getElementById("artifactSelect");
  select.innerHTML = "";
  for (const artifact of state.artifacts) {
    const option = document.createElement("option");
    option.value = artifact.path;
    option.textContent = artifact.name;
    select.appendChild(option);
  }
  state.currentArtifactPath = payload.default_artifact_path;
  if (state.currentArtifactPath) {
    select.value = state.currentArtifactPath;
  }
}

async function loadArtifact(path) {
  state.currentArtifactPath = path;
  const encoded = encodeURIComponent(path);
  state.packageData = await fetchJson(`/api/artifact?path=${encoded}`);
  renderDashboard();
}

function renderMetricCard(label, value, extraClass = "") {
  return `
    <div class="metric-card">
      <p class="metric-label">${label}</p>
      <p class="metric-value ${extraClass}">${value}</p>
    </div>
  `;
}

function buildLineChart(points, valueKey, colorClass) {
  if (!points.length) {
    return `<div class="muted">No data available.</div>`;
  }
  const width = 760;
  const height = 260;
  const padding = 26;
  const values = points.map((point) => Number(point[valueKey] || 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = (width - padding * 2) / Math.max(points.length - 1, 1);
  const path = points
    .map((point, index) => {
      const x = padding + stepX * index;
      const y = height - padding - ((Number(point[valueKey] || 0) - min) / span) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  const midY = height - padding - ((0 - min) / span) * (height - padding * 2);
  const zeroLine = min <= 0 && max >= 0 ? `<line class="grid-line" x1="${padding}" y1="${midY}" x2="${width - padding}" y2="${midY}"></line>` : "";
  return `
    <svg viewBox="0 0 ${width} ${height}" class="svg-chart" role="img" aria-label="${valueKey} chart">
      <line class="axis" x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}"></line>
      <line class="axis" x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}"></line>
      ${zeroLine}
      <path class="${colorClass}" d="${path}"></path>
    </svg>
  `;
}

function buildBarChart(points, valueKey) {
  if (!points.length) {
    return `<div class="muted">No data available.</div>`;
  }
  const width = 760;
  const height = 260;
  const padding = 26;
  const values = points.map((point) => Number(point[valueKey] || 0));
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;
  const chartWidth = width - padding * 2;
  const barWidth = chartWidth / Math.max(points.length, 1);
  const zeroY = height - padding - ((0 - min) / span) * (height - padding * 2);
  const bars = points
    .map((point, index) => {
      const value = Number(point[valueKey] || 0);
      const scaledY = height - padding - ((value - min) / span) * (height - padding * 2);
      const x = padding + index * barWidth + 1;
      const y = Math.min(scaledY, zeroY);
      const barHeight = Math.max(Math.abs(zeroY - scaledY), 1);
      const className = value >= 0 ? "bar-gain" : "bar-loss";
      return `<rect class="${className}" x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(barWidth - 2, 1).toFixed(2)}" height="${barHeight.toFixed(2)}"></rect>`;
    })
    .join("");
  return `
    <svg viewBox="0 0 ${width} ${height}" class="svg-chart" role="img" aria-label="${valueKey} bar chart">
      <line class="axis" x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}"></line>
      <line class="axis" x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}"></line>
      <line class="grid-line" x1="${padding}" y1="${zeroY}" x2="${width - padding}" y2="${zeroY}"></line>
      ${bars}
    </svg>
  `;
}

function buildTable(headers, rows) {
  const thead = headers.map((header) => `<th>${header}</th>`).join("");
  const body = rows.length
    ? rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${headers.length}" class="muted">No rows</td></tr>`;
  return `<div class="table-wrap"><table><thead><tr>${thead}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function buildBarList(items, labelKey, valueKey) {
  if (!items.length) {
    return `<div class="muted">No rows</div>`;
  }
  const maxAbs = Math.max(...items.map((item) => Math.abs(Number(item[valueKey] || 0))), 1);
  return `
    <div class="bar-list">
      ${items
        .map((item) => {
          const value = Number(item[valueKey] || 0);
          const width = (Math.abs(value) / maxAbs) * 100;
          return `
            <div class="bar-row">
              <div class="bar-meta">
                <span>${item[labelKey]}</span>
                <span class="${cssSignClass(value)}">${formatCurrency(value)}</span>
              </div>
              <div class="bar-track">
                <div class="bar-fill ${value < 0 ? "loss" : ""}" style="width:${Math.max(width, 2)}%"></div>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function sortByPnl(items) {
  return [...items].sort((left, right) => Number(right.realized_pnl || 0) - Number(left.realized_pnl || 0));
}

function renderDashboard() {
  const root = document.getElementById("dashboardRoot");
  const artifactPathLabel = document.getElementById("artifactPath");
  const data = state.packageData;
  if (!data) {
    root.innerHTML = `<section class="status-card"><p>No artifact loaded.</p></section>`;
    artifactPathLabel.textContent = "";
    return;
  }

  artifactPathLabel.textContent = data.artifact.path;
  const summary = data.summary;
  const diagnostics = data.diagnostics;
  const backtest = data.details.backtest;
  const byCity = sortByPnl(diagnostics.by_city || []);
  const daily = backtest.daily || [];
  const ledger = backtest.trades || [];
  const selectedCandidates = diagnostics.selected_candidates || [];
  const approvedNotSelected = diagnostics.approved_not_selected || [];
  const comparison = data.comparison;

  root.innerHTML = `
    <section class="card">
      <div class="chip-row">
        <span class="chip">Range ${summary.start_date} to ${summary.end_date}</span>
        <span class="chip">Entry ${summary.entry_time_utc} UTC</span>
        <span class="chip">Profile ${data.run.profile}</span>
        <span class="chip">Sides ${data.run.no_only ? "NO-only" : "YES+NO"}</span>
        <span class="chip">Artifact ${data.artifact.name}</span>
      </div>
    </section>

    <section class="metrics-grid">
      ${renderMetricCard("Ending Balance", formatCurrency(summary.ending_balance), cssSignClass(summary.total_pnl))}
      ${renderMetricCard("Total P&L", formatCurrency(summary.total_pnl), cssSignClass(summary.total_pnl))}
      ${renderMetricCard("Return", formatPercent(summary.return_pct), cssSignClass(summary.return_pct))}
      ${renderMetricCard("Max Drawdown", formatPercent(summary.max_drawdown), "loss")}
      ${renderMetricCard("Win Rate", formatPercent(summary.win_rate))}
      ${renderMetricCard("Brier Score", formatRate(summary.brier_score))}
      ${renderMetricCard("Trades", formatNumber(backtest.trades.length))}
      ${renderMetricCard("Fees", formatCurrency(summary.total_fees), "loss")}
    </section>

    ${
      comparison
        ? `<section class="metrics-grid">
            ${renderMetricCard("Baseline P&L Delta", formatCurrency(comparison.total_pnl_delta), cssSignClass(comparison.total_pnl_delta))}
            ${renderMetricCard("Baseline Return Delta", formatPercent(comparison.return_pct_delta), cssSignClass(comparison.return_pct_delta))}
            ${renderMetricCard("Baseline Drawdown Delta", formatPercent(comparison.max_drawdown_delta), cssSignClass(-comparison.max_drawdown_delta))}
          </section>`
        : ""
    }

    <section class="two-col">
      <article class="card chart-shell">
        <h2>Daily Equity</h2>
        ${buildLineChart(daily, "equity", "line")}
        <p class="table-caption">Best equity ${formatCurrency(diagnostics.daily_equity.best_equity.equity)} on ${diagnostics.daily_equity.best_equity.cycle_day}. Worst equity ${formatCurrency(diagnostics.daily_equity.worst_equity.equity)} on ${diagnostics.daily_equity.worst_equity.cycle_day}.</p>
      </article>
      <article class="card chart-shell">
        <h2>Realized Daily P&L</h2>
        ${buildBarChart(daily, "realized_pnl")}
        <p class="table-caption">${diagnostics.daily_equity.positive_realized_days} positive realized days, ${diagnostics.daily_equity.negative_realized_days} negative realized days.</p>
      </article>
    </section>

    <section class="two-col">
      <article class="card">
        <h2>By City</h2>
        ${buildBarList(byCity, "name", "realized_pnl")}
      </article>
      <article class="card">
        <h2>By Side</h2>
        ${buildTable(
          ["Side", "Trades", "Wins", "Win Rate", "P&L"],
          (diagnostics.by_side || []).map((item) => [
            item.name,
            formatNumber(item.trades),
            formatNumber(item.wins),
            formatPercent(item.win_rate),
            `<span class="${cssSignClass(item.realized_pnl)}">${formatCurrency(item.realized_pnl)}</span>`,
          ]),
        )}
      </article>
    </section>

    <section class="three-col">
      <article class="card">
        <h2>Candidate Summary</h2>
        ${buildTable(
          ["Metric", "Value"],
          [
            ["Approved", formatNumber(diagnostics.candidate_summary.approved_candidates)],
            ["Selected", formatNumber(diagnostics.candidate_summary.selected_candidates)],
            ["Approved, not selected", formatNumber(diagnostics.candidate_summary.approved_not_selected)],
            ["Rejected", formatNumber(diagnostics.candidate_summary.rejected_candidates)],
          ],
        )}
      </article>
      <article class="card">
        <h2>Skip Reasons</h2>
        ${buildTable(
          ["Reason", "Count"],
          (diagnostics.skip_reasons || []).map((item) => [item.reason, formatNumber(item.count)]),
        )}
      </article>
      <article class="card">
        <h2>Concentration</h2>
        ${buildTable(
          ["City/Date", "Trades", "P&L"],
          (diagnostics.city_date_concentration || []).slice(0, 10).map((item) => [
            item.name,
            formatNumber(item.trades),
            `<span class="${cssSignClass(item.realized_pnl)}">${formatCurrency(item.realized_pnl)}</span>`,
          ]),
        )}
      </article>
    </section>

    <section class="two-col">
      <article class="card">
        <h2>EV Bin Performance</h2>
        ${buildTable(
          ["EV Bin", "Trades", "Win Rate", "P&L"],
          (diagnostics.ev_bins || []).map((item) => [
            item.name,
            formatNumber(item.trades),
            formatPercent(item.win_rate),
            `<span class="${cssSignClass(item.realized_pnl)}">${formatCurrency(item.realized_pnl)}</span>`,
          ]),
        )}
      </article>
      <article class="card">
        <h2>Price Bin Performance</h2>
        ${buildTable(
          ["Price Bin", "Trades", "Win Rate", "P&L"],
          (diagnostics.price_bins || []).map((item) => [
            item.name,
            formatNumber(item.trades),
            formatPercent(item.win_rate),
            `<span class="${cssSignClass(item.realized_pnl)}">${formatCurrency(item.realized_pnl)}</span>`,
          ]),
        )}
      </article>
    </section>

    <section class="two-col">
      <article class="card">
        <h2>Top Winners</h2>
        ${buildTable(
          ["Ticker", "Date", "Side", "Contracts", "EV", "P&L"],
          (diagnostics.top_winners || []).map((item) => [
            item.ticker,
            item.target_date,
            item.side,
            formatNumber(item.contracts),
            formatRate(item.expected_value),
            `<span class="gain">${formatCurrency(item.realized_pnl)}</span>`,
          ]),
        )}
      </article>
      <article class="card">
        <h2>Top Losers</h2>
        ${buildTable(
          ["Ticker", "Date", "Side", "Contracts", "EV", "P&L"],
          (diagnostics.top_losers || []).map((item) => [
            item.ticker,
            item.target_date,
            item.side,
            formatNumber(item.contracts),
            formatRate(item.expected_value),
            `<span class="loss">${formatCurrency(item.realized_pnl)}</span>`,
          ]),
        )}
      </article>
    </section>

    <section class="two-col">
      <article class="card">
        <h2>Selected Candidates</h2>
        ${buildTable(
          ["Ticker", "Side", "Rank", "EV", "Price"],
          selectedCandidates.slice(0, 20).map((item) => [
            item.ticker,
            item.side,
            formatRate(item.ranking_score),
            formatRate(item.expected_value),
            Number(item.price || 0).toFixed(2),
          ]),
        )}
      </article>
      <article class="card">
        <h2>Approved, Not Selected</h2>
        ${buildTable(
          ["Ticker", "Side", "Rank", "EV", "Price"],
          approvedNotSelected.slice(0, 20).map((item) => [
            item.ticker,
            item.side,
            formatRate(item.ranking_score),
            formatRate(item.expected_value),
            Number(item.price || 0).toFixed(2),
          ]),
        )}
      </article>
    </section>

    <section class="card">
      <h2>Trade Ledger</h2>
      <div class="filters">
        <input id="ledgerSearch" type="search" placeholder="Search ticker or city" />
        <select id="sideFilter">
          <option value="">All sides</option>
          <option value="YES">YES</option>
          <option value="NO">NO</option>
        </select>
        <select id="cityFilter">
          <option value="">All cities</option>
          ${[...new Set(ledger.map((trade) => trade.city))].sort().map((city) => `<option value="${city}">${city}</option>`).join("")}
        </select>
      </div>
      <div id="ledgerTable"></div>
      <p class="table-caption">Showing the full trade ledger from the saved backtest artifact only.</p>
    </section>
  `;

  attachLedgerTable(ledger);
}

function attachLedgerTable(ledger) {
  const search = document.getElementById("ledgerSearch");
  const sideFilter = document.getElementById("sideFilter");
  const cityFilter = document.getElementById("cityFilter");
  const tableRoot = document.getElementById("ledgerTable");

  function renderLedger() {
    const needle = search.value.trim().toLowerCase();
    const side = sideFilter.value;
    const city = cityFilter.value;
    const filtered = ledger.filter((trade) => {
      if (side && trade.side !== side) {
        return false;
      }
      if (city && trade.city !== city) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return `${trade.ticker} ${trade.city} ${trade.target_date}`.toLowerCase().includes(needle);
    });
    tableRoot.innerHTML = buildTable(
      ["Ticker", "Date", "City", "Side", "Contracts", "Entry", "Pred. YES", "EV", "Payout", "P&L"],
      filtered.slice(0, 250).map((trade) => [
        trade.ticker,
        trade.target_date,
        trade.city,
        trade.side,
        formatNumber(trade.contracts),
        Number(trade.entry_price || 0).toFixed(2),
        formatRate(trade.predicted_probability_yes),
        formatRate(trade.expected_value),
        Number(trade.payout || 0).toFixed(2),
        `<span class="${cssSignClass(trade.realized_pnl)}">${formatCurrency(trade.realized_pnl)}</span>`,
      ]),
    );
  }

  search.addEventListener("input", renderLedger);
  sideFilter.addEventListener("change", renderLedger);
  cityFilter.addEventListener("change", renderLedger);
  renderLedger();
}

async function boot() {
  try {
    await loadArtifactList();
    const selector = document.getElementById("artifactSelect");
    selector.addEventListener("change", async (event) => {
      await loadArtifact(event.target.value);
    });
    if (state.currentArtifactPath) {
      await loadArtifact(state.currentArtifactPath);
    }
  } catch (error) {
    document.getElementById("dashboardRoot").innerHTML = `
      <section class="status-card">
        <h2>Dashboard failed to load</h2>
        <p class="muted">${error.message}</p>
      </section>
    `;
  }
}

boot();
