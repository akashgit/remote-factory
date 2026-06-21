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
#benchmark-dashboard details {
  margin-bottom: 0.2rem;
}
#benchmark-dashboard details summary {
  cursor: pointer;
  padding: 0.4rem 0;
  font-size: 0.9rem;
}
#benchmark-dashboard details summary:hover {
  opacity: 0.7;
}
#benchmark-dashboard details table {
  margin: 0.3rem 0 0.5rem 1rem;
  font-size: 0.85rem;
}
#benchmark-dashboard details td {
  padding: 0.3rem 0.6rem;
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
  };
}

function formatDurationShort(seconds) {
  if (seconds == null) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? m + 'm ' + s + 's' : s + 's';
}

function formatDurationMinutes(seconds) {
  if (seconds == null) return '—';
  return (seconds / 60).toFixed(1) + 'm';
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

function formatDateShort(ts) {
  if (!ts) return '—';
  const iso = ts.replace(
    /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/,
    '$1-$2-$3T$4:$5:$6Z'
  );
  const d = new Date(iso);
  if (isNaN(d)) return ts;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
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

function statusEmoji(resolved) {
  return resolved ? '✅' : '❌';
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

function computeSolverAverages(mainResults) {
  const bySolver = {};
  for (const r of mainResults) {
    const solver = getSolver(r);
    if (!bySolver[solver]) bySolver[solver] = { durations: [], costs: [], count: 0 };
    bySolver[solver].count++;
    if (r.duration_seconds != null) bySolver[solver].durations.push(r.duration_seconds);
    if (r.details?.cost_usd != null) bySolver[solver].costs.push(r.details.cost_usd);
  }
  const result = {};
  for (const [solver, data] of Object.entries(bySolver)) {
    const avgDur = data.durations.length > 0
      ? data.durations.reduce((a, b) => a + b, 0) / data.durations.length : null;
    const avgCost = data.costs.length > 0
      ? data.costs.reduce((a, b) => a + b, 0) / data.costs.length : null;
    const totalCost = data.costs.length > 0
      ? data.costs.reduce((a, b) => a + b, 0) : null;
    result[solver] = { avgDur, avgCost, totalCost, count: data.count };
  }
  return result;
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
    const ts = entries[0].timestamp || '';
    const iso = ts.replace(
      /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/,
      '$1-$2-$3T$4:$5:$6Z'
    );
    const date = new Date(iso);
    if (isNaN(date)) continue;

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
  html += '<div style="width:100%;margin:2rem 0"><canvas id="comparison-chart" style="width:100%!important;height:400px!important"></canvas></div>';

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

// Section 3: Duration — line chart over time, then per-benchmark breakdown
function renderDurationSection(mainResults) {
  const runAvgs = computeRunAverages(mainResults);
  const hasTrend = runAvgs.factoryDurationPoints.length > 0 || runAvgs.claudeDurationPoints.length > 0;

  if (!hasTrend) return '';

  let html = '<div class="section-title">Average Duration Over Time (Main Branch)</div>';
  html += '<div style="width:100%;margin:2rem 0"><canvas id="avg-duration-chart" style="width:100%!important;height:400px!important"></canvas></div>';
  html += '<p class="footnote">Average across all benchmarks. Per-benchmark breakdown below.</p>';

  const byKey = getLatestByCombo(mainResults);
  const benchmarks = [...new Set(mainResults.map(r => r.benchmark))].sort();
  const factoryDurations = benchmarks.map(b => {
    const r = byKey[b + '|factory'];
    return r?.duration_seconds ?? null;
  });
  const claudeDurations = benchmarks.map(b => {
    const r = byKey[b + '|claude-code'];
    return r?.duration_seconds ?? null;
  });

  const hasBreakdown = factoryDurations.some(v => v !== null) || claudeDurations.some(v => v !== null);
  if (hasBreakdown) {
    html += '<div style="width:100%;margin:2rem 0"><canvas id="duration-breakdown-chart" style="width:100%!important;height:400px!important"></canvas></div>';
  }

  setTimeout(() => {
    const colors = chartColors();

    const avgCtx = document.getElementById('avg-duration-chart');
    if (avgCtx) {
      new Chart(avgCtx, {
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
                label: (item) => item.dataset.label + ': ' + formatDurationShort(item.raw),
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
    }

    if (hasBreakdown) {
      const breakdownCtx = document.getElementById('duration-breakdown-chart');
      if (breakdownCtx) {
        new Chart(breakdownCtx, {
          type: 'bar',
          data: {
            labels: benchmarks,
            datasets: [
              {
                label: 'Factory',
                data: factoryDurations,
                backgroundColor: '#4285f4',
                borderRadius: 4,
              },
              {
                label: 'Claude Code',
                data: claudeDurations,
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
              tooltip: {
                filter: (item) => item.raw !== null,
                callbacks: {
                  label: (item) => item.dataset.label + ': ' + formatDurationShort(item.raw),
                },
              },
            },
            scales: {
              x: {
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
      }
    }
  }, 0);

  return html;
}

// Section 4: Cost — line chart over time, then per-benchmark breakdown
function renderCostSection(mainResults) {
  const runAvgs = computeRunAverages(mainResults);
  const hasTrend = runAvgs.factoryCostPoints.length > 0 || runAvgs.claudeCostPoints.length > 0;

  if (!hasTrend) return '';

  const avgs = computeSolverAverages(mainResults);
  const factoryStats = avgs['factory'] || {};
  const claudeStats = avgs['claude-code'] || {};

  let html = '<div class="section-title">Average Cost Per Run Over Time (Main Branch)</div>';

  html += '<div class="avg-summary">';
  html += '<strong>Total Spend:</strong> ';
  html += `Factory ${formatCost(factoryStats.totalCost)} (${factoryStats.count || 0} runs)`;
  html += ` | Claude Code ${formatCost(claudeStats.totalCost)} (${claudeStats.count || 0} runs)`;
  html += '</div>';

  html += '<div style="width:100%;margin:2rem 0"><canvas id="avg-cost-chart" style="width:100%!important;height:400px!important"></canvas></div>';
  html += '<p class="footnote">Average across all benchmarks. Per-benchmark breakdown below. Vertex AI runs may show $0 cost when billing is handled at the platform level.</p>';

  const byKey = getLatestByCombo(mainResults);
  const benchmarks = [...new Set(mainResults.map(r => r.benchmark))].sort();
  const factoryCosts = benchmarks.map(b => {
    const r = byKey[b + '|factory'];
    return r?.details?.cost_usd ?? null;
  });
  const claudeCosts = benchmarks.map(b => {
    const r = byKey[b + '|claude-code'];
    return r?.details?.cost_usd ?? null;
  });

  const hasBreakdown = factoryCosts.some(v => v !== null) || claudeCosts.some(v => v !== null);
  if (hasBreakdown) {
    html += '<div style="width:100%;margin:2rem 0"><canvas id="cost-breakdown-chart" style="width:100%!important;height:400px!important"></canvas></div>';
  }

  setTimeout(() => {
    const colors = chartColors();

    const avgCtx = document.getElementById('avg-cost-chart');
    if (avgCtx) {
      new Chart(avgCtx, {
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
                label: (item) => item.dataset.label + ': ' + formatCost(item.raw),
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
    }

    if (hasBreakdown) {
      const breakdownCtx = document.getElementById('cost-breakdown-chart');
      if (breakdownCtx) {
        new Chart(breakdownCtx, {
          type: 'bar',
          data: {
            labels: benchmarks,
            datasets: [
              {
                label: 'Factory',
                data: factoryCosts,
                backgroundColor: '#4285f4',
                borderRadius: 4,
              },
              {
                label: 'Claude Code',
                data: claudeCosts,
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
              tooltip: {
                filter: (item) => item.raw !== null,
                callbacks: {
                  label: (item) => item.dataset.label + ': ' + formatCost(item.raw),
                },
              },
            },
            scales: {
              x: {
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
      }
    }
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

// Section 6: Full History — grouped by run_id
function renderHistoryTable(results) {
  const benchmarks = [...new Set(results.map(r => r.benchmark))].sort();
  const solvers = [...new Set(results.map(r => getSolver(r)))].sort();

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
    run.score = run.total > 0 ? (run.passed / run.total * 100).toFixed(0) + '%' : '—';
  }

  const sortedRuns = Object.values(runs).sort((a, b) =>
    (b.date || '').localeCompare(a.date || '')
  );

  let html = '<div class="section-title">Full History</div>';

  html += '<div class="filter-bar">';
  html += '<label>Benchmark: <select id="filter-benchmark"><option value="">All</option>';
  for (const b of benchmarks) html += `<option value="${b}">${b}</option>`;
  html += '</select></label>';
  html += '<label>Solver: <select id="filter-solver"><option value="">All</option>';
  for (const s of solvers) html += `<option value="${s}">${s}</option>`;
  html += '</select></label>';
  html += '</div>';

  html += '<table id="history-table"><thead><tr>';
  html += '<th>Date</th><th>Branch/PR</th><th>Passed</th>';
  html += '<th>Total</th><th>Score</th><th>Cost</th>';
  html += '<th>Duration</th><th>Details</th>';
  html += '</tr></thead><tbody>';

  for (const run of sortedRuns) {
    const commit = run.commit ? run.commit.substring(0, 7) : '';
    const commitSuffix = commit
      ? ` @ <a href="https://github.com/${REPO}/commit/${run.commit}"><code>${commit}</code></a>`
      : '';
    const runLink = run.run_url
      ? ` <a href="${run.run_url}">[GHA]</a>`
      : '';

    const jobBenchmarks = run.jobs.map(j => j.benchmark);
    const jobSolvers = run.jobs.map(j => getSolver(j));
    const benchmarkAttr = [...new Set(jobBenchmarks)].join(',');
    const solverAttr = [...new Set(jobSolvers)].join(',');

    html += `<tr data-benchmarks="${benchmarkAttr}" data-solvers="${solverAttr}">`;
    html += `<td style="white-space:nowrap">${formatDate(run.date)}</td>`;
    html += `<td>${run.branch}${commitSuffix}</td>`;
    html += `<td>${run.passed}</td>`;
    html += `<td>${run.total}</td>`;
    html += `<td>${run.score}</td>`;
    html += `<td>${formatCost(run.totalCost)}</td>`;
    html += `<td>${formatDurationShort(run.maxDuration)}</td>`;
    html += '<td>';

    html += `<details><summary>${run.passed}/${run.total} passed — ${run.branch} — ${formatDateShort(run.date)}${runLink}</summary>`;
    html += '<table>';
    html += '<tr><th>Benchmark</th><th>Solver</th><th>Result</th><th>Duration</th><th>Cost</th></tr>';
    for (const j of run.jobs) {
      const solver = getSolver(j);
      html += `<tr data-benchmark="${j.benchmark}" data-solver="${solver}">`;
      html += `<td>${j.benchmark}</td>`;
      html += `<td>${solver}</td>`;
      html += `<td>${statusEmoji(j.resolved)}</td>`;
      html += `<td>${formatDurationShort(j.duration_seconds)}</td>`;
      html += `<td>${formatCost(j.details?.cost_usd)}</td>`;
      html += '</tr>';
    }
    html += '</table></details>';

    html += '</td>';
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
        const rowBenchmarks = (row.dataset.benchmarks || '').split(',');
        const rowSolvers = (row.dataset.solvers || '').split(',');
        const matchB = !bv || rowBenchmarks.includes(bv);
        const matchS = !sv || rowSolvers.includes(sv);
        row.style.display = (matchB && matchS) ? '' : 'none';

        const detailRows = row.querySelectorAll('details table tr[data-benchmark]');
        for (const dr of detailRows) {
          const dMatchB = !bv || dr.dataset.benchmark === bv;
          const dMatchS = !sv || dr.dataset.solver === sv;
          dr.style.display = (dMatchB && dMatchS) ? '' : 'none';
        }
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
      html += renderDurationSection(mainResults);
      html += renderCostSection(mainResults);
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
