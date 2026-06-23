# Benchmark Dashboard

<div id="benchmark-dashboard">
  <p>Loading benchmark results...</p>
</div>

<style>
#benchmark-dashboard table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
#benchmark-dashboard th {
  text-align: left;
  padding: 0.6rem 0.8rem;
  border-bottom: 2px solid var(--md-default-fg-color--lightest);
  white-space: nowrap;
}
#benchmark-dashboard td {
  padding: 0.5rem 0.8rem;
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
  vertical-align: top;
}
#benchmark-dashboard tr:hover td {
  background: var(--md-default-fg-color--lightest);
}
#benchmark-dashboard .status-success { color: #22863a; }
#benchmark-dashboard .status-failure { color: #cb2431; }
#benchmark-dashboard .error-msg {
  color: var(--md-default-fg-color--light);
  font-style: italic;
}
#benchmark-dashboard .section-title {
  margin-top: 2.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.2rem;
  font-weight: 600;
}
#benchmark-dashboard .section-explanation {
  font-size: 0.85rem;
  opacity: 0.6;
  margin: 0.3rem 0 1.5rem 0;
  font-style: italic;
}
#benchmark-dashboard .avg-summary {
  font-size: 1.05rem;
  margin: 0.8rem 0;
  line-height: 1.6;
}
#benchmark-dashboard .avg-summary strong {
  font-size: 1.1rem;
}
#benchmark-dashboard .footnote {
  font-size: 0.8rem;
  opacity: 0.6;
  font-style: italic;
  margin: 0.5rem 0 1.5rem 0;
}

/* Hero section */
#benchmark-dashboard .hero-section { text-align: center; margin: 1rem 0 2.5rem 0; }
#benchmark-dashboard .hero-cards { display: flex; gap: 2rem; justify-content: center; flex-wrap: wrap; margin: 1.5rem 0; }
#benchmark-dashboard .hero-card { flex: 1; text-align: center; padding: 2rem; border-radius: 12px; background: var(--md-default-bg-color--light, #f5f5f5); min-width: 240px; border: 2px solid; }
#benchmark-dashboard .hero-card.factory { border-color: #4285f4; }
#benchmark-dashboard .hero-card.claude-code { border-color: #ff7043; }
#benchmark-dashboard .hero-score { font-size: 3rem; font-weight: 800; line-height: 1.1; }
#benchmark-dashboard .hero-label { font-size: 1.1rem; color: var(--md-default-fg-color--light); margin-bottom: 0.5rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
#benchmark-dashboard .hero-trend { font-size: 0.9rem; margin-top: 0.5rem; }
#benchmark-dashboard .hero-explanation { font-size: 0.85rem; opacity: 0.6; max-width: 600px; margin: 0 auto; }

/* Filter bar */
#benchmark-dashboard .filter-bar {
  display: flex;
  gap: 1rem;
  margin: 0.5rem 0 1rem 0;
  align-items: center;
  flex-wrap: wrap;
}
#benchmark-dashboard .filter-bar select {
  padding: 0.3rem 0.6rem;
  border-radius: 4px;
  border: 1px solid var(--md-default-fg-color--lightest);
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  font-size: 0.85rem;
}
#benchmark-dashboard .filter-bar label {
  font-size: 0.85rem;
  font-weight: 500;
}

/* Run history expandable rows */
#benchmark-dashboard .run-detail { background: rgba(255,255,255,0.02); }
#benchmark-dashboard .run-detail td:first-child { padding-left: 2rem; font-size: 0.85rem; opacity: 0.85; }
#benchmark-dashboard .run-detail.hidden { display: none; }
#benchmark-dashboard .toggle-details {
  background: none;
  border: 1px solid var(--md-default-fg-color--lightest);
  border-radius: 4px;
  cursor: pointer;
  padding: 0.15rem 0.5rem;
  font-size: 0.8rem;
  color: var(--md-default-fg-color);
}
#benchmark-dashboard .toggle-details:hover {
  background: var(--md-default-fg-color--lightest);
}

/* Trend indicators */
#benchmark-dashboard .trend-up { color: #22863a; }
#benchmark-dashboard .trend-down { color: #cb2431; }
#benchmark-dashboard .trend-neutral { color: #888; }

