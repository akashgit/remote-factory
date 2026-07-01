# Full Eval Dashboard

<div id="full-eval-dashboard">
  <p>Loading full eval results...</p>
</div>

<style>
#full-eval-dashboard table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
#full-eval-dashboard th {
  text-align: left;
  padding: 0.6rem 0.8rem;
  border-bottom: 2px solid var(--md-default-fg-color--lightest);
  white-space: nowrap;
}
#full-eval-dashboard td {
  padding: 0.5rem 0.8rem;
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
  vertical-align: top;
}
#full-eval-dashboard tr:hover td {
  background: var(--md-default-fg-color--lightest);
}
#full-eval-dashboard .status-success { color: #22863a; }
#full-eval-dashboard .status-failure { color: #cb2431; }
#full-eval-dashboard .error-msg {
  color: var(--md-default-fg-color--light);
  font-style: italic;
}
#full-eval-dashboard .section-title {
  margin-top: 2.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.2rem;
  font-weight: 600;
}
#full-eval-dashboard .section-explanation {
  font-size: 0.85rem;
  opacity: 0.6;
  margin: 0.3rem 0 1.5rem 0;
  font-style: italic;
}

/* Hero section */
#full-eval-dashboard .hero-section { text-align: center; margin: 1rem 0 2.5rem 0; }
#full-eval-dashboard .hero-cards { display: flex; gap: 1.5rem; justify-content: center; flex-wrap: wrap; margin: 1.5rem 0; }
#full-eval-dashboard .hero-card { flex: 0 1 220px; text-align: center; padding: 1.5rem 1rem; border-radius: 12px; background: var(--md-default-bg-color--light, #f5f5f5); border: 2px solid; }
#full-eval-dashboard .hero-card.factory { border-color: #4285f4; }
#full-eval-dashboard .hero-card.claude-code { border-color: #ff7043; }
#full-eval-dashboard .hero-score { font-size: 2.4rem; font-weight: 800; line-height: 1.1; }
#full-eval-dashboard .hero-label { font-size: 0.85rem; color: var(--md-default-fg-color--light); margin-bottom: 0.3rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
#full-eval-dashboard .hero-sublabel { font-size: 0.75rem; color: var(--md-default-fg-color--light); opacity: 0.7; }
#full-eval-dashboard .hero-trend { font-size: 0.85rem; margin-top: 0.4rem; }
#full-eval-dashboard .hero-explanation { font-size: 0.85rem; opacity: 0.6; max-width: 700px; margin: 0 auto; }

/* Filter bar */
#full-eval-dashboard .filter-bar {
  display: flex;
  gap: 1rem;
  margin: 0.5rem 0 1rem 0;
  align-items: center;
  flex-wrap: wrap;
}
#full-eval-dashboard .filter-bar select {
  padding: 0.3rem 0.6rem;
  border-radius: 4px;
  border: 1px solid var(--md-default-fg-color--lightest);
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  font-size: 0.85rem;
}
#full-eval-dashboard .filter-bar label {
  font-size: 0.85rem;
  font-weight: 500;
}

/* Expandable rows */
#full-eval-dashboard .run-detail { background: rgba(255,255,255,0.02); }
#full-eval-dashboard .run-detail td:first-child { padding-left: 2rem; font-size: 0.85rem; opacity: 0.85; }
#full-eval-dashboard .run-detail.hidden { display: none; }
#full-eval-dashboard .toggle-details {
  background: none;
  border: 1px solid var(--md-default-fg-color--lightest);
  border-radius: 4px;
  cursor: pointer;
  padding: 0.15rem 0.5rem;
  font-size: 0.8rem;
  color: var(--md-default-fg-color);
}
#full-eval-dashboard .toggle-details:hover {
  background: var(--md-default-fg-color--lightest);
}

/* Task detail rows */
#full-eval-dashboard .task-row { font-size: 0.82rem; }
#full-eval-dashboard .task-row td { padding: 0.3rem 0.8rem; opacity: 0.9; }
#full-eval-dashboard .task-row.hidden { display: none; }

/* Trend indicators */
#full-eval-dashboard .trend-up { color: #22863a; }
#full-eval-dashboard .trend-down { color: #cb2431; }
#full-eval-dashboard .trend-neutral { color: #888; }

