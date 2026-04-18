# Strategy — Cycle 7 (Dashboard UI/UX Focus)

**Date:** 2026-04-18
**Composite:** 0.852
**Focus:** dashboard UI/UX
**FEEC analysis:** No critical bugs (Fix=none), capability_surface at 0.611 is the biggest growth gap (Exploit), dashboard charting is new capability surface (Explore).

---

## H1: Sparklines on project cards + Chart.js radar modal for dimension breakdowns

- **Category:** Explore
- **What:**
  1. Add Chart.js via CDN (`<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`) to `index.html`
  2. Extend the `/api/projects/{name}/history` endpoint to include per-experiment dimension scores (parse from `.factory/experiments/NNN/eval_after.json`)
  3. Add a new `/api/projects/{name}/dimensions` endpoint returning the latest dimension breakdown (all 11 dimensions with name, score, weight, category)
  4. Add inline SVG sparklines to each project card showing the last 10-20 composite scores as a trend line (pure JS SVG generation — polyline in a 60x20 viewBox, no extra library needed)
  5. Add a click-to-expand dimension breakdown modal: clicking a project card's score opens a fixed-position overlay with a Chart.js radar chart showing all 11 dimensions (6 hygiene + 5 growth), color-coded by category (blue for hygiene, green for growth), with the composite score displayed large in the center
  6. Add color coding to the score display on project cards: green (>=0.8), yellow (0.6-0.8), red (<0.6)
- **Why:** The composite score is currently just a number on the project card — there is no trend visibility and no way to see which dimensions are strong vs weak. The research report identifies this as the highest-ROI improvement (Phase 1: Core Analytics). The CEO verdict explicitly prioritizes "sparklines + dimension radar chart" as #1. This transforms the dashboard from a status board into an analytical tool. The radar chart in particular makes the hygiene-vs-growth balance immediately visible, addressing the recurring concern about hygiene bias in strategy.
- **Growth dimension:** capability_surface
- **Expected impact:**
  - capability_surface: +0.03-0.05 (new API endpoints = new public functions, new JS rendering functions)
  - observability: +0.01 (dimension data surfaced to users)
  - Composite: +0.01-0.02
- **Priority:** HIGH
- **Scope:**
  - `factory/dashboard/static/index.html` — add Chart.js CDN, sparkline SVG generator, radar chart modal, score color coding
  - `factory/dashboard/app.py` — add `/api/projects/{name}/dimensions` endpoint, extend history endpoint with dimension data
- **Focus target:** dashboard UI/UX

---

## H2: KPI summary strip with aggregate metrics above the main grid

- **Category:** Explore
- **What:**
  1. Add a new `/api/summary` endpoint in `app.py` returning: `{ total_projects, active_projects, avg_score, experiments_today, keep_count_today, revert_count_today, total_experiments }`
  2. Add a KPI strip as the first row of the main grid (above the existing project/events/experiments panels): `grid-template-rows: 80px 1fr 1fr`
  3. Render 4 KPI cards in the strip using CSS flexbox:
     - **Projects**: total count with "X active" indicator (green dot count)
     - **Avg Score**: weighted average composite across all projects, color-coded (green/yellow/red), large 28px number
     - **Experiments**: total count with today's count highlighted, keep/revert split shown as a mini bar (green/red proportional segments)
     - **Keep Rate**: percentage across all experiments, with color coding
  4. Each KPI card gets a subtle hover state and uses the existing CSS custom property palette
  5. Update the responsive breakpoint: on mobile, the KPI strip becomes a 2x2 grid
- **Why:** The CEO verdict lists "KPI summary strip" as priority #2. Users landing on the dashboard currently have no at-a-glance health check — they must mentally aggregate data from individual project cards. A KPI strip answers "how is everything doing?" in under 1 second. The Stripe/Linear research pattern shows 4-6 KPI cards in the top 80-120px as the standard for developer dashboards. This is new capability surface (new endpoint, new rendering functions).
- **Growth dimension:** capability_surface
- **Expected impact:**
  - capability_surface: +0.02-0.03 (new API endpoint, new rendering functions)
  - Composite: +0.005-0.01
- **Priority:** MEDIUM
- **Scope:**
  - `factory/dashboard/static/index.html` — add KPI strip HTML/CSS/JS, update grid layout
  - `factory/dashboard/app.py` — add `/api/summary` endpoint
- **Focus target:** dashboard UI/UX

---

## H3: Score history line chart with hygiene/growth breakdown and keep/revert markers

- **Category:** Exploit
- **What:**
  1. Add a score history panel that appears when a project is selected — a full-width Chart.js line chart below the experiments table (or as a toggleable view within the experiments panel)
  2. The chart shows composite score over all experiments (x-axis = experiment ID, y-axis = score 0.0-1.0)
  3. Plot two series on the same chart: hygiene average (blue line) and growth average (green line), with composite as a thicker overlay
  4. Mark keep/revert decisions with green/red dots on the composite line
  5. Add hover tooltips showing experiment ID, hypothesis (first 40 chars), delta, and verdict
  6. Use the existing `/api/projects/{name}/history` data (already loaded for the experiments table) — extend it to include `score_before` alongside `score_after` so deltas can be visualized
  7. Add a toggle button in the experiments panel header to switch between table view and chart view
- **Why:** This directly exploits the biggest growth gap: capability_surface at 0.611 (the lowest growth dimension at 28% weight). Adding a rich charting view adds multiple new public functions (chart rendering, data transformation, toggle logic) increasing the surface count. The score history chart makes plateau detection and regression patterns visible — you can see at a glance whether the score is climbing, stalling, or oscillating. This is especially valuable given the 100% keep rate: the chart reveals whether keeps are producing meaningful score gains or marginal ones. Builds on Chart.js already added by H1.
- **Growth dimension:** capability_surface
- **Expected impact:**
  - capability_surface: +0.02-0.04 (chart rendering functions, data transformation, toggle logic)
  - Composite: +0.005-0.015
- **Priority:** MEDIUM
- **Scope:**
  - `factory/dashboard/static/index.html` — add chart panel, toggle button, Chart.js line chart config, tooltip formatting
  - `factory/dashboard/app.py` — extend history endpoint to include `score_before` and dimension scores per experiment
- **Focus target:** dashboard UI/UX

---

## Execution Order

1. **H1 first** (sparklines + radar) — highest visual impact, establishes Chart.js foundation, adds the dimension API endpoint that H3 depends on
2. **H2 second** (KPI strip) — independent of H1's charting, extends the layout with aggregate metrics
3. **H3 third** (score history) — builds on Chart.js from H1 and the extended history API

## Anti-patterns to Avoid
- Don't add React/Vue/Svelte — keep everything in the single HTML file with CDN libraries
- Don't add decorative animations — Vercel's lesson: "speed over visual flourish"
- Don't break SSE streaming performance — insert new events at top, cap at 200, no full re-renders
- Don't sacrifice information density for whitespace — pack more data per screen, not less

## Notes
- All 3 hypotheses target dashboard UI/UX per the Focus Directive (3/3)
- All 3 have explicit Growth dimension tags (capability_surface)
- Combined expected impact on capability_surface: +0.07-0.12 (0.611 -> ~0.68-0.73)
- Combined expected composite delta: +0.02-0.045