/* Pagination controls */
#benchmark-dashboard .pagination { display: flex; justify-content: center; align-items: center; gap: 8px; margin: 16px 0; flex-wrap: wrap; }
#benchmark-dashboard .pagination button { background: #2a2a3e; color: #e0e0e0; border: 1px solid #444; border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 0.85rem; }
#benchmark-dashboard .pagination button:hover:not(:disabled) { background: #3a3a5e; }
#benchmark-dashboard .pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
#benchmark-dashboard .pagination button.active { background: #667eea; border-color: #667eea; }
#benchmark-dashboard .pagination .page-info { color: #aaa; font-size: 0.85em; }
</style>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<script>
const REPO = 'akashgit/remote-factory';
const JSONL_URL = `https://raw.githubusercontent.com/${REPO}/benchmark-data/results.jsonl`;

function isDarkMode() {
  return document.body.getAttribute('data-md-color-scheme') === 'slate';
}

function chartColors() {
  const dark = isDarkMode();
  return {
    text: dark ? '#ccc' : '#333',
    grid: dark ? '#444' : '#e0e0e0',
  };
}

function formatDurationShort(seconds) {
  if (seconds == null) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? m + 'm ' + s + 's' : s + 's';
}

function formatCost(usd) {
  if (usd == null) return '—';
  return '$' + usd.toFixed(2);
}