/* Pagination controls */
#full-eval-dashboard .pagination { display: flex; justify-content: center; align-items: center; gap: 8px; margin: 16px 0; flex-wrap: wrap; }
#full-eval-dashboard .pagination button { background: #2a2a3e; color: #e0e0e0; border: 1px solid #444; border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 0.85rem; }
#full-eval-dashboard .pagination button:hover:not(:disabled) { background: #3a3a5e; }
#full-eval-dashboard .pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
#full-eval-dashboard .pagination button.active { background: #667eea; border-color: #667eea; }
#full-eval-dashboard .pagination .page-info { color: #aaa; font-size: 0.85em; }

/* Cost analytics */
#full-eval-dashboard .cost-grid { display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1rem 0; }
#full-eval-dashboard .cost-card { flex: 1; min-width: 200px; padding: 1.2rem; border-radius: 8px; background: var(--md-default-bg-color--light, #f5f5f5); border: 1px solid var(--md-default-fg-color--lightest); }
#full-eval-dashboard .cost-card .cost-value { font-size: 1.8rem; font-weight: 700; }
#full-eval-dashboard .cost-card .cost-label { font-size: 0.8rem; color: var(--md-default-fg-color--light); text-transform: uppercase; letter-spacing: 0.04em; }
</style>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<script>
const FE_REPO = 'akashgit/remote-factory';
const FE_JSONL_URL = `https://raw.githubusercontent.com/${FE_REPO}/benchmark-data/full-eval-results.jsonl`;

function feIsDarkMode() {
  return document.body.getAttribute('data-md-color-scheme') === 'slate';
}

function feChartColors() {
  const dark = feIsDarkMode();
  return {
    text: dark ? '#ccc' : '#333',
    grid: dark ? '#444' : '#e0e0e0',
  };
}

function feFormatDuration(seconds) {
  if (seconds == null) return '—';
  if (seconds >= 3600) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return h + 'h ' + m + 'm';
  }
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? m + 'm ' + s + 's' : s + 's';
}

function feFormatCost(usd) {
  if (usd == null) return '—';
  return '$' + usd.toFixed(2);
}

