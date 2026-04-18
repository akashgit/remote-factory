---
date: 2026-04-18
type: research
project: remote-factory
focus: dashboard-ui-ux
source: factory-researcher
tags:
  - factory
  - research
  - dashboard
  - ui-ux
---

# Research Report — Factory Dashboard UI/UX

## Project Summary

The Factory Dashboard (`/Users/akash/cursor-projects/remote-factory/factory/dashboard/`) is a FastAPI-served single HTML file that monitors multi-agent software evolution experiments across projects. It displays project cards, a live SSE event stream, and an experiments table. Current state: ~550 lines of inline HTML/CSS/JS, functional dark-themed dev tool UI, but minimal visual richness — no charts, no score trends, no dimension breakdowns, basic project cards.

## External Research

### Similar Projects & What They Teach

#### Vercel Dashboard (2026 redesign)
- **What it is:** Developer deployment platform dashboard, rolled out Feb 26, 2026
- **Key principle:** "Speed over visual flourish" — developers are allergic to bad UX, reject aesthetic flourishes that slow them down
- **What makes it polished:** Deep understanding of developer workflows, eliminates friction points, makes the UI disappear while providing clarity and power
- **Pattern:** Complexity without obscurity — simplifies interface while maintaining access to advanced features
- **Lesson for Factory:** Don't add decorative animations. Prioritize performance and instant legibility. The dashboard should feel like a terminal, not a marketing site.