function formatDate(ts) {
  if (!ts) return '—';
  const d = parseTimestamp(ts);
  if (!d) return ts;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function parseTimestamp(ts) {
  if (!ts) return null;
  const iso = ts.replace(
    /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/,
    '$1-$2-$3T$4:$5:$6Z'
  );
  const d = new Date(iso);
  return isNaN(d) ? null : d;
}

function statusIcon(resolved) {
  return resolved
    ? '<span class="status-success">PASS</span>'
    : '<span class="status-failure">FAIL</span>';
}

function getSolver(r) {
  return r.solver || r.details?.solver || 'unknown';
}

function isMainBranch(r) {
  return r.ref === 'refs/heads/main';
}

function isPR(r) {
  return (r.ref && r.ref.startsWith('refs/pull/')) || !!r.pr_number;
}

function formatRefLabel(r) {
  if (!r.ref) return '—';
  if (r.ref === 'refs/heads/main') return 'main';
  if (r.ref.startsWith('refs/pull/'))
    return 'PR #' + r.ref.replace('refs/pull/', '').replace('/merge', '');
  return r.ref.replace('refs/heads/', '');
}

async function fetchJsonl() {
  const resp = await fetch(JSONL_URL);
  if (!resp.ok) return null;
  const text = await resp.text();
  const lines = text.trim().split('\n').filter(Boolean);
  return lines.map(l => {
    try { return JSON.parse(l); }
    catch { return null; }
  }).filter(Boolean);
}

function getLatestByCombo(results) {
  const byKey = {};
  for (const r of results) {
    const solver = getSolver(r);
    const key = r.benchmark + '|' + solver;
    if (!byKey[key] || (r.timestamp > byKey[key].timestamp)) {
      byKey[key] = r;
    }
  }
  return byKey;
}

function groupByRunId(results) {
  const runs = {};
  for (const entry of results) {
    const rid = entry.run_id || entry.timestamp || Math.random().toString();
    if (!runs[rid]) {
      runs[rid] = {
        run_id: rid,
        date: entry.timestamp,
        ref: entry.ref,
        commit: entry.commit,
        run_url: entry.run_url,
        trigger: entry.trigger,
        jobs: []
      };
    }
    runs[rid].jobs.push(entry);
  }

  for (const run of Object.values(runs)) {
    run.passed = run.jobs.filter(j => j.resolved).length;
    run.total = run.jobs.length;
    run.totalCost = run.jobs.reduce((s, j) => s + (j.details?.cost_usd || 0), 0);
    run.maxDuration = Math.max(...run.jobs.map(j => j.duration_seconds || 0));
    run.branch = formatRefLabel(run);
    run.passRate = run.total > 0 ? (run.passed / run.total * 100) : 0;
    run.score = run.total > 0 ? run.passRate.toFixed(0) + '%' : '—';
  }

  return Object.values(runs).sort((a, b) =>
    (b.date || '').localeCompare(a.date || '')
  );
}

function computeRunAccuracy(jobs) {
  const resolved = jobs.filter(j => j.resolved).length;
  return jobs.length > 0 ? (resolved / jobs.length) * 100 : null;
}

function computeAccuracyPoints(mainResults) {
  const runsByRunId = {};
  for (const e of mainResults) {
    const rid = e.run_id || e.timestamp;
    if (!runsByRunId[rid]) runsByRunId[rid] = [];
    runsByRunId[rid].push(e);
  }

  const factoryPoints = [];
  const claudePoints = [];

  for (const [, entries] of Object.entries(runsByRunId)) {
    const date = parseTimestamp(entries[0].timestamp);
    if (!date) continue;

    const factoryJobs = entries.filter(e => getSolver(e) === 'factory');
    const claudeJobs = entries.filter(e => getSolver(e) === 'claude-code');

    if (factoryJobs.length > 0) {
      factoryPoints.push({ x: date, y: computeRunAccuracy(factoryJobs) });
    }
    if (claudeJobs.length > 0) {
      claudePoints.push({ x: date, y: computeRunAccuracy(claudeJobs) });
    }
  }

  factoryPoints.sort((a, b) => a.x - b.x);
  claudePoints.sort((a, b) => a.x - b.x);
  return { factoryPoints, claudePoints };
}

function computeRunAverages(mainResults) {
  const runsByRunId = {};
  for (const e of mainResults) {
    const rid = e.run_id || e.timestamp || '';
    if (!runsByRunId[rid]) runsByRunId[rid] = [];
    runsByRunId[rid].push(e);
  }

  const factoryDurationPoints = [];
  const claudeDurationPoints = [];
  const factoryCostPoints = [];
  const claudeCostPoints = [];

  for (const [, entries] of Object.entries(runsByRunId)) {
    const date = parseTimestamp(entries[0].timestamp);
    if (!date) continue;

    const factoryJobs = entries.filter(e => getSolver(e) === 'factory');
    const claudeJobs = entries.filter(e => getSolver(e) === 'claude-code');

    if (factoryJobs.length > 0) {
      const durs = factoryJobs.filter(j => j.duration_seconds != null);
      const costs = factoryJobs.filter(j => j.details?.cost_usd != null);
      if (durs.length > 0)
        factoryDurationPoints.push({ x: date, y: durs.reduce((s, j) => s + j.duration_seconds, 0) / durs.length });
      if (costs.length > 0)
        factoryCostPoints.push({ x: date, y: costs.reduce((s, j) => s + j.details.cost_usd, 0) / costs.length });
    }
    if (claudeJobs.length > 0) {
      const durs = claudeJobs.filter(j => j.duration_seconds != null);
      const costs = claudeJobs.filter(j => j.details?.cost_usd != null);
      if (durs.length > 0)
        claudeDurationPoints.push({ x: date, y: durs.reduce((s, j) => s + j.duration_seconds, 0) / durs.length });
      if (costs.length > 0)
        claudeCostPoints.push({ x: date, y: costs.reduce((s, j) => s + j.details.cost_usd, 0) / costs.length });
    }
  }

  const sortByDate = (a, b) => a.x - b.x;
  factoryDurationPoints.sort(sortByDate);
  claudeDurationPoints.sort(sortByDate);
  factoryCostPoints.sort(sortByDate);
  claudeCostPoints.sort(sortByDate);

  return { factoryDurationPoints, claudeDurationPoints, factoryCostPoints, claudeCostPoints };
}

// Section 1: Hero — Headline Numbers
function renderHero(mainResults) {
  const runsByRunId = {};
  for (const e of mainResults) {
    const rid = e.run_id || e.timestamp;
    if (!runsByRunId[rid]) runsByRunId[rid] = [];
    runsByRunId[rid].push(e);
  }

  const runIds = Object.keys(runsByRunId).sort((a, b) => {
    const ta = runsByRunId[a][0].timestamp || '';
    const tb = runsByRunId[b][0].timestamp || '';
    return tb.localeCompare(ta);
  });

  if (runIds.length === 0) return '';

  const latestEntries = runsByRunId[runIds[0]];
  const priorEntries = runIds.length > 1 ? runsByRunId[runIds[1]] : [];

  function solverAccuracy(entries, solver) {
    const jobs = entries.filter(e => getSolver(e) === solver);
    return computeRunAccuracy(jobs);
  }

  const factoryAcc = solverAccuracy(latestEntries, 'factory');
  const claudeAcc = solverAccuracy(latestEntries, 'claude-code');
  const prevFactoryAcc = solverAccuracy(priorEntries, 'factory');
  const prevClaudeAcc = solverAccuracy(priorEntries, 'claude-code');

  function trendHtml(current, prev) {
    if (current == null) return '';
    if (prev == null) return '<div class="hero-trend trend-neutral">first run</div>';
    const delta = current - prev;
    if (Math.abs(delta) < 0.5) return '<div class="hero-trend trend-neutral">= no change</div>';
    const sign = delta > 0 ? '+' : '';
    const cls = delta > 0 ? 'trend-up' : 'trend-down';
    const arrow = delta > 0 ? '&#9650;' : '&#9660;';
    return `<div class="hero-trend ${cls}">${arrow} ${sign}${delta.toFixed(0)}% vs prior run</div>`;
  }

  let html = '<div class="hero-section">';
  html += '<div class="hero-cards">';

  html += '<div class="hero-card factory">';
  html += '<div class="hero-label">Factory</div>';
  html += `<div class="hero-score">${factoryAcc != null ? factoryAcc.toFixed(0) + '%' : '—'}</div>`;
  html += trendHtml(factoryAcc, prevFactoryAcc);
  html += '</div>';

  html += '<div class="hero-card claude-code">';
  html += '<div class="hero-label">Claude Code</div>';
  html += `<div class="hero-score">${claudeAcc != null ? claudeAcc.toFixed(0) + '%' : '—'}</div>`;
  html += trendHtml(claudeAcc, prevClaudeAcc);
  html += '</div>';

  html += '</div>';
  html += '<div class="hero-explanation">Latest main branch results, averaged across all benchmarks. Accuracy = percentage of benchmark tasks solved correctly (higher is better).</div>';
  html += '</div>';
  return html;
}

// Section 2: Accuracy Trend — Line Chart Over Time
function renderAccuracyTrend(mainResults) {
  const { factoryPoints, claudePoints } = computeAccuracyPoints(mainResults);
  if (factoryPoints.length === 0 && claudePoints.length === 0) return '';

  let html = '<div class="section-title">Accuracy Over Time</div>';
  html += '<p class="section-explanation">Average solve rate across all benchmarks for each CI run on the main branch.</p>';
  html += '<div style="width:100%;margin:2rem 0"><canvas id="accuracy-trend-chart" style="width:100%!important;height:400px!important"></canvas></div>';

  setTimeout(() => {
    const ctx = document.getElementById('accuracy-trend-chart');
    if (!ctx) return;
    const colors = chartColors();

    new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [
          {
            label: 'Factory',
            data: factoryPoints,
            borderColor: '#4285f4',
            backgroundColor: '#4285f4',
            tension: 0.2,
            pointRadius: 4,
          },
          {
            label: 'Claude Code',
            data: claudePoints,
            borderColor: '#ff7043',
            backgroundColor: '#ff7043',
            tension: 0.2,
            pointRadius: 4,
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
              label: (item) => item.dataset.label + ': ' + item.raw.y.toFixed(0) + '%',
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
  }, 0);

  return html;
}

// Section 3: Duration Trend — Line Chart Over Time
function renderDurationTrend(mainResults) {
  const runAvgs = computeRunAverages(mainResults);
  if (runAvgs.factoryDurationPoints.length === 0 && runAvgs.claudeDurationPoints.length === 0) return '';

  let html = '<div class="section-title">Average Duration Over Time</div>';
  html += '<p class="section-explanation">Average wall-clock time per benchmark task. Lower is better.</p>';
  html += '<div style="width:100%;margin:2rem 0"><canvas id="duration-trend-chart" style="width:100%!important;height:400px!important"></canvas></div>';

  setTimeout(() => {
    const ctx = document.getElementById('duration-trend-chart');
    if (!ctx) return;
    const colors = chartColors();

    new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [
          {
            label: 'Factory',
            data: runAvgs.factoryDurationPoints,
            borderColor: '#4285f4',
            backgroundColor: '#4285f4',
            tension: 0.2,
            pointRadius: 4,
          },
          {
            label: 'Claude Code',
            data: runAvgs.claudeDurationPoints,
            borderColor: '#ff7043',
            backgroundColor: '#ff7043',
            tension: 0.2,
            pointRadius: 4,
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
              label: (item) => item.dataset.label + ': ' + formatDurationShort(item.raw.y),
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
            title: { display: true, text: 'Duration (seconds)', color: colors.text },
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
        },
      },
    });
  }, 0);

  return html;
}

// Section 4: Cost Trend — Line Chart Over Time
function renderCostTrend(mainResults) {
  const runAvgs = computeRunAverages(mainResults);
  if (runAvgs.factoryCostPoints.length === 0 && runAvgs.claudeCostPoints.length === 0) return '';

  const totalFactory = runAvgs.factoryCostPoints.reduce((s, p) => s + p.y, 0);
  const totalClaude = runAvgs.claudeCostPoints.reduce((s, p) => s + p.y, 0);

  let html = '<div class="section-title">Average Cost Per Run Over Time</div>';
  html += '<div class="avg-summary">';
  html += `<strong>Total Spend:</strong> Factory ${formatCost(totalFactory)} | Claude Code ${formatCost(totalClaude)}`;
  html += '</div>';
  html += '<p class="section-explanation">Average API cost per benchmark task. Vertex AI runs may show $0 when billing is at the platform level.</p>';
  html += '<div style="width:100%;margin:2rem 0"><canvas id="cost-trend-chart" style="width:100%!important;height:400px!important"></canvas></div>';

  setTimeout(() => {
    const ctx = document.getElementById('cost-trend-chart');
    if (!ctx) return;
    const colors = chartColors();

    new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [
          {
            label: 'Factory',
            data: runAvgs.factoryCostPoints,
            borderColor: '#4285f4',
            backgroundColor: '#4285f4',
            tension: 0.2,
            pointRadius: 4,
          },
          {
            label: 'Claude Code',
            data: runAvgs.claudeCostPoints,
            borderColor: '#ff7043',
            backgroundColor: '#ff7043',
            tension: 0.2,
            pointRadius: 4,
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
              label: (item) => item.dataset.label + ': ' + formatCost(item.raw.y),
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
            title: { display: true, text: 'Cost (USD)', color: colors.text },
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
        },
      },
    });
  }, 0);

  return html;
}