function feFormatDate(ts) {
  if (!ts) return '—';
  const d = feParseTimestamp(ts);
  if (!d) return ts;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function feParseTimestamp(ts) {
  if (!ts) return null;
  const iso = ts.replace(
    /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/,
    '$1-$2-$3T$4:$5:$6Z'
  );
  const d = new Date(iso);
  return isNaN(d) ? null : d;
}

function feStatusIcon(resolved) {
  return resolved
    ? '<span class="status-success">&#10003;</span>'
    : '<span class="status-failure">&#10007;</span>';
}

async function feFetchJsonl() {
  const resp = await fetch(FE_JSONL_URL);
  if (!resp.ok) return null;
  const text = await resp.text();
  const lines = text.trim().split('\n').filter(Boolean);
  return lines.map(l => {
    try { return JSON.parse(l); }
    catch { return null; }
  }).filter(Boolean);
}

// Section 1: Hero — Latest accuracy per benchmark x solver
function feRenderHero(results) {
  const latest = {};
  for (const r of results) {
    const key = r.benchmark + '|' + r.solver;
    if (!latest[key] || (r.timestamp > latest[key].timestamp)) {
      latest[key] = r;
    }
  }

  const priorByKey = {};
  for (const r of results) {
    const key = r.benchmark + '|' + r.solver;
    const cur = latest[key];
    if (r.timestamp < cur.timestamp) {
      if (!priorByKey[key] || r.timestamp > priorByKey[key].timestamp) {
        priorByKey[key] = r;
      }
    }
  }

  const entries = Object.entries(latest).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return '';

  let html = '<div class="hero-section">';
  html += '<div class="hero-cards">';

  for (const [key, r] of entries) {
    const acc = r.total > 0 ? (r.passed / r.total * 100) : null;
    const solverClass = r.solver === 'claude-code' ? 'claude-code' : 'factory';
    const prior = priorByKey[key];
    const prevAcc = prior && prior.total > 0 ? (prior.passed / prior.total * 100) : null;

    let trendHtml = '';
    if (acc != null && prevAcc != null) {
      const delta = acc - prevAcc;
      if (Math.abs(delta) < 0.5) {
        trendHtml = '<div class="hero-trend trend-neutral">= no change</div>';
      } else {
        const sign = delta > 0 ? '+' : '';
        const cls = delta > 0 ? 'trend-up' : 'trend-down';
        const arrow = delta > 0 ? '&#9650;' : '&#9660;';
        trendHtml = `<div class="hero-trend ${cls}">${arrow} ${sign}${delta.toFixed(1)}% vs prior</div>`;
      }
    } else if (acc != null) {
      trendHtml = '<div class="hero-trend trend-neutral">first run</div>';
    }

    html += `<div class="hero-card ${solverClass}">`;
    html += `<div class="hero-label">${r.benchmark}</div>`;
    html += `<div class="hero-sublabel">${r.solver}</div>`;
    html += `<div class="hero-score">${acc != null ? acc.toFixed(1) + '%' : '—'}</div>`;
    html += `<div class="hero-sublabel">${r.passed}/${r.total} tasks</div>`;
    html += trendHtml;
    html += '</div>';
  }

  html += '</div>';
  html += '<div class="hero-explanation">Latest full eval accuracy per benchmark and solver. Accuracy = percentage of all tasks in the dataset solved correctly.</div>';
  html += '</div>';
  return html;
}

// Section 2: Accuracy Trend Chart
function feRenderAccuracyTrend(results) {
  const benchmarks = [...new Set(results.map(r => r.benchmark))].sort();
  const solvers = [...new Set(results.map(r => r.solver))].sort();

  const datasets = [];
  const colorMap = { factory: '#4285f4', 'claude-code': '#ff7043' };
  const dashMap = { factory: [], 'claude-code': [5, 5] };
  const benchColors = ['#4285f4', '#ff7043', '#66bb6a', '#ab47bc', '#ffa726', '#26c6da'];

  let colorIdx = 0;
  for (const bench of benchmarks) {
    for (const solver of solvers) {
      const subset = results
        .filter(r => r.benchmark === bench && r.solver === solver)
        .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));

      if (subset.length === 0) continue;

      const points = subset
        .map(r => {
          const d = feParseTimestamp(r.timestamp);
          if (!d || r.total === 0) return null;
          return { x: d, y: (r.passed / r.total) * 100 };
        })
        .filter(Boolean);

      if (points.length === 0) continue;

      const color = benchmarks.length === 1
        ? (colorMap[solver] || benchColors[colorIdx % benchColors.length])
        : benchColors[colorIdx % benchColors.length];

      datasets.push({
        label: bench + ' / ' + solver,
        data: points,
        borderColor: color,
        backgroundColor: color,
        tension: 0.2,
        pointRadius: 4,
        borderDash: solvers.length > 1 && solver === 'claude-code' ? [5, 5] : [],
      });
      colorIdx++;
    }
  }

  if (datasets.length === 0) return '';

  let html = '<div class="section-title">Accuracy Over Time</div>';
  html += '<p class="section-explanation">Solve rate across the full dataset for each benchmark/solver combination.</p>';

  html += '<div class="filter-bar">';
  html += '<label>Benchmark: <select id="fe-filter-trend-bench"><option value="">All</option>';
  for (const b of benchmarks) html += `<option value="${b}">${b}</option>`;
  html += '</select></label>';
  html += '<label>Solver: <select id="fe-filter-trend-solver"><option value="">All</option>';
  for (const s of solvers) html += `<option value="${s}">${s}</option>`;
  html += '</select></label>';
  html += '</div>';

  html += '<div style="width:100%;margin:1rem 0 2rem 0"><canvas id="fe-accuracy-chart" style="width:100%!important;height:400px!important"></canvas></div>';

  setTimeout(() => {
    const ctx = document.getElementById('fe-accuracy-chart');
    if (!ctx) return;
    const colors = feChartColors();

    const chart = new Chart(ctx, {
      type: 'line',
      data: { datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: colors.text } },
          tooltip: {
            callbacks: {
              label: (item) => item.dataset.label + ': ' + item.raw.y.toFixed(1) + '%',
            },
          },
        },
        scales: {
          x: {
            type: 'time',
            time: { unit: 'day', tooltipFormat: 'MMM d, yyyy HH:mm' },
            title: { display: true, text: 'Date', color: colors.text },
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
          y: {
            min: 0,
            max: 100,
            title: { display: true, text: 'Accuracy (%)', color: colors.text },
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
        },
      },
    });

    const benchSelect = document.getElementById('fe-filter-trend-bench');
    const solverSelect = document.getElementById('fe-filter-trend-solver');

    function updateChart() {
      const bv = benchSelect?.value || '';
      const sv = solverSelect?.value || '';
      chart.data.datasets.forEach(ds => {
        const [dsBench, dsSolver] = ds.label.split(' / ');
        const matchB = !bv || dsBench === bv;
        const matchS = !sv || dsSolver === sv;
        ds.hidden = !(matchB && matchS);
      });
      chart.update();
    }

    if (benchSelect) benchSelect.addEventListener('change', updateChart);
    if (solverSelect) solverSelect.addEventListener('change', updateChart);
  }, 0);

  return html;
}