#### Linear, Notion, Stripe Patterns
- **Sidebar navigation:** 240-280px persistent sidebar (collapsible to 64px icons). Scales cleanly from 5 to 50 features with nested groups.
- **Information density:** Prioritize data over whitespace — dashboard users are power users who want information, not breathing room
- **KPI cards:** Top 80-120px of content area = prime real estate. Display 4-6 most actionable KPIs with: primary number, trend indicator, sparkline
- **Loading states:** Content-shaped placeholders with shimmer animation reduce perceived load time by 20-30% vs spinners
- **Lesson for Factory:** Add a score trend sparkline to each project card. Consider a sidebar for projects list (currently left panel). Show 4-6 top-level KPIs above the events/experiments panels (total projects, active cycles, avg composite score, today's experiments).

### Best Practices for Real-Time Event Streams

#### SSE UI Patterns (from Railway, Vercel, GitHub Actions logs)
- **Streaming defaults:** Railway `railway up` streams build logs by default, with `--json` flag for structured output
- **Follow mode:** Vercel uses `--follow` flag for live streaming (continues for up to 5 minutes unless interrupted)
- **JSON Lines format:** Vercel and Railway both support `--json` output to pipe to other tools
- **Frontend optimization techniques:**
  - Update state only on change (don't re-render on every event)
  - Batch or throttle events (avoid 60 updates/sec)
  - Use refs for high-frequency updates (bypass React state)
  - Virtualized lists for 1000+ events (react-window pattern)
  - Offload heavy calculations to Web Workers
- **Common patterns:**
  - Grouping events by deployment/cycle
  - Collapsing/expanding event groups
  - Filtering by event type, project, agent
  - Highlighting errors/failures with visual prominence
- **Lesson for Factory:** Add event filtering UI (dropdown or chips for event types). Consider grouping events by cycle or experiment ID. Add a "pause stream" toggle to freeze the view while inspecting. Current 200-event DOM cap is good — keep it.

### Lightweight Charting for Vanilla JS

#### Chart.js (Most Recommended)
- **Bundle size:** 48KB full, tree-shakeable to 14KB for basic charts
- **Vanilla JS:** Works without build tools via CDN: `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`
- **Usage:** Canvas-based, requires `<canvas id="chart"></canvas>`, instantiate with `new Chart(ctx, config)`
- **Supported charts:** Line, bar, radar, doughnut, pie, polarArea, bubble, scatter
- **Lesson for Factory:** Perfect fit. Add via CDN. Use line charts for score trends, radar charts for dimension breakdowns, bar charts for experiment counts by verdict.

#### Alternatives Considered
- **Tremor:** Simplest but very basic, good for quick internal dashboards (Factory is internal, but we want richer viz)
- **AnyChart:** Framework-agnostic, lightweight, but overkill for our needs
- **Apache ECharts:** 400KB+, supports canvas+SVG, realtime for 100k+ datapoints — too heavy for Factory
- **CanvasJS:** 10x faster than Flash/SVG, but Chart.js is more widely used and better documented
- **Verdict:** Chart.js wins for balance of features, size, and vanilla JS support

#### Sparklines for Inline Mini-Charts
- **Sparklines.js:** Vanilla JS, zero dependencies, updated Feb 2026, canvas/VML rendering
- **mitjafelicijan/sparklines:** Tiny SVG sparkline library, zero deps, data attributes API
- **Pure CSS approaches:** Lightweight, but less flexible than SVG/canvas
- **Integration pattern:** Inline in project cards, table cells, or KPI cards. Don't render 100+ per frame — use virtualization for long lists.
- **Lesson for Factory:** Add sparklines to project cards showing last 10-20 composite scores as a trend line. Use Sparklines.js (canvas) or mitjafelicijan/sparklines (SVG). SVG is cleaner for static dashboards.

### Score Visualization Patterns

#### Composite Metrics & Breakdowns
- **Scorecard pattern (Adobe Analytics, Power BI, Looker):**
  - Single primary metric prominently displayed
  - Drill-down breakdowns by dimensions
  - Red-Orange-Yellow-Green status indicators in heatmaps
  - Sparklines for trend-over-time
- **Dashboard pattern (Stripe, Databox):**
  - 4-6 KPI cards with: number, comparison (vs previous period), single visual
  - Dimension breakdowns appear on hover or in expandable panels
- **Visualization approaches:**
  - **Radar charts** for multi-dimensional scores (good for 5-11 dimensions)
  - **Bar charts** (horizontal) for dimension comparisons
  - **Gauge charts** for single composite score with threshold zones
  - **Sparklines** for trends (tiny line charts, no axes)
- **Lesson for Factory:** Show composite score as large number on project card. On click, expand to show radar chart of all 11 dimensions (6 hygiene + 5 growth). Add sparkline of last 20 composite scores. Use color coding: green (≥0.8), yellow (0.6-0.8), red (<0.6).

## Prior Knowledge (from Vault)

### From `00-Factory/Dashboard.md`
- Factory currently tracks 3 active projects (remote-factory, test-idea, cp-agent)
- Dashboard is the central hub — should be the go-to place to see project health at a glance
- ACE playbook evolution table shows cross-project insights (60 experiments, 3 roles evolved)

### From `10-Projects/remote-factory/Dashboard.md`
- Current composite score: 0.849 (up from 0.802 in Cycle 6)
- Weakest dimension: research_grounding (0.575)
- 100% keep rate across 23 experiments — indicates evals may be too easy (per Patterns.md anti-pattern)
- Web dashboard runs on `http://localhost:8420`

### From `10-Projects/remote-factory/Exp-033-dashboard-logging.md`
- Experiment 33 added structlog to all 9 dashboard functions
- Dashboard module handles SSE streaming, project scanning, TSV parsing
- Observability improved from 0.783 to 0.805 (+0.022)

### From `00-Factory/Patterns.md`
- **eval_saturation:** 100% keep rates make progress unmeasurable — need harder evals, variance testing
- **feedback_loop_not_tradeoff:** Tech debt and feature velocity aren't opposites; clean systems sustain speed
- **cross_project_hypothesis_transfer:** Winning hypotheses should be tested on similar projects
- **decomposed_eval_scoring:** Separate planning quality from execution success in agent evals

### Key Insight
The dashboard is already instrumented with structlog (Exp 33). The next evolution should focus on **visual richness** and **analytical depth** — showing trends, dimension breakdowns, and cross-project patterns. The 100% keep rate suggests we need better visibility into **why** experiments succeed, not just **that** they succeed.

## Recommended Focus Areas

### 1. Score Trends & Dimension Breakdowns (Highest Impact)
**Why:** The composite score is the primary success metric, but it's currently just a number. Users can't see:
- How the score evolved over time (upward trend? plateau? spikes?)
- Which dimensions are weak vs strong
- How hygiene vs growth dimensions balance out

**What to add:**
- **Sparkline on project cards:** Last 10-20 composite scores as a tiny line chart (use mitjafelicijan/sparklines SVG library)
- **Expandable dimension view:** Click a project card → modal/panel with radar chart showing all 11 dimensions (use Chart.js radar chart)
- **Score history chart:** Full-width line chart showing composite score over all experiments (Chart.js line chart with dual-axis for hygiene/growth)

**Implementation approach:**
- Add Chart.js via CDN: `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`
- Add sparklines.js via CDN: `<script src="https://cdn.jsdelivr.net/npm/sparklines/sparklines.js"></script>` or inline SVG generation
- Fetch dimension data from API (extend `/api/projects/:name/history` to include per-experiment dimension scores)
- Render radar chart in a modal overlay (CSS: `position: fixed; z-index: 1000; backdrop-filter: blur(4px)`)

**Priority:** HIGH — this directly addresses the "composite score visibility" gap

### 2. Event Stream Filtering & Grouping (Medium Impact)
**Why:** The event stream currently shows all events in chronological order, capped at 200. As projects scale, this becomes noisy. Users need to:
- Filter events by type (agent.*, experiment.*, cycle.*, etc.)
- Filter events by project
- Group events by cycle or experiment ID
- Pause the stream to inspect details

**What to add:**
- **Filter chips:** Row of clickable filter chips above event stream (e.g., "Agents", "Experiments", "Cycles", "Errors"). Click to toggle.
- **Project filter:** Dropdown or search input to show events only for selected project
- **Pause/Resume button:** Toggle to freeze the stream (stop adding new events to DOM)
- **Event grouping:** Collapsible groups by cycle (e.g., "Cycle 6 — 3 experiments") with expand/collapse chevron

**Implementation approach:**
- Add filter state to JS: `let activeFilters = new Set(['agent.*', 'experiment.*'])`
- Modify `addEvent()` to check filters before inserting into DOM
- Add CSS for filter chips: `.filter-chip { padding: 4px 8px; border-radius: 12px; cursor: pointer; }`
- Use local storage to persist filter preferences: `localStorage.setItem('eventFilters', JSON.stringify([...activeFilters]))`

**Priority:** MEDIUM — improves usability for multi-project setups, less critical for 1-3 projects

### 3. Top-Level KPI Summary (Medium Impact)
**Why:** Users landing on the dashboard don't have a quick "health check" view. The dashboard should answer:
- How many projects are being managed?
- How many cycles ran today?
- What's the average composite score across all projects?
- How many experiments were kept vs reverted today?

**What to add:**
- **KPI strip above main grid:** 4-6 cards showing:
  1. Total projects (with "X active" badge)
  2. Avg composite score (with color coding: green ≥0.8, yellow 0.6-0.8, red <0.6)
  3. Experiments today (with keep/revert split)
  4. ACE evolution status (e.g., "3 roles updated")
- **Sparklines on KPI cards:** Tiny trend lines showing score evolution, experiment count trends

**Implementation approach:**
- Add new API endpoint: `/api/summary` returning `{ total_projects, active_projects, avg_score, experiments_today, keep_count_today, revert_count_today, ace_last_update }`
- Add new grid row above main panels: `grid-template-rows: 100px 1fr 1fr`
- Use CSS flexbox for KPI cards: `.kpi-strip { display: flex; gap: 12px; padding: 12px 24px; }`
- Each KPI card: 200px wide, primary number (large font), label (small), sparkline (inline SVG)

**Priority:** MEDIUM — nice-to-have, provides at-a-glance health check

### 4. Expandable Experiment Details (Low Impact, High Polish)
**Why:** The experiments table shows ID, verdict, delta, score, and truncated hypothesis (60 chars). Users can't see:
- Full hypothesis text
- Which files changed
- Build/test/lint status before/after
- Agent logs or error messages

**What to add:**
- **Expandable table rows:** Click a row → expand to show:
  - Full hypothesis
  - Files changed (with +/- line counts)
  - Dimension deltas (table: dimension name, before, after, delta)
  - Link to GitHub PR/issue (if available)
  - Link to `.factory/experiments/NNN/` artifacts

**Implementation approach:**
- Add hidden `<tr class="exp-details">` after each experiment row
- Toggle visibility on click: `row.nextElementSibling.classList.toggle('hidden')`
- Fetch full experiment data: `/api/projects/:name/experiments/:id` returning `{ hypothesis, files_changed, dimensions, pr_url, issue_url }`
- Render nested table for dimension deltas

**Priority:** LOW — nice-to-have, but most users will check GitHub PRs or `.factory/` artifacts directly

### 5. Performance & Interaction Polish (Low Impact, High Feel)
**Why:** The dashboard is functional but feels basic. Small touches that make it feel polished:
- Skeleton loading states (instead of "Loading...")
- Smooth transitions on card hovers, filter toggles
- Keyboard shortcuts (e.g., `/` to focus search, `p` to pause stream)
- Dark mode toggle (currently dark-only)
- Responsive layout improvements (mobile view is usable but cramped)

**What to add:**
- **Skeleton loaders:** Replace "Loading..." with pulsing content-shaped placeholders
- **Hover states:** Subtle scale/shadow on project cards
- **Keyboard shortcuts:** Add `keydown` listener for `/` (search), `p` (pause), `f` (focus filters)
- **Dark/light mode toggle:** CSS custom properties already support this — add a toggle button in header
- **Mobile polish:** Use `@container` queries (if targeting modern browsers) for responsive cards

**Implementation approach:**
- Add skeleton CSS: `.skeleton { background: linear-gradient(90deg, var(--surface) 25%, var(--border) 50%, var(--surface) 75%); animation: shimmer 2s infinite; }`
- Add keyboard listener: `document.addEventListener('keydown', e => { if (e.key === '/') focusSearch(); })`
- Add light mode CSS: `:root.light { --bg: #ffffff; --surface: #f6f8fa; --text: #24292f; ... }`
- Toggle with button: `document.documentElement.classList.toggle('light')`

**Priority:** LOW — polish pass after core features are in

## Technology Recommendations

### Charting
- **Chart.js via CDN:** `https://cdn.jsdelivr.net/npm/chart.js` (48KB, tree-shakeable to 14KB)
  - Use for: line charts (score trends), radar charts (dimension breakdowns), bar charts (experiment counts)
  - Canvas-based, works in vanilla JS, no build tools needed
- **Sparklines.js or mitjafelicijan/sparklines:** For inline mini-charts
  - Use for: project card score trends, KPI card trends
  - SVG approach (mitjafelicijan) is cleaner for static dashboards

### CSS Patterns
- **CSS Grid for layout:** Already using `grid-template-columns: 280px 1fr; grid-template-rows: 1fr 1fr`. Extend to 3 rows for KPI strip.
- **CSS custom properties for theming:** Already using `:root { --bg, --surface, --border, ... }`. Add light mode variants.
- **Shimmer animation for skeletons:** `@keyframes shimmer { 0% { background-position: -200px 0; } 100% { background-position: 200px 0; } }`
- **Backdrop blur for modals:** `backdrop-filter: blur(4px)` for dimension breakdown modals

### Data Fetching
- **Extend existing API endpoints:**
  - `/api/projects` → add `dimensions` field (array of `{ name, score }`)
  - `/api/projects/:name/history` → add `dimensions` field per experiment
  - `/api/summary` (new) → return top-level KPIs
  - `/api/projects/:name/experiments/:id` (new) → return full experiment details
- **SSE already working well:** No changes needed, just add client-side filtering

### Keep It Simple
- **No React, no build tools, no npm:** All recommendations fit in a single HTML file with CDN libraries
- **Progressive enhancement:** Dashboard works without JS (shows "Enable JavaScript" message), works better with charts
- **Inline everything:** CSS in `<style>`, JS in `<script>`, no external files beyond CDN libraries

## Specific Patterns to Adopt

### 1. Vercel-Style Information Density
- Remove decorative whitespace. Pack more data per screen.
- Example: Project cards currently have 12px padding, 4px margin. Tighten to 8px padding, 2px margin.

### 2. Stripe-Style KPI Cards
- Structure: `<div class="kpi-card"><div class="kpi-value">0.849</div><div class="kpi-label">Avg Score</div><svg class="kpi-sparkline">...</svg></div>`
- 200-280px wide, primary number at 32px font, label at 11px font, sparkline at 40px height

### 3. Railway-Style Event Filtering
- Row of chips above event stream: "All", "Agents", "Experiments", "Cycles", "Errors"
- Click to toggle. Active chips have `background: var(--accent); color: var(--bg)`

### 4. Linear-Style Expandable Rows
- Click experiment row → expand to show nested details panel
- Nested panel has light background (`background: var(--surface)`) and inset border

### 5. GitHub-Style Skeleton Loaders
- Replace "Loading..." with content-shaped placeholders
- Use shimmer animation: `background: linear-gradient(90deg, ...); animation: shimmer 2s infinite;`

## What NOT to Do (Anti-Patterns)

### 1. Don't Add Heavy Frameworks
- **Anti-pattern:** "Let's rewrite this in React/Vue/Svelte for better state management"
- **Why it's bad:** Kills the single-file simplicity, adds build step, increases complexity
- **Instead:** Vanilla JS + CDN libraries is perfect for this use case

### 2. Don't Overload with Animations
- **Anti-pattern:** "Add slide-in animations, fade transitions, loading spinners with easing"
- **Why it's bad:** Developers hate decorative animations. Vercel's key lesson: "Speed over visual flourish"
- **Instead:** Subtle hover states, instant interactions, shimmer for loading states only

### 3. Don't Make It Pretty Before Functional
- **Anti-pattern:** "Let's redesign the color scheme, try new fonts, add gradients"
- **Why it's bad:** Dashboard users want data, not aesthetics. Polish comes after functionality.
- **Instead:** Focus on score trends, dimension breakdowns, event filtering first. Polish last.

### 4. Don't Add Features Without Clear Use Cases
- **Anti-pattern:** "What if users want to export to CSV? Or create custom views? Or collaborate?"
- **Why it's bad:** Feature bloat. Factory dashboard has 1 user (Akash) and 3 projects. Solve real needs first.
- **Instead:** Wait for pain points to emerge. Only add features that address actual friction.

### 5. Don't Ignore Mobile/Responsive
- **Anti-pattern:** "This is a dev tool, no one will use it on mobile"
- **Why it's bad:** Even dev tools get checked on phones (e.g., "Did the cycle finish?" while away from desk)
- **Instead:** Keep the existing responsive layout. Add `@container` queries for card-level responsiveness.

### 6. Don't Break SSE Streaming Performance
- **Anti-pattern:** "Let's re-render the entire event list on every SSE message"
- **Why it's bad:** Causes jank, dropped frames, sluggish UI
- **Instead:** Insert new events at the top, cap at 200, use `DocumentFragment` for batch inserts

### 7. Don't Sacrifice Information Density for Whitespace
- **Anti-pattern:** "Let's add more padding, breathing room, card shadows"
- **Why it's bad:** Dashboard users are power users who want to see more data per screen
- **Instead:** Tighten spacing, increase table row density, pack more info into cards

## Summary: Prioritized Roadmap

**Phase 1: Core Analytics (Highest ROI)**
1. Add Chart.js via CDN
2. Add sparklines to project cards (score trends)
3. Add expandable dimension breakdown (radar chart modal)
4. Add score history chart (full-width line chart)

**Phase 2: Event Stream Improvements**
1. Add event type filtering (chips UI)
2. Add project filter dropdown
3. Add pause/resume toggle
4. Add event grouping by cycle

**Phase 3: Top-Level KPIs**
1. Add `/api/summary` endpoint
2. Add KPI strip above main grid
3. Add sparklines to KPI cards

**Phase 4: Polish**
1. Add skeleton loaders
2. Add keyboard shortcuts
3. Add dark/light mode toggle
4. Mobile layout improvements

**Out of Scope (For Now)**
- Expandable experiment details (low ROI, users check GitHub/artifacts)
- Custom dashboards / saved views (no use case yet)
- Export to CSV (no use case yet)
- Multi-user features (single user)

## Next Steps for Builder

1. **Start with sparklines:** Add mitjafelicijan/sparklines (SVG, zero deps) to project cards showing last 10 composite scores
2. **Add Chart.js:** Include via CDN, create radar chart modal for dimension breakdowns
3. **Extend API:** Add `dimensions` field to `/api/projects/:name/history` response (parse from `.factory/experiments/NNN/eval_after.json`)
4. **Test with real data:** Verify charts render correctly with remote-factory's 23 experiments
5. **Iterate:** Get feedback, refine, then move to event filtering

**Estimated effort:** 2-3 experiments to get Phase 1 working, 1-2 experiments for Phase 2, 1 experiment for Phase 3.