// Section 5: Per-Benchmark Breakdown Table
function renderPerBenchmarkTable(mainResults) {
  const byKey = getLatestByCombo(mainResults);
  const combos = Object.entries(byKey).sort(([a], [b]) => a.localeCompare(b));

  if (combos.length === 0) {
    return '<div class="section-title">Latest Results by Benchmark</div>'
      + '<p class="error-msg">No main branch results yet.</p>';
  }

  let html = '<div class="section-title">Latest Results by Benchmark</div>';
  html += '<p class="section-explanation">Most recent main branch result for each benchmark and solver combination.</p>';
  html += '<table><thead><tr>';
  html += '<th>Benchmark</th><th>Solver</th><th>Result</th><th>Duration</th>';
  html += '<th>Cost</th><th>Commit</th><th>Run</th>';
  html += '</tr></thead><tbody>';

  for (const [, r] of combos) {
    const solver = getSolver(r);
    const commit = r.commit ? r.commit.substring(0, 7) : '—';
    const commitLink = r.commit
      ? `<a href="https://github.com/${REPO}/commit/${r.commit}"><code>${commit}</code></a>`
      : commit;
    const runLink = r.run_url
      ? `<a href="${r.run_url}">details</a>`
      : '—';

    html += '<tr>';
    html += `<td>${r.benchmark}</td>`;
    html += `<td>${solver}</td>`;
    html += `<td>${statusIcon(r.resolved)} ${(r.score * 100).toFixed(0)}%</td>`;
    html += `<td>${formatDurationShort(r.duration_seconds)}</td>`;
    html += `<td>${formatCost(r.details?.cost_usd)}</td>`;
    html += `<td>${commitLink}</td>`;
    html += `<td>${runLink}</td>`;
    html += '</tr>';
  }

  html += '</tbody></table>';
  return html;
}