// Section 3: Per-task results table with expandable rows
let feCurrentPage = 1;
const FE_RUNS_PER_PAGE = 15;
let feAllRuns = [];
let feFilteredRuns = [];

function feGetFilteredRuns() {
  const bv = document.getElementById('fe-filter-bench')?.value || '';
  const sv = document.getElementById('fe-filter-solver')?.value || '';
  return feAllRuns.filter(r => {
    return (!bv || r.benchmark === bv) && (!sv || r.solver === sv);
  });
}

function feRenderPaginationControls(current, total, totalItems, startIdx, endIdx) {
  if (totalItems === 0) return '<span class="page-info">No matching runs</span>';
  if (total <= 1) return `<span class="page-info">Showing all ${totalItems} runs</span>`;

  let html = `<span class="page-info">Showing ${startIdx + 1}–${endIdx} of ${totalItems}</span>`;
  html += `<button class="fe-page-btn" data-page="${current - 1}" ${current === 1 ? 'disabled' : ''}>&#8592; Prev</button>`;

  const pages = [];
  pages.push(1);
  if (current > 3) pages.push('...');
  for (let p = Math.max(2, current - 1); p <= Math.min(total - 1, current + 1); p++) pages.push(p);
  if (current < total - 2) pages.push('...');
  if (total > 1) pages.push(total);

  for (const p of pages) {
    if (p === '...') {
      html += '<span class="page-info">&#8230;</span>';
    } else {
      html += `<button class="fe-page-btn${p === current ? ' active' : ''}" data-page="${p}">${p}</button>`;
    }
  }

  html += `<button class="fe-page-btn" data-page="${current + 1}" ${current === total ? 'disabled' : ''}>Next &#8594;</button>`;
  return html;
}

