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
#benchmark-dashboard .benchmark-tag {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  margin: 0.1rem 0;
  border-radius: 3px;
  font-size: 0.8rem;
  background: var(--md-default-fg-color--lightest);
}
#benchmark-dashboard .status-success { color: #22863a; }
#benchmark-dashboard .status-failure { color: #cb2431; }
#benchmark-dashboard .status-pending { color: #b08800; }
#benchmark-dashboard .error-msg {
  color: var(--md-default-fg-color--light);
  font-style: italic;
}
#benchmark-dashboard .section-title {
  margin-top: 2rem;
  margin-bottom: 0.5rem;
  font-size: 1.2rem;
  font-weight: 600;
}
#benchmark-dashboard .chart-container {
  position: relative;
  margin: 1rem 0 2rem 0;
  max-height: 350px;
}
#benchmark-dashboard .charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  margin: 1rem 0;
}
@media (max-width: 768px) {
  #benchmark-dashboard .charts-row {
    grid-template-columns: 1fr;
  }
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
#benchmark-dashboard .trend-placeholder {
  text-align: center;
  padding: 2rem;
  color: var(--md-default-fg-color--light);
  font-style: italic;
  border: 1px dashed var(--md-default-fg-color--lightest);
  border-radius: 8px;
  margin: 1rem 0;
}
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
    bg: dark ? '#1e1e1e' : '#fff',
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
    const solver = r.details?.solver || r.solver || 'unknown';
    const key = r.benchmark + '|' + solver;
    if (!byKey[key] || (r.timestamp > byKey[key].timestamp)) {
      byKey[key] = r;
    }
  }
  return byKey;
}

function renderSummaryCards(results) {
  const byKey = getLatestByCombo(results);

  const combos = Object.entries(byKey).sort(([a], [b]) => a.localeCompare(b));

  let html = '<div class="section-title">Latest Results</div>';
  html += '<div class="summary-cards">';

  for (const [key, r] of combos) {
    const solver = r.details?.solver || r.solver || 'unknown';
    const scoreVal = r.score;
    const scoreClass = scoreVal > 0 ? 'score-pass' : scoreVal === 0 ? 'score-zero' : 'score-none';
    const icon = r.resolved ? '✅' : '❌';
    const commit = r.commit ? r.commit.substring(0, 7) : '—';
    const commitLink = r.commit
      ? `<a href="https://github.com/${REPO}/commit/${r.commit}"><code>${commit}</code></a>`
      : commit;

    html += '<div class="summary-card">';
    html += `<div class="card-title">${r.benchmark} · ${solver}</div>`;
    html += `<div class="card-score ${scoreClass}">${icon} ${scoreVal}</div>`;
    html += '<div class="card-meta">';
    html += `${formatDurationShort(r.duration_seconds)} · ${formatCost(r.details?.cost_usd)}`;
    html += `<br>${commitLink}`;
    html += '</div>';
    html += '</div>';
  }

  html += '</div>';
  return html;
}

function renderComparisonChart(results) {
  const byKey = getLatestByCombo(results);

  const benchmarks = [...new Set(results.map(r => r.benchmark))].sort();
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
  html += '<div class="chart-container"><canvas id="comparison-chart"></canvas></div>';

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
            backgroundColor: '#ea4335',
            borderRadius: 4,
          },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
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