function formatTrendColumn(delta) {
  if (!delta) return '<span class="trend-neutral">—</span>';
  let parts = [];

  const accVal = delta.accuracy;
  if (Math.abs(accVal) >= 0.5) {
    const sign = accVal > 0 ? '+' : '';
    const cls = accVal > 0 ? 'trend-up' : 'trend-down';
    const arrow = accVal > 0 ? '&#9650;' : '&#9660;';
    parts.push(`<span class="${cls}">${arrow}${sign}${accVal.toFixed(0)}% acc</span>`);
  }

  const costVal = delta.cost;
  if (Math.abs(costVal) >= 0.01) {
    const sign = costVal > 0 ? '+' : '';
    const cls = costVal > 0 ? 'trend-down' : 'trend-up';
    const arrow = costVal > 0 ? '&#9650;' : '&#9660;';
    parts.push(`<span class="${cls}">${arrow}${sign}$${costVal.toFixed(2)} cost</span>`);
  }

  const durVal = delta.duration;
  if (Math.abs(durVal) >= 1) {
    const sign = durVal > 0 ? '+' : '';
    const cls = durVal > 0 ? 'trend-down' : 'trend-up';
    const arrow = durVal > 0 ? '&#9650;' : '&#9660;';
    parts.push(`<span class="${cls}">${arrow}${sign}${formatDurationShort(Math.abs(durVal))} dur</span>`);
  }

  return parts.length > 0 ? parts.join('<br>') : '<span class="trend-neutral">= no change</span>';
}