function feUpdateTable() {
  feFilteredRuns = feGetFilteredRuns();
  const totalPages = Math.max(1, Math.ceil(feFilteredRuns.length / FE_RUNS_PER_PAGE));
  if (feCurrentPage > totalPages) feCurrentPage = totalPages;

  const start = (feCurrentPage - 1) * FE_RUNS_PER_PAGE;
  const pageRuns = feFilteredRuns.slice(start, start + FE_RUNS_PER_PAGE);

  let rowsHtml = '';
  for (let i = 0; i < pageRuns.length; i++) {
    const r = pageRuns[i];
    const rid = 'fe-run-' + (start + i);
    const acc = r.total > 0 ? (r.passed / r.total * 100).toFixed(1) + '%' : '—';
    const cost = r.details?.cost_usd;
    const tasks = r.tasks || [];

    rowsHtml += `<tr class="run-summary">`;
    rowsHtml += `<td style="white-space:nowrap">${feFormatDate(r.timestamp)}</td>`;
    rowsHtml += `<td>${r.benchmark}</td>`;
    rowsHtml += `<td>${r.solver}</td>`;
    rowsHtml += `<td>${r.passed}/${r.total}</td>`;
    rowsHtml += `<td>${acc}</td>`;
    rowsHtml += `<td>${feFormatCost(cost)}</td>`;
    rowsHtml += `<td>${feFormatDuration(r.duration_seconds)}</td>`;
    if (tasks.length > 0) {
      rowsHtml += `<td><button class="toggle-details" data-target="${rid}">+ ${tasks.length} tasks</button></td>`;
    } else {
      rowsHtml += '<td></td>';
    }
    rowsHtml += '</tr>';

    for (const t of tasks) {
      rowsHtml += `<tr class="task-row hidden" data-parent="${rid}">`;
      rowsHtml += `<td></td>`;
      rowsHtml += `<td colspan="2" style="padding-left:2rem;font-family:monospace;font-size:0.78rem">${t.instance_id}</td>`;
      rowsHtml += `<td>${feStatusIcon(t.resolved)}</td>`;
      rowsHtml += `<td>${t.resolved ? '<span class="status-success">PASS</span>' : '<span class="status-failure">FAIL</span>'}</td>`;
      rowsHtml += `<td>${feFormatCost(t.cost_usd)}</td>`;
      rowsHtml += `<td>${feFormatDuration(t.duration_seconds)}</td>`;
      rowsHtml += '<td></td>';
      rowsHtml += '</tr>';
    }
  }

  const tbody = document.getElementById('fe-tbody');
  if (tbody) tbody.innerHTML = rowsHtml;

  const endIdx = start + pageRuns.length;
  const pHtml = feRenderPaginationControls(feCurrentPage, totalPages, feFilteredRuns.length, start, endIdx);
  const pTop = document.getElementById('fe-pagination-top');
  const pBottom = document.getElementById('fe-pagination-bottom');
  if (pTop) pTop.innerHTML = pHtml;
  if (pBottom) pBottom.innerHTML = pHtml;
}

function feRenderResultsTable(results) {
  feAllRuns = [...results].sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
  feCurrentPage = 1;

  const benchmarks = [...new Set(results.map(r => r.benchmark))].sort();
  const solvers = [...new Set(results.map(r => r.solver))].sort();

  let html = '<div class="section-title">Run History</div>';
  html += '<p class="section-explanation">All full eval runs, newest first. Click a row to see per-task breakdown.</p>';

  html += '<div class="filter-bar">';
  html += '<label>Benchmark: <select id="fe-filter-bench"><option value="">All</option>';
  for (const b of benchmarks) html += `<option value="${b}">${b}</option>`;
  html += '</select></label>';
  html += '<label>Solver: <select id="fe-filter-solver"><option value="">All</option>';
  for (const s of solvers) html += `<option value="${s}">${s}</option>`;
  html += '</select></label>';
  html += '</div>';

  html += '<div id="fe-pagination-top" class="pagination"></div>';
  html += '<table><thead><tr>';
  html += '<th>Date</th><th>Benchmark</th><th>Solver</th><th>Passed</th><th>Score</th><th>Cost</th><th>Duration</th><th></th>';
  html += '</tr></thead><tbody id="fe-tbody"></tbody></table>';
  html += '<div id="fe-pagination-bottom" class="pagination"></div>';

  setTimeout(() => {
    feUpdateTable();

    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.toggle-details');
      if (!btn || !btn.dataset.target?.startsWith('fe-run-')) return;
      const rid = btn.dataset.target;
      const details = document.querySelectorAll(`.task-row[data-parent="${rid}"]`);
      const isHidden = details[0]?.classList.contains('hidden');
      details.forEach(row => row.classList.toggle('hidden'));
      const count = details.length;
      btn.textContent = isHidden ? `− ${count} tasks` : `+ ${count} tasks`;
    });

    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.fe-page-btn');
      if (!btn || btn.disabled) return;
      const page = parseInt(btn.dataset.page);
      if (page && page !== feCurrentPage) {
        feCurrentPage = page;
        feUpdateTable();
      }
    });

    const benchSelect = document.getElementById('fe-filter-bench');
    const solverSelect = document.getElementById('fe-filter-solver');
    if (benchSelect) benchSelect.addEventListener('change', () => { feCurrentPage = 1; feUpdateTable(); });
    if (solverSelect) solverSelect.addEventListener('change', () => { feCurrentPage = 1; feUpdateTable(); });
  }, 0);

  return html;
}

