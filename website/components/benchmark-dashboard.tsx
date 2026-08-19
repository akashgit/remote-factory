"use client";

import { useEffect, useRef } from "react";

const REPO = "akashgit/remote-factory";
const JSONL_URL = `https://raw.githubusercontent.com/${REPO}/benchmark-data/results.jsonl`;

export function BenchmarkDashboard() {
  const ref = useRef<HTMLDivElement>(null);
  const loaded = useRef(false);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;

    async function load() {
      const container = ref.current;
      if (!container) return;

      try {
        const resp = await fetch(JSONL_URL);
        if (!resp.ok) {
          container.innerHTML =
            '<p style="opacity:0.6;font-style:italic">No benchmark data available yet. Run the benchmark workflow to generate initial data.</p>';
          return;
        }
        const text = await resp.text();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const results: any[] = text
          .trim()
          .split("\n")
          .filter(Boolean)
          .map((l) => {
            try {
              return JSON.parse(l);
            } catch {
              return null;
            }
          })
          .filter(Boolean);

        if (results.length === 0) {
          container.innerHTML =
            '<p style="opacity:0.6;font-style:italic">No benchmark data available yet.</p>';
          return;
        }

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const mainResults = results.filter((r: any) => r.ref === "refs/heads/main");

        let html = "<h2>Latest Results</h2>";
        html +=
          '<table><thead><tr><th>Benchmark</th><th>Solver</th><th>Result</th><th>Duration</th><th>Cost</th></tr></thead><tbody>';

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const byKey: Record<string, any> = {};
        for (const r of mainResults) {
          const solver = r.solver || r.details?.solver || "unknown";
          const key = `${r.benchmark}|${solver}`;
          if (!byKey[key] || r.timestamp > byKey[key].timestamp) {
            byKey[key] = r;
          }
        }

        for (const [, r] of Object.entries(byKey).sort(([a], [b]) =>
          a.localeCompare(b),
        )) {
          const solver = r.solver || r.details?.solver || "unknown";
          const score = r.score;
          const duration = r.duration_seconds;
          const cost = r.details?.cost_usd;
          const status = r.resolved
            ? '<span style="color:#22863a">PASS</span>'
            : '<span style="color:#cb2431">FAIL</span>';
          const dur =
            duration != null
              ? `${Math.floor(duration / 60)}m ${Math.round(duration % 60)}s`
              : "—";
          const costStr = cost != null ? `$${cost.toFixed(2)}` : "—";

          html += `<tr><td>${r.benchmark}</td><td>${solver}</td><td>${status} ${(score * 100).toFixed(0)}%</td><td>${dur}</td><td>${costStr}</td></tr>`;
        }

        html += "</tbody></table>";
        html += `<p style="opacity:0.6;font-size:0.85rem;margin-top:1rem">${results.length} total results across all branches. Showing latest main branch results per benchmark/solver combination.</p>`;

        container.innerHTML = html;
      } catch (err) {
        container.innerHTML = `<p style="color:#cb2431">${(err as Error).message}</p>`;
      }
    }

    load();
  }, []);

  return (
    <div ref={ref}>
      <p style={{ opacity: 0.6, fontStyle: "italic" }}>
        Loading benchmark results...
      </p>
    </div>
  );
}