// Section 6: Run History — paginated with expandable details
let currentPage = 1;
const RUNS_PER_PAGE = 20;
let allSortedRuns = [];

function getFilteredRuns() {
  const bv = document.getElementById('filter-benchmark')?.value || '';
  const sv = document.getElementById('filter-solver')?.value || '';
  return allSortedRuns.filter(run => {
    const runBenchmarks = [...new Set(run.jobs.map(j => j.benchmark))];
    const runSolvers = [...new Set(run.jobs.map(j => getSolver(j)))];
    return (!bv || runBenchmarks.includes(bv)) && (!sv || runSolvers.includes(sv));
  });
}

function renderPaginationControls(current, total, totalItems, startIdx, endIdx) {
  if (totalItems === 0) return '<span class="page-info">No matching runs</span>';
  if (total <= 1) return `<span class="page-info">Showing all ${totalItems} runs</span>`;

  let html = `<span class="page-info">Showing runs ${startIdx + 1}–${endIdx} of ${totalItems}</span>`;
  html += `<button class="page-btn" data-page="${current - 1}" ${current === 1 ? 'disabled' : ''}>← Prev</button>`;

  const pages = [];
  pages.push(1);
  if (current > 3) pages.push('...');
  for (let p = Math.max(2, current - 1); p <= Math.min(total - 1, current + 1); p++) {
    pages.push(p);
  }
  if (current < total - 2) pages.push('...');
  if (total > 1) pages.push(total);

  for (const p of pages) {
    if (p === '...') {
      html += '<span class="page-info">…</span>';
    } else {
      html += `<button class="page-btn${p === current ? ' active' : ''}" data-page="${p}">${p}</button>`;
    }
  }

  html += `<button class="page-btn" data-page="${current + 1}" ${current === total ? 'disabled' : ''}>Next →</button>`;
  return html;
}