// Section 4: Cost & Duration Analytics
function feRenderCostAnalytics(results) {
  if (results.length === 0) return '';

  const totalCost = results.reduce((s, r) => s + (r.details?.cost_usd || 0), 0);
  const totalTasks = results.reduce((s, r) => s + (r.total || 0), 0);
  const avgCostPerTask = totalTasks > 0 ? totalCost / totalTasks : 0;
  const totalDuration = results.reduce((s, r) => s + (r.duration_seconds || 0), 0);
  const avgDuration = results.length > 0 ? totalDuration / results.length : 0;
  const totalRuns = results.length;

  let html = '<div class="section-title">Cost &amp; Duration Analytics</div>';
  html += '<p class="section-explanation">Aggregate cost and duration statistics across all full eval runs. Vertex AI runs may show $0 when billing is at the platform level.</p>';

  html += '<div class="cost-grid">';
  html += '<div class="cost-card">';
  html += `<div class="cost-value">${feFormatCost(totalCost)}</div>`;
  html += `<div class="cost-label">Total Spend (${totalRuns} runs)</div>`;
  html += '</div>';
  html += '<div class="cost-card">';
  html += `<div class="cost-value">${feFormatCost(avgCostPerTask)}</div>`;
  html += `<div class="cost-label">Avg Cost / Task (${totalTasks} tasks)</div>`;
  html += '</div>';
  html += '<div class="cost-card">';
  html += `<div class="cost-value">${feFormatDuration(avgDuration)}</div>`;
  html += '<div class="cost-label">Avg Duration / Run</div>';
  html += '</div>';
  html += '</div>';

  html += '<div style="width:100%;margin:1.5rem 0"><canvas id="fe-cost-chart" style="width:100%!important;height:300px!important"></canvas></div>';

  setTimeout(() => {
    const ctx = document.getElementById('fe-cost-chart');
    if (!ctx) return;
    const colors = feChartColors();

    const sortedByDate = [...results].sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
    const costPoints = sortedByDate
      .map(r => {
        const d = feParseTimestamp(r.timestamp);
        return d ? { x: d, y: r.details?.cost_usd || 0, label: r.benchmark + ' / ' + r.solver } : null;
      })
      .filter(Boolean);

    const durationPoints = sortedByDate
      .map(r => {
        const d = feParseTimestamp(r.timestamp);
        return d ? { x: d, y: (r.duration_seconds || 0) / 3600, label: r.benchmark + ' / ' + r.solver } : null;
      })
      .filter(Boolean);

    new Chart(ctx, {
      type: 'bar',
      data: {
        datasets: [
          {
            label: 'Cost (USD)',
            data: costPoints,
            backgroundColor: '#4285f4aa',
            yAxisID: 'y',
          },
          {
            label: 'Duration (hours)',
            data: durationPoints,
            backgroundColor: '#ff704388',
            yAxisID: 'y1',
          },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: colors.text } },
          tooltip: {
            callbacks: {
              title: (items) => items[0]?.raw?.label || '',
              label: (item) => {
                if (item.datasetIndex === 0) return 'Cost: ' + feFormatCost(item.raw.y);
                return 'Duration: ' + (item.raw.y).toFixed(1) + 'h';
              },
            },
          },
        },
        scales: {
          x: {
            type: 'time',
            time: { unit: 'day', tooltipFormat: 'MMM d, yyyy' },
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
          y: {
            position: 'left',
            title: { display: true, text: 'Cost (USD)', color: colors.text },
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
          y1: {
            position: 'right',
            title: { display: true, text: 'Duration (hours)', color: colors.text },
            ticks: { color: colors.text },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }, 0);

  return html;
}

async function feRenderDashboard() {
  const container = document.getElementById('full-eval-dashboard');

  try {
    const results = await feFetchJsonl();

    if (results && results.length > 0) {
      let html = '';
      html += feRenderHero(results);
      html += feRenderAccuracyTrend(results);
      html += feRenderResultsTable(results);
      html += feRenderCostAnalytics(results);
      container.innerHTML = html;
      return;
    }

    container.innerHTML = '<p class="error-msg">No full eval results available yet.<br>'
      + 'Run a full benchmark evaluation to generate data:<br>'
      + '<code>benchmarks/run-full-eval.sh swebench --solver factory</code></p>';
  } catch (err) {
    container.innerHTML = `<p class="error-msg">${err.message}</p>`;
  }
}

feRenderDashboard();
</script>
