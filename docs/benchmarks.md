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
  cursor: pointer;
  user-select: none;
}
#benchmark-dashboard th:hover {
  opacity: 0.7;
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
#benchmark-dashboard .summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 1rem;
  margin: 1rem 0 2rem 0;
}
#benchmark-dashboard .summary-card {
  border: 1px solid var(--md-default-fg-color--lightest);
  border-radius: 8px;
  padding: 1rem 1.2rem;
}
#benchmark-dashboard .summary-card .card-title {
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 0.6rem;
  opacity: 0.7;
}
#benchmark-dashboard .summary-card .card-score {
  font-size: 1.6rem;
  font-weight: 700;
  margin-bottom: 0.3rem;
}
#benchmark-dashboard .summary-card .card-meta {
  font-size: 0.8rem;
  opacity: 0.6;
}
#benchmark-dashboard .card-score.score-pass { color: #22863a; }
#benchmark-dashboard .card-score.score-zero { color: #b08800; }
#benchmark-dashboard .card-score.score-none { color: #888; }
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
#benchmark-dashboard .delta-positive { color: #22863a; }
#benchmark-dashboard .delta-negative { color: #cb2431; }
#benchmark-dashboard .delta-neutral { color: #888; }
</style>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<script>
const REPO = 'akashgit/remote-factory';
const JSONL_URL = `https://raw.githubusercontent.com/${REPO}/benchmark-data/results.jsonl`;

const CHART_COLORS = [
  '#4285f4', '#ea4335', '#34a853', '#fbbc04',
  '#8e24aa', '#00acc1', '#ff7043', '#9ccc65'
];

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
  const iso = ts.replace(
    /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/,
    '$1-$2-$3T$4:$5:$6Z'
  );
  const d = new Date(iso);
  if (isNaN(d)) return ts;
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

function formatDelta(val, invert) {
  if (val == null) return '—';
  const sign = val > 0 ? '+' : '';
  const cls = val > 0
    ? (invert ? 'delta-negative' : 'delta-positive')
    : val < 0
      ? (invert ? 'delta-positive' : 'delta-negative')
      : 'delta-neutral';
  return `<span class="${cls}">${sign}${typeof val === 'number' && Math.abs(val) < 10 ? val.toFixed(2) : val}</span>`;
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

// Section 1: Latest Main Branch Results
function renderLatestMain(mainResults) {
  const byKey = getLatestByCombo(mainResults);
  const combos = Object.entries(byKey).sort(([a], [b]) => a.localeCompare(b));

  if (combos.length === 0) {
    return '<div class="section-title">Latest Results (main branch)</div>'
      + '<p class="error-msg">No main branch results yet.</p>';
  }

  let html = '<div class="section-title">Latest Results (main branch)</div>';
  html += '<table><thead><tr>';
  html += '<th>Benchmark</th><th>Solver</th><th>Score</th><th>Duration</th>';
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
    html += `<td>${statusIcon(r.resolved)} ${r.score}</td>`;
    html += `<td>${formatDurationShort(r.duration_seconds)}</td>`;
    html += `<td>${formatCost(r.details?.cost_usd)}</td>`;
    html += `<td>${commitLink}</td>`;
    html += `<td>${runLink}</td>`;
    html += '</tr>';
  }

  html += '</tbody></table>';
  return html;
}

// Section 2: Factory vs Claude Code Comparison
function renderComparisonChart(mainResults) {
  const byKey = getLatestByCombo(mainResults);

  const benchmarks = [...new Set(mainResults.map(r => r.benchmark))].sort();
  const factoryScores = benchmarks.map(b => {
    const r = byKey[b + '|factory'];
    return r ? r.score : null;
  });
  const claudeScores = benchmarks.map(b => {
    const r = byKey[b + '|claude-code'];
    return r ? r.score : null;
  });

  const hasAnyData = factoryScores.some(v => v !== null) || claudeScores.some(v => v !== null);
  if (!hasAnyData) return '';

  let html = '<div class="section-title">Factory vs Claude Code</div>';
  html += '<div style="width: 100%; margin: 2rem 0;">';
  html += '<canvas id="comparison-chart" style="width: 100% !important; height: 400px !important;"></canvas>';
  html += '</div>';

  setTimeout(() => {
    const ctx = document.getElementById('comparison-chart');
    if (!ctx) return;
    const colors = chartColors();

    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: benchmarks,
        datasets: [
          {
            label: 'Factory',
            data: factoryScores,
            backgroundColor: '#4285f4',
            borderRadius: 4,
          },
          {
            label: 'Claude Code',
            data: claudeScores,
            backgroundColor: '#ff7043',
            borderRadius: 4,
          },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: colors.text } },
          tooltip: { filter: (item) => item.raw !== null },
        },
        scales: {
          x: {
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
          y: {
            min: 0,
            max: 1,
            title: { display: true, text: 'Score', color: colors.text },
            ticks: { color: colors.text },
            grid: { color: colors.grid },
          },
        },
      },
    });
  }, 0);

  return html;
}