function updateRunHistoryTable() {
  const filteredRuns = getFilteredRuns();
  const totalPages = Math.max(1, Math.ceil(filteredRuns.length / RUNS_PER_PAGE));
  if (currentPage > totalPages) currentPage = totalPages;

  const start = (currentPage - 1) * RUNS_PER_PAGE;
  const pageRuns = filteredRuns.slice(start, start + RUNS_PER_PAGE);

  const bv = document.getElementById('filter-benchmark')?.value || '';
  const sv = document.getElementById('filter-solver')?.value || '';

  let rowsHtml = '';
  for (const run of pageRuns) {
    const rid = run.run_id.replace(/[^a-zA-Z0-9_-]/g, '_');
    const jobBenchmarks = [...new Set(run.jobs.map(j => j.benchmark))].join(',');
    const jobSolvers = [...new Set(run.jobs.map(j => getSolver(j)))].join(',');

    rowsHtml += `<tr class="run-summary" data-run-id="${rid}" data-benchmarks="${jobBenchmarks}" data-solvers="${jobSolvers}">`;
    rowsHtml += `<td style="white-space:nowrap">${formatDate(run.date)}</td>`;
    rowsHtml += `<td>${run.branch}</td>`;
    rowsHtml += `<td>${run.score} (${run.passed}/${run.total})</td>`;
    rowsHtml += `<td>${formatCost(run.totalCost)}</td>`;
    rowsHtml += `<td>${formatDurationShort(run.maxDuration)}</td>`;
    rowsHtml += `<td>${formatTrendColumn(run.delta)}</td>`;
    rowsHtml += `<td><button class="toggle-details" data-target="${rid}">+</button></td>`;
    rowsHtml += '</tr>';

    for (const j of run.jobs) {
      const solver = getSolver(j);
      const matchB = !bv || j.benchmark === bv;
      const matchS = !sv || solver === sv;
      if (!matchB || !matchS) continue;
      const runLink = j.run_url ? `<a href="${j.run_url}">link</a>` : '';
      rowsHtml += `<tr class="run-detail hidden" data-parent="${rid}" data-benchmark="${j.benchmark}" data-solver="${solver}">`;
      rowsHtml += `<td>${j.benchmark}</td>`;
      rowsHtml += `<td>${solver}</td>`;
      rowsHtml += `<td>${statusIcon(j.resolved)} ${(j.score * 100).toFixed(0)}%</td>`;
      rowsHtml += `<td>${formatCost(j.details?.cost_usd)}</td>`;
      rowsHtml += `<td>${formatDurationShort(j.duration_seconds)}</td>`;
      rowsHtml += `<td></td>`;
      rowsHtml += `<td>${runLink}</td>`;
      rowsHtml += '</tr>';
    }
  }

  const tbody = document.getElementById('history-tbody');
  if (tbody) tbody.innerHTML = rowsHtml;

  const endIdx = start + pageRuns.length;
  const paginationHtml = renderPaginationControls(currentPage, totalPages, filteredRuns.length, start, endIdx);
  const pTop = document.getElementById('pagination-top');
  const pBottom = document.getElementById('pagination-bottom');
  if (pTop) pTop.innerHTML = paginationHtml;
  if (pBottom) pBottom.innerHTML = paginationHtml;
}

function renderRunHistory(allResults) {
  const benchmarks = [...new Set(allResults.map(r => r.benchmark))].sort();
  const solvers = [...new Set(allResults.map(r => getSolver(r)))].sort();
  const sortedRuns = groupByRunId(allResults);

  for (let i = 0; i < sortedRuns.length; i++) {
    const run = sortedRuns[i];
    const prev = sortedRuns[i + 1];
    if (!prev) {
      run.delta = null;
      continue;
    }
    run.delta = {
      accuracy: run.passRate - prev.passRate,
      cost: run.totalCost - prev.totalCost,
      duration: run.maxDuration - prev.maxDuration,
    };
  }

  allSortedRuns = sortedRuns;
  currentPage = 1;

  let html = '<div class="section-title">Run History</div>';
  html += '<p class="section-explanation">All benchmark runs, newest first. Trend arrows compare each run against the immediately preceding run. Green = improvement, red = regression.</p>';

  html += '<div class="filter-bar">';
  html += '<label>Benchmark: <select id="filter-benchmark"><option value="">All</option>';
  for (const b of benchmarks) html += `<option value="${b}">${b}</option>`;
  html += '</select></label>';
  html += '<label>Solver: <select id="filter-solver"><option value="">All</option>';
  for (const s of solvers) html += `<option value="${s}">${s}</option>`;
  html += '</select></label>';
  html += '</div>';

  html += '<div id="pagination-top" class="pagination"></div>';
  html += '<table id="history-table"><thead><tr>';
  html += '<th>Date</th><th>Branch</th><th>Pass Rate</th>';
  html += '<th>Cost</th><th>Duration</th><th>Trend</th><th></th>';
  html += '</tr></thead><tbody id="history-tbody"></tbody></table>';
  html += '<div id="pagination-bottom" class="pagination"></div>';

  setTimeout(() => {
    updateRunHistoryTable();

    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.toggle-details');
      if (!btn) return;
      const runId = btn.dataset.target;
      const details = document.querySelectorAll(`.run-detail[data-parent="${runId}"]`);
      const isHidden = details[0]?.classList.contains('hidden');
      details.forEach(row => row.classList.toggle('hidden'));
      btn.textContent = isHidden ? '−' : '+';
    });

    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.page-btn');
      if (!btn || btn.disabled) return;
      const page = parseInt(btn.dataset.page);
      if (page && page !== currentPage) {
        currentPage = page;
        updateRunHistoryTable();
      }
    });

    const benchSelect = document.getElementById('filter-benchmark');
    const solverSelect = document.getElementById('filter-solver');
    if (benchSelect) benchSelect.addEventListener('change', () => { currentPage = 1; updateRunHistoryTable(); });
    if (solverSelect) solverSelect.addEventListener('change', () => { currentPage = 1; updateRunHistoryTable(); });
  }, 0);

  return html;
}

