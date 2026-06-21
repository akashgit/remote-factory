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
</style>

<script>
const REPO = 'akashgit/remote-factory';
const WORKFLOW = 'benchmark.yml';
const API = 'https://api.github.com';

async function apiFetch(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    if (resp.status === 403) throw new Error('GitHub API rate limit exceeded. Try again later.');
    throw new Error(`GitHub API error: ${resp.status}`);
  }
  return resp.json();
}

function statusIcon(conclusion) {
  if (conclusion === 'success') return '<span class="status-success">✅ pass</span>';
  if (conclusion === 'failure') return '<span class="status-failure">❌ fail</span>';
  if (conclusion === 'cancelled') return '<span class="status-pending">⏭ cancelled</span>';
  if (!conclusion) return '<span class="status-pending">⏳ running</span>';
  return '<span class="status-pending">' + conclusion + '</span>';
}

function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function formatDuration(startedAt, completedAt) {
  if (!startedAt || !completedAt) return '—';
  const ms = new Date(completedAt) - new Date(startedAt);
  const mins = Math.floor(ms / 60000);
  const secs = Math.floor((ms % 60000) / 1000);
  if (mins > 0) return mins + 'm ' + secs + 's';
  return secs + 's';
}

async function renderDashboard() {
  const container = document.getElementById('benchmark-dashboard');

  try {
    const data = await apiFetch(
      `${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=20&status=completed`
    );
    const runs = data.workflow_runs || [];

    if (runs.length === 0) {
      container.innerHTML = '<p class="error-msg">No benchmark runs found.</p>';
      return;
    }

    const jobPromises = runs.slice(0, 15).map(run =>
      apiFetch(`${API}/repos/${REPO}/actions/runs/${run.id}/jobs?per_page=30`)
        .then(d => ({ run, jobs: d.jobs || [] }))
    );
    const entries = await Promise.all(jobPromises);

    let html = '<table><thead><tr>';
    html += '<th>Date</th><th>Commit</th><th>Trigger</th><th>Benchmarks</th>';
    html += '<th>Duration</th><th>Status</th><th></th>';
    html += '</tr></thead><tbody>';

    for (const { run, jobs } of entries) {
      const benchmarkJobs = jobs.filter(j => j.name.startsWith('benchmark'));

      const benchmarks = benchmarkJobs.map(j => {
        const match = j.name.match(/benchmark\s*\(([^,]+),\s*([^,]+)/);
        if (!match) return null;
        const icon = j.conclusion === 'success' ? '✅' : j.conclusion === 'failure' ? '❌' : '⏳';
        return `<span class="benchmark-tag">${icon} ${match[1]} (${match[2]})</span>`;
      }).filter(Boolean).join('<br>');

      html += '<tr>';
      html += `<td>${formatDate(run.created_at)}</td>`;
      html += `<td><a href="https://github.com/${REPO}/commit/${run.head_sha}"><code>${run.head_sha.substring(0, 7)}</code></a></td>`;
      html += `<td>${run.event}</td>`;
      html += `<td>${benchmarks || '<span class="error-msg">—</span>'}</td>`;
      html += `<td>${formatDuration(run.run_started_at, run.updated_at)}</td>`;
      html += `<td>${statusIcon(run.conclusion)}</td>`;
      html += `<td><a href="${run.html_url}">details →</a></td>`;
      html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;

  } catch (err) {
    container.innerHTML = `<p class="error-msg">${err.message}</p>`;
  }
}

renderDashboard();
</script>