// Section 3: Duration Trend
function renderDurationChart(results) {
  const sorted = [...results].sort((a, b) =>
    (a.timestamp || '').localeCompare(b.timestamp || '')
  );

  const series = {};
  for (const r of sorted) {
    const solver = getSolver(r);
    const key = r.benchmark + ' / ' + solver;
    if (!series[key]) series[key] = [];
    const date = parseTimestamp(r.timestamp);
    if (date && r.duration_seconds != null) {
      series[key].push({ x: date, y: r.duration_seconds });
    }
  }

  const keys = Object.keys(series).filter(k => series[k].length >= 2).sort();
  if (keys.length === 0) return '';

  const colors = chartColors();

  let html = '<div class="section-title">Duration Trend</div>';
  html += '<div style="width: 100%; margin: 2rem 0;">';
  html += '<canvas id="duration-chart" style="width: 100% !important; height: 400px !important;"></canvas>';
  html += '</div>';

  setTimeout(() => {
    const ctx = document.getElementById('duration-chart');
    if (!ctx) return;

    const datasets = keys.map((k, i) => ({
      label: k,
      data: series[k],
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '33',
      tension: 0.3,
      pointRadius: 4,
      spanGaps: true,
    }));

    new Chart(ctx, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: colors.text } },
        },
        scales: {
          x: {
            type: 'time',
            time: {
              unit: 'day',
              displayFormats: { day: 'MMM d' },
              tooltipFormat: 'MMM d, yyyy HH:mm',
            },
            title: { display: true, text: 'Date', color: colors.text },
            ticks: { color: colors.text, maxRotation: 45, maxTicksLimit: 10 },
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

// Section 4: Cost Trend
function renderCostChart(results) {
  const sorted = [...results].sort((a, b) =>
    (a.timestamp || '').localeCompare(b.timestamp || '')
  );

  const series = {};
  for (const r of sorted) {
    const solver = getSolver(r);
    const key = r.benchmark + ' / ' + solver;
    if (!series[key]) series[key] = [];
    const date = parseTimestamp(r.timestamp);
    const cost = r.details?.cost_usd;
    if (date && cost != null) {
      series[key].push({ x: date, y: cost });
    }
  }

  const keys = Object.keys(series).filter(k => series[k].length >= 2).sort();
  if (keys.length === 0) return '';

  const colors = chartColors();

  let html = '<div class="section-title">Cost Trend</div>';
  html += '<div style="width: 100%; margin: 2rem 0;">';
  html += '<canvas id="cost-chart" style="width: 100% !important; height: 400px !important;"></canvas>';
  html += '</div>';

  setTimeout(() => {
    const ctx = document.getElementById('cost-chart');
    if (!ctx) return;

    const datasets = keys.map((k, i) => ({
      label: k,
      data: series[k],
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '33',
      tension: 0.3,
      pointRadius: 4,
      spanGaps: true,
    }));

    new Chart(ctx, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: colors.text } },
        },
        scales: {
          x: {
            type: 'time',
            time: {
              unit: 'day',
              displayFormats: { day: 'MMM d' },
              tooltipFormat: 'MMM d, yyyy HH:mm',
            },
            title: { display: true, text: 'Date', color: colors.text },
            ticks: { color: colors.text, maxRotation: 45, maxTicksLimit: 10 },
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

// Section 5: PR Comparison
function renderPRComparison(allResults) {
  const prResults = allResults.filter(isPR);
  if (prResults.length === 0) return '';

  const mainResults = allResults.filter(isMainBranch);
  const mainLatest = getLatestByCombo(mainResults);

  let html = '<div class="section-title">Pull Request Results</div>';
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

    html += '<tr>';
    html += `<td>${prLabel}</td>`;
    html += `<td>${r.benchmark}</td>`;
    html += `<td>${solver}</td>`;
    html += `<td>${prScore != null ? prScore : '—'}</td>`;
    html += `<td>${mainScore != null ? mainScore : '—'}</td>`;
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

// Section 6: Full History
function renderHistoryTable(results) {
  const benchmarks = [...new Set(results.map(r => r.benchmark))].sort();
  const solvers = [...new Set(results.map(r => getSolver(r)))].sort();

  let html = '<div class="section-title">Full History</div>';

  html += '<div class="filter-bar">';
  html += '<label>Benchmark: <select id="filter-benchmark"><option value="">All</option>';
  for (const b of benchmarks) html += `<option value="${b}">${b}</option>`;
  html += '</select></label>';
  html += '<label>Solver: <select id="filter-solver"><option value="">All</option>';
  for (const s of solvers) html += `<option value="${s}">${s}</option>`;
  html += '</select></label>';
  html += '</div>';

  const sorted = [...results].sort((a, b) =>
    (b.timestamp || '').localeCompare(a.timestamp || '')
  );

  html += '<table id="history-table"><thead><tr>';
  html += '<th>Date</th><th>Ref</th><th>Benchmark</th>';
  html += '<th>Solver</th><th>Score</th><th>Duration</th>';
  html += '<th>Cost</th><th>Commit</th><th>Run</th>';
  html += '</tr></thead><tbody>';

  for (const r of sorted) {
    const solver = getSolver(r);
    const commit = r.commit ? r.commit.substring(0, 7) : '—';
    const commitLink = r.commit
      ? `<a href="https://github.com/${REPO}/commit/${r.commit}"><code>${commit}</code></a>`
      : commit;
    const runLink = r.run_url
      ? `<a href="${r.run_url}">details</a>`
      : '—';
    const cost = r.details?.cost_usd;

    const refLabel = r.ref
      ? r.ref.replace('refs/heads/', '').replace('refs/pull/', 'PR ').replace('/merge', '')
      : '—';

    html += `<tr data-benchmark="${r.benchmark}" data-solver="${solver}">`;
    html += `<td style="white-space:nowrap">${formatDate(r.timestamp)}</td>`;
    html += `<td>${refLabel}</td>`;
    html += `<td>${r.benchmark}</td>`;
    html += `<td>${solver}</td>`;
    html += `<td>${statusIcon(r.resolved)} ${r.score}</td>`;
    html += `<td>${formatDurationShort(r.duration_seconds)}</td>`;
    html += `<td>${formatCost(cost)}</td>`;
    html += `<td>${commitLink}</td>`;
    html += `<td>${runLink}</td>`;
    html += '</tr>';
  }

  html += '</tbody></table>';

  setTimeout(() => {
    const benchSelect = document.getElementById('filter-benchmark');
    const solverSelect = document.getElementById('filter-solver');
    if (!benchSelect || !solverSelect) return;

    function applyFilters() {
      const bv = benchSelect.value;
      const sv = solverSelect.value;
      const rows = document.querySelectorAll('#history-table tbody tr');
      for (const row of rows) {
        const matchB = !bv || row.dataset.benchmark === bv;
        const matchS = !sv || row.dataset.solver === sv;
        row.style.display = (matchB && matchS) ? '' : 'none';
      }
    }

    benchSelect.addEventListener('change', applyFilters);
    solverSelect.addEventListener('change', applyFilters);
  }, 0);

  return html;
}

async function renderDashboard() {
  const container = document.getElementById('benchmark-dashboard');

  try {
    const allResults = await fetchJsonl();

    if (allResults && allResults.length > 0) {
      const mainResults = allResults.filter(isMainBranch);

      let html = '';
      html += renderLatestMain(mainResults);
      html += renderComparisonChart(mainResults);
      html += renderDurationChart(allResults);
      html += renderCostChart(allResults);
      html += renderPRComparison(allResults);
      html += renderHistoryTable(allResults);
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