// Section 7: PR Comparison
function renderPRComparison(allResults) {
  const prResults = allResults.filter(isPR);
  if (prResults.length === 0) return '';

  const mainResults = allResults.filter(isMainBranch);
  const mainLatest = getLatestByCombo(mainResults);

  let html = '<div class="section-title">Pull Request Comparison</div>';
  html += '<p class="section-explanation">Pull request benchmark results compared against the latest main branch baseline.</p>';
  html += '<table><thead><tr>';
  html += '<th>PR</th><th>Benchmark</th><th>Solver</th>';
  html += '<th>PR Score</th><th>Main Score</th><th>Delta</th>';
  html += '<th>PR Cost</th><th>Main Cost</th><th>Delta</th>';
  html += '<th>PR Duration</th><th>Main Duration</th>';
  html += '</tr></thead><tbody>';

  const sortedPR = [...prResults].sort((a, b) =>
    (b.timestamp || '').localeCompare(a.timestamp || '')
  );

  for (const r of sortedPR) {
    const solver = getSolver(r);
    const key = r.benchmark + '|' + solver;
    const baseline = mainLatest[key];

    const prLabel = r.pr_number
      ? `<a href="https://github.com/${REPO}/pull/${r.pr_number}">#${r.pr_number}</a>`
      : r.ref ? r.ref.replace('refs/pull/', '').replace('/merge', '') : '—';

    const prScore = r.score;
    const mainScore = baseline ? baseline.score : null;
    const scoreDelta = (prScore != null && mainScore != null) ? prScore - mainScore : null;

    const prCost = r.details?.cost_usd;
    const mainCost = baseline?.details?.cost_usd;
    const costDelta = (prCost != null && mainCost != null) ? prCost - mainCost : null;

    const prDur = r.duration_seconds;
    const mainDur = baseline?.duration_seconds;

    function formatDelta(val, invert) {
      if (val == null) return '—';
      const sign = val > 0 ? '+' : '';
      const cls = val > 0
        ? (invert ? 'trend-down' : 'trend-up')
        : val < 0
          ? (invert ? 'trend-up' : 'trend-down')
          : 'trend-neutral';
      return `<span class="${cls}">${sign}${typeof val === 'number' && Math.abs(val) < 10 ? val.toFixed(2) : val}</span>`;
    }

    html += '<tr>';
    html += `<td>${prLabel}</td>`;
    html += `<td>${r.benchmark}</td>`;
    html += `<td>${solver}</td>`;
    html += `<td>${prScore != null ? (prScore * 100).toFixed(0) + '%' : '—'}</td>`;
    html += `<td>${mainScore != null ? (mainScore * 100).toFixed(0) + '%' : '—'}</td>`;
    html += `<td>${formatDelta(scoreDelta, false)}</td>`;
    html += `<td>${formatCost(prCost)}</td>`;
    html += `<td>${formatCost(mainCost)}</td>`;
    html += `<td>${formatDelta(costDelta != null ? Number(costDelta.toFixed(2)) : null, true)}</td>`;
    html += `<td>${formatDurationShort(prDur)}</td>`;
    html += `<td>${formatDurationShort(mainDur)}</td>`;
    html += '</tr>';
  }

  html += '</tbody></table>';
  return html;
}

async function renderDashboard() {
  const container = document.getElementById('benchmark-dashboard');

  try {
    const allResults = await fetchJsonl();

    if (allResults && allResults.length > 0) {
      const mainResults = allResults.filter(isMainBranch);

      let html = '';
      html += renderHero(mainResults);
      html += renderAccuracyTrend(mainResults);
      html += renderDurationTrend(mainResults);
      html += renderCostTrend(mainResults);
      html += renderPerBenchmarkTable(mainResults);
      html += renderRunHistory(allResults);
      html += renderPRComparison(allResults);
      container.innerHTML = html;
      return;
    }

    container.innerHTML = '<p class="error-msg">No benchmark data available yet.<br>'
      + 'Run the benchmark workflow to generate initial data: Actions &rarr; Benchmark CI &rarr; Run workflow</p>';
  } catch (err) {
    container.innerHTML = `<p class="error-msg">${err.message}</p>`;
  }
}

renderDashboard();
</script>
