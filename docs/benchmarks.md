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
</style>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<script>
const REPO = 'akashgit/remote-factory';
const JSONL_URL = `https://raw.githubusercontent.com/${REPO}/benchmark-data/results.jsonl`;
const WORKFLOW = 'benchmark.yml';
const API = 'https://api.github.com';

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

async function fetchFromApi() {
  const resp = await fetch(
    `${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=20&status=completed`
  );
  if (!resp.ok) return null;
  const data = await resp.json();
  return data.workflow_runs || [];
}

function renderLatestTable(results) {
  const byKey = {};
  for (const r of results) {
    const solver = r.details?.solver || r.solver || 'unknown';
    const key = r.benchmark + '|' + solver;
    if (!byKey[key] || (r.timestamp > byKey[key].timestamp)) {
      byKey[key] = r;
    }
  }

  const latest = Object.values(byKey).sort((a, b) =>
    (a.benchmark + (a.details?.solver || '')).localeCompare(b.benchmark + (b.details?.solver || ''))
  );

  let html = '<div class="section-title">Latest Results</div>';
  html += '<table><thead><tr>';
  html += '<th>Benchmark</th><th>Solver</th><th>Result</th><th>Score</th>';
  html += '<th>Duration</th><th>Cost</th><th>Commit</th><th>Run</th>';
  html += '</tr></thead><tbody>';

  for (const r of latest) {
    const solver = r.details?.solver || r.solver || 'unknown';
    const commit = r.commit ? r.commit.substring(0, 7) : '—';
    const commitLink = r.commit
      ? `<a href="https://github.com/${REPO}/commit/${r.commit}"><code>${commit}</code></a>`
      : commit;
    const runLink = r.run_url
      ? `<a href="${r.run_url}">details →</a>`
      : '—';
    const cost = r.details?.cost_usd;

    html += '<tr>';
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
  return html;
}

function renderCharts(results) {
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

  const keys = Object.keys(series).sort();
  const colors = chartColors();

  let html = '<div class="charts-row">';
  html += '<div class="chart-container"><canvas id="duration-chart"></canvas></div>';
  html += '<div class="chart-container"><canvas id="cost-chart"></canvas></div>';
  html += '</div>';

  setTimeout(() => {
    const durCtx = document.getElementById('duration-chart');
    const costCtx = document.getElementById('cost-chart');
    if (!durCtx || !costCtx) return;

    const durDatasets = keys.map((k, i) => ({
      label: k,
      data: series[k].map(p => ({ x: p.x, y: p.duration != null ? p.duration / 60 : null })),
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '33',
      tension: 0.3,
      pointRadius: 3,
      spanGaps: true,
    }));

    const costDatasets = keys.map((k, i) => ({
      label: k,
      data: series[k].map(p => ({ x: p.x, y: p.cost })).filter(p => p.y != null),
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '33',
      tension: 0.3,
      pointRadius: 3,
      spanGaps: true,
    }));

    const commonOptions = {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          type: 'time',
          time: { tooltipFormat: 'MMM d, yyyy HH:mm' },
          ticks: { color: colors.text },
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
  const sorted = [...results].sort((a, b) =>
    (b.timestamp || '').localeCompare(a.timestamp || '')
  );

  let html = '<div class="section-title">Full History</div>';
  html += '<table><thead><tr>';
  html += '<th>Date</th><th>Benchmark</th><th>Solver</th><th>Result</th>';
  html += '<th>Score</th><th>Duration</th><th>Cost</th><th>Commit</th><th>Run</th>';
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

    html += '<tr>';
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
  return html;
}

function renderApiFallback(runs) {
  let html = '<div class="section-title">Recent Workflow Runs</div>';
  html += '<p class="error-msg">Benchmark data branch not available yet. Showing workflow run metadata from GitHub API.</p>';
  html += '<table><thead><tr>';
  html += '<th>Date</th><th>Commit</th><th>Trigger</th><th>Status</th><th></th>';
  html += '</tr></thead><tbody>';

  for (const run of runs) {
    const d = new Date(run.created_at);
    const date = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
      + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    const commit = run.head_sha ? run.head_sha.substring(0, 7) : '—';
    const conclusion = run.conclusion || 'running';
    const statusClass = conclusion === 'success' ? 'status-success'
      : conclusion === 'failure' ? 'status-failure' : 'status-pending';

    html += '<tr>';
    html += `<td>${date}</td>`;
    html += `<td><a href="https://github.com/${REPO}/commit/${run.head_sha}"><code>${commit}</code></a></td>`;
    html += `<td>${run.event}</td>`;
    html += `<td><span class="${statusClass}">${conclusion}</span></td>`;
    html += `<td><a href="${run.html_url}">details →</a></td>`;
    html += '</tr>';
  }

  html += '</tbody></table>';
  return html;
}

async function renderDashboard() {
  const container = document.getElementById('benchmark-dashboard');

  try {
    const results = await fetchJsonl();

    if (results && results.length > 0) {
      let html = '';
      html += renderLatestTable(results);
      html += renderCharts(results);
      html += renderHistoryTable(results);
      container.innerHTML = html;
      return;
    }

    const runs = await fetchFromApi();
    if (runs && runs.length > 0) {
      container.innerHTML = renderApiFallback(runs);
      return;
    }

    container.innerHTML = '<p class="error-msg">No benchmark data available yet.</p>';
  } catch (err) {
    container.innerHTML = `<p class="error-msg">${err.message}</p>`;
  }
}

renderDashboard();
</script>