function renderTrendCharts(results) {
  const sorted = [...results].sort((a, b) =>
    (a.timestamp || '').localeCompare(b.timestamp || '')
  );

  const series = {};
  for (const r of sorted) {
    const solver = r.details?.solver || r.solver || 'unknown';
    const key = r.benchmark + ' / ' + solver;
    if (!series[key]) series[key] = [];
    const date = parseTimestamp(r.timestamp);
    if (date) {
      series[key].push({
        x: date,
        duration: r.duration_seconds,
        cost: r.details?.cost_usd,
        score: r.score,
      });
    }
  }

  const totalPoints = Object.values(series).reduce((sum, s) => sum + s.length, 0);
  if (totalPoints < 3) {
    return '<div class="section-title">Trends Over Time</div>'
      + '<div class="trend-placeholder">Trends will appear after at least 3 benchmark runs.</div>';
  }

  const keys = Object.keys(series).sort();
  const colors = chartColors();
  const pointSize = totalPoints < 10 ? 5 : 3;

  let html = '<div class="section-title">Trends Over Time</div>';
  html += '<div class="charts-row">';
  html += '<div class="chart-container"><canvas id="duration-chart"></canvas></div>';
  html += '<div class="chart-container"><canvas id="cost-chart"></canvas></div>';
  html += '</div>';

  setTimeout(() => {
    const durCtx = document.getElementById('duration-chart');
    const costCtx = document.getElementById('cost-chart');
    if (!durCtx || !costCtx) return;

    const shortLabel = (k) => {
      const [bench, solver] = k.split(' / ');
      return bench + ' (' + solver + ')';
    };

    const durDatasets = keys.map((k, i) => ({
      label: shortLabel(k),
      data: series[k].map(p => ({ x: p.x, y: p.duration != null ? p.duration / 60 : null })),
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '33',
      tension: 0.3,
      pointRadius: pointSize,
      spanGaps: true,
    }));

    const costDatasets = keys.map((k, i) => ({
      label: shortLabel(k),
      data: series[k].map(p => ({ x: p.x, y: p.cost })).filter(p => p.y != null),
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '33',
      tension: 0.3,
      pointRadius: pointSize,
      spanGaps: true,
    }));

    const commonOptions = {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: 'index', intersect: false },
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
          ticks: { color: colors.text },
          grid: { color: colors.grid },
        },
      },
      plugins: {
        legend: { labels: { color: colors.text } },
      },
    };

    new Chart(durCtx, {
      type: 'line',
      data: { datasets: durDatasets },
      options: {
        ...commonOptions,
        plugins: {
          ...commonOptions.plugins,
          title: { display: true, text: 'Duration (minutes)', color: colors.text },
        },
      },
    });

    new Chart(costCtx, {
      type: 'line',
      data: { datasets: costDatasets },
      options: {
        ...commonOptions,
        plugins: {
          ...commonOptions.plugins,
          title: { display: true, text: 'Cost (USD)', color: colors.text },
        },
      },
    });
  }, 0);

  return html;
}

function renderHistoryTable(results) {
  const benchmarks = [...new Set(results.map(r => r.benchmark))].sort();
  const solvers = [...new Set(results.map(r => r.details?.solver || r.solver || 'unknown'))].sort();

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
  html += '<th data-col="date">Date</th><th data-col="benchmark">Benchmark</th>';
  html += '<th data-col="solver">Solver</th><th data-col="result">Result</th>';
  html += '<th data-col="score">Score</th><th data-col="duration">Duration</th>';
  html += '<th data-col="cost">Cost</th><th data-col="commit">Commit</th><th data-col="run">Run</th>';
  html += '</tr></thead><tbody>';

  for (const r of sorted) {
    const solver = r.details?.solver || r.solver || 'unknown';
    const commit = r.commit ? r.commit.substring(0, 7) : '—';
    const commitLink = r.commit
      ? `<a href="https://github.com/${REPO}/commit/${r.commit}"><code>${commit}</code></a>`
      : commit;
    const runLink = r.run_url
      ? `<a href="${r.run_url}">details →</a>`
      : '—';
    const cost = r.details?.cost_usd;

    html += `<tr data-benchmark="${r.benchmark}" data-solver="${solver}">`;
    html += `<td style="white-space:nowrap">${formatDate(r.timestamp)}</td>`;
    html += `<td>${r.benchmark}</td>`;
    html += `<td>${solver}</td>`;
    html += `<td>${statusIcon(r.resolved)}</td>`;
    html += `<td>${r.score}</td>`;
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
    const results = await fetchJsonl();

    if (results && results.length > 0) {
      let html = '';
      html += renderSummaryCards(results);
      html += renderComparisonChart(results);
      html += renderTrendCharts(results);
      html += renderHistoryTable(results);
      container.innerHTML = html;
      return;
    }

    container.innerHTML = '<p class="error-msg">No benchmark data available yet.<br>Run the benchmark workflow to generate initial data: Actions → Benchmark CI → Run workflow</p>';
  } catch (err) {
    container.innerHTML = `<p class="error-msg">${err.message}</p>`;
  }
}

renderDashboard();
</script>
