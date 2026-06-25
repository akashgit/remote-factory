# Expected Behavior: Researcher Agent

## 1. Identity & Responsibility

The Researcher is the factory's expert investigator and knowledge synthesizer. It surveys codebases, distills external research into actionable insights, and connects disparate findings into a coherent picture. Its reports are the foundation that every downstream decision rests on — the Strategist's hypotheses, the CEO's verdicts, and the Builder's implementation choices all trace back to Researcher output.

**What the Researcher IS:**
- An investigator — runs `factory study`, reads code, searches the web, reads archive sources
- A knowledge synthesizer — turns raw data from multiple sources into structured, actionable reports
- A domain expert — provides similar-project analysis, tech stack recommendations, failure-mode solutions, and cross-project patterns

**What the Researcher is NOT:**
- NOT a code writer — never modifies source code (constraint explicit in Mode 1, implicit in all modes)
- NOT an eval runner — never runs `pytest`, `ruff`, `mypy`, or `python eval/score.py`
- NOT a strategist — provides findings and recommendations, but does not generate hypotheses or build plans
- NOT a decision-maker — reports data for the Strategist and CEO to act on

**Relationship to other agents:**
- Spawned by CEO via `factory agent researcher --task "..." --project $PROJECT_PATH`
- Output read by CEO for review, then by Strategist for hypothesis generation
- In Build/Design: 3 parallel researchers produce tagged outputs; CEO combines them
- In Research: Receives Failure Analyst output as primary input (no direct interaction with Failure Analyst)
- Never interacts directly with Builder, QA, or Archivist

**Four operating modes:**
- Mode 1: Discovery (Discover workflow)
- Mode 2: Research (Build, Design, Improve workflows)
- Mode 3: Self-Improvement Research (Meta workflow, or when target project is the factory)
- Mode 4: Failure Research (Research workflow)

---

## 2. Per-Workflow Behavior

### Workflow: Build / Design (Parallel x3)

**Phase:** Phase 1 — Research (Parallel)
**Spawned by:** CEO spawns 3 researcher instances in a SINGLE Bash call with `&` + `wait`. Each uses `--review-tag` for distinct output files.
**Inputs received:**
  - `--review-tag similar`: Task to find similar projects, existing solutions, prior art. Reads `.factory/archive/` for prior knowledge.
  - `--review-tag techstack`: Task to identify best tech stack, architecture patterns, framework comparisons.
  - `--review-tag pitfalls`: Task to identify pitfalls, common mistakes, MVP scope best practices. Reads `.factory/archive/` for past build lessons.

**Expected process (ordered steps):**

*Researcher `similar`:*
  1. Search the web for similar projects, existing solutions, and prior art
  2. Analyze strengths, weaknesses, and market positioning of found projects
  3. Check `.factory/archive/` for prior knowledge on similar builds
  4. Write findings to `.factory/strategy/research-similar.md` covering: similar projects found (with links), what they do well/what's missing, differentiation opportunities

*Researcher `techstack`:*
  1. Identify the best technology stack for this type of project
  2. Find architecture patterns and best practices
  3. Evaluate framework/library options with trade-offs
  4. Write findings to `.factory/strategy/research-techstack.md` covering: recommended tech stack with rationale, architecture patterns, framework comparisons

*Researcher `pitfalls`:*
  1. Identify potential pitfalls and common mistakes for this type of project
  2. Research MVP scope best practices
  3. Check `.factory/archive/` for lessons from past builds
  4. Write findings to `.factory/strategy/research-pitfalls.md` covering: pitfalls to avoid, MVP scope recommendation, lessons from past builds

**Expected outputs/artifacts:**
  - `.factory/strategy/research-similar.md` — similar projects analysis (by `similar` instance)
  - `.factory/strategy/research-techstack.md` — tech stack recommendations (by `techstack` instance)
  - `.factory/strategy/research-pitfalls.md` — pitfalls and scope (by `pitfalls` instance)

**Handoff:** All 3 must complete (CEO waits at barrier). CEO reads all 3, combines into `.factory/strategy/research-combined.md`, then applies CEO Review — Research gate. On PROCEED, the combined research goes to the Strategist.

---

### Workflow: Improve

**Phase:** Phase 2 — Researcher (single instance, synchronous)
**Spawned by:** CEO spawns 1 researcher synchronously after `factory study` completes
**Inputs received:**
  - `.factory/strategy/observations.md` — output from `factory study`
  - `.factory/strategy/backlog.md` — work queue
  - `.factory/config.json` — factory configuration
  - README, pyproject.toml — project context
  - `.factory/archive/` — prior knowledge
  - Experiment history (accessible via `factory history`)
  - Optional: Focus Directive from CEO task for targeted mode

**Expected process (ordered steps):**
  1. **Run local study:** `factory study "$PROJECT_PATH"` for interaction logs + shallow search
  2. **Read the backlog:** Read `.factory/strategy/backlog.md`. Assess which items are achievable, blocked, already done, or obsolete. Note status in report for Strategist.
  3. **Read project context:** README, pyproject.toml, experiment history, current strategy
  4. **Search externally:** Use `WebSearch` for similar projects, best practices, relevant techniques (5–8 queries; 3–5 in targeted mode)
  5. **Read deeply:** Use `WebFetch` on top 3–5 most promising search results
  6. **Check prior knowledge:** Read `.factory/archive/` for cross-project patterns and prior learnings
  7. **Synthesize:** Write structured research report

**Targeted mode (when CEO task includes Focus Directive):**
  - Scope research to target item only — read only the target item from backlog
  - Focus web searches on the specific target (e.g., "WebSocket best practices in Python")
  - Limit WebSearch to 3–5 queries, all related to the target
  - Keep research tight — inform one specific implementation, not a broad survey

**Expected outputs/artifacts:**
  - `.factory/strategy/research.md` (or `research-local.md` depending on CEO's task directive) with sections:
    - `## Project Summary` — brief project overview and current state
    - `## External Research Findings` — similar projects, best practices, techniques with source URLs
    - `## Prior Knowledge (Archive)` — relevant findings from `.factory/archive/`, or "No archive available"
    - `## Recommended Focus Areas` — actionable insights for Strategist, ranked by expected impact
  - Optionally: new source notes to `.factory/archive/sources/`

**Handoff:** CEO reads output at `.factory/reviews/researcher-latest.md` and the written `research-local.md`. Applies CEO Review — Research gate. On PROCEED, Strategist receives the research plus CEO's verdict notes.

---

### Workflow: Research

**Phase:** Phase 2 — Researcher (single instance, synchronous, Mode 4 — Failure Research)
**Spawned by:** CEO spawns 1 researcher synchronously after Failure Analyst completes
**Inputs received:**
  - `.factory/strategy/failure_analysis.md` — Failure Analyst's categorized failure modes, frequency counts, root cause breakdowns
  - `.factory/config.json` — research target config (objective, metric, target value, mutable/fixed surfaces)
  - `.factory/archive/sources/` — prior knowledge on failure categories
  - Mutable surfaces list (which files the Builder can change)
  - Fixed surfaces list (which files MUST NOT be changed)

**Expected process (ordered steps):**
  1. **Read the failure analysis:** Load `.factory/research/runs/<cycle>/failure_analysis.md` or `.factory/strategy/failure_analysis.md` — this is the primary input
  2. **Extract dominant failure modes:** From the Failure Distribution section, identify top 2–3 failure categories by frequency
  3. **Read research target config:** Understand the objective, mutable surfaces, and fixed surfaces
  4. **Check prior knowledge FIRST:** Read `.factory/archive/sources/` for prior knowledge on these failure categories. Only WebSearch for topics NOT already covered.
  5. **Search for targeted solutions:** For each dominant failure mode, WebSearch for known solutions, workarounds, similar systems that solved same problem, techniques targeting the failure pattern. Spend 60%+ of search budget on the #1 failure category.
  6. **Read deeply:** Use `WebFetch` on top 3–5 most promising results
  7. **Map solutions to mutable surfaces:** For each finding, note which mutable surface files would need to change
  8. **Synthesize:** Write structured research report focused on actionable fixes

**Expected outputs/artifacts:**
  - `.factory/strategy/research.md` (or `research-local.md`) with sections:
    - `## Context` — research target, current metric, dominant failure modes
    - `## Prior Knowledge (Archive)` — relevant prior findings
    - `## Solution Research by Failure Mode` — per-category sections with root cause summary, external findings, recommended approach, mutable surface, confidence level
    - `## Cross-Cutting Findings` — patterns across multiple failure categories
    - `## References` — URLs and sources consulted

**Handoff:** CEO reads output, applies CEO Review — Research gate. On PROCEED, Strategist receives research along with failure analysis for hypothesis generation.

---

### Workflow: Meta

**Phase:** Phase 1 — Researcher (single instance, synchronous, Mode 3 — Self-Improvement) AND Phase 4 — Test Researcher (single instance, synchronous)
**Spawned by:** CEO spawns researcher twice in Meta workflow — once for cross-project analysis, once for test analysis

**Instance 1: Cross-Project Research (Phase 1)**

**Inputs received:**
  - `.factory/strategy/insights.md` — cross-project patterns generated by `factory insights`
  - Current playbooks at `~/.factory/playbooks/<role>.md` and `factory/agents/playbooks/<role>.md`
  - `.factory/archive/sources/` — prior research notes
  - `.factory/archive/patterns/patterns.md` — cross-project patterns already discovered

**Expected process (ordered steps):**
  1. **Read cross-project insights** at `.factory/strategy/insights.md` — analyze which hypothesis categories succeed and fail across projects
  2. **Read current playbooks** — identify what guidance exists and what's missing
  3. **Read prior knowledge FIRST:** `.factory/archive/sources/` and `.factory/archive/patterns/patterns.md`. Only WebSearch for topics NOT already covered.
  4. **Search externally:** WebSearch for self-evolution topics (5–8 queries): "self-evolving software agents", "autonomous software improvement loop", "meta-learning agent architecture", "LLM agent self-improvement", "automated code quality improvement"
  5. **Identify recurring patterns, anti-patterns, and improvement opportunities** — compare agent performance across projects
  6. **Write findings** to `.factory/strategy/research-local.md`

**Expected outputs/artifacts:**
  - `.factory/strategy/research-local.md` with sections:
    - `## Self-Improvement Context` — cross-project insights summary, category success rates, design space coverage
    - `## External Research: Self-Evolution` — relevant papers, projects, techniques
    - `## Recommendations by Dimension` — table mapping dimensions to findings and recommendations
    - `## Recommended Focus Areas` — actionable insights ranked by impact

**Instance 2: Test Researcher (Phase 4)**

**Inputs received:**
  - `.factory/strategy/test-inventory.md` — test inventory from `pytest --co -q`

**Expected process (ordered steps):**
  1. Read test inventory
  2. Analyze for redundant tests (overlapping coverage), dead tests (testing nothing meaningful), flaky tests (inconsistent results)
  3. Write findings with specific test names and reasons for removal

**Expected outputs/artifacts:**
  - `.factory/strategy/test-analysis.md` — specific test names, overlap analysis, removal recommendations with reasons

**Handoff:** Instance 1: CEO applies Research gate, then Strategist reads research for playbook diff generation. Instance 2: User approval steering point, then Test Builder deletes approved tests.

---

## 3. Invariants (MUST always hold)

**INV-1: Run local study before external search (Modes 2, 3).**
> "Always run local study first — it's fast baseline context."
> — `researcher.md:65`

Trace check: `factory study` call or local file reads should precede any `WebSearch` tool call.

**INV-2: WebSearch query limits.**
> "Limit WebSearch to 5-8 queries (3-5 in targeted mode)."
> — `researcher.md:66` (Mode 2), `researcher.md:149` (Mode 3), `researcher.md:214` (Mode 4)

Trace check: Count `WebSearch` tool calls in session. Standard: 5–8. Targeted: 3–5. Exceeding limit is a constraint violation.

**INV-3: WebFetch page limits.**
> "Limit WebFetch to 3-5 pages."
> — `researcher.md:67` (Mode 2), `researcher.md:150` (Mode 3), `researcher.md:215` (Mode 4)

Trace check: Count `WebFetch` tool calls in session. Should not exceed 5.

**INV-4: No calendar-time estimates.**
> "Do not include calendar-time estimates (e.g., '8-10 weeks', '6 months'). The factory uses AI agents, not human teams — duration estimates are meaningless."
> — `researcher.md:71`

Trace check: Grep output files for duration patterns (weeks, months, sprints, quarters). CEO will REDIRECT if found.

**INV-5: Write report even if external search fails.**
> "Write report even if external search fails — include local findings."
> — `researcher.md:69`

Trace check: Output file must exist and have at minimum Project Summary and Recommended Focus Areas (Mode 2) or Context + one Solution Research section (Mode 4).

**INV-6: Mode 4 — prioritize dominant failure mode.**
> "Prioritize the dominant failure mode — spend 60%+ of your search budget on the #1 failure category."
> — `researcher.md:221`

Trace check: In Mode 4, majority of `WebSearch` queries should relate to the dominant failure category identified in `failure_analysis.md`.

**INV-7: Read archive before web search (Modes 3, 4).**
> "Read prior knowledge FIRST: Read `.factory/archive/sources/` for prior knowledge. Only WebSearch for topics NOT already covered."
> — `researcher.md:139-140` (Mode 3), `researcher.md:204` (Mode 4)

Trace check: File reads of `.factory/archive/` should precede `WebSearch` calls.

---

## 4. Constraints & Forbidden Actions

**Forbidden across all modes:**
- Modifying source code files — researcher is read-only analysis agent
- Running evals, tests, or linters — these are QA/Evaluator responsibilities
- Generating hypotheses or build plans — Strategist's job
- Including calendar-time estimates in any output

**Mode 1 (Discovery) specific:**
- "Limit scope to reading and analyzing existing project artifacts — do not modify source code" (`researcher.md:32`)
- Do not add eval dimensions the project can't actually run
- Weight tests highest (0.4–0.5), lint second (0.2–0.3)
- Set `human_reviewed: false` in eval profile

**Mode 4 (Failure Research) specific:**
- "Do NOT do general domain research — Mode 2 handles that. Mode 4 is laser-focused on the failures" (`researcher.md:217`)
- "Map every finding to a mutable surface. Findings that require changing fixed surfaces should be noted as constraints, not recommendations" (`researcher.md:218`)
- Must read failure analysis FIRST before any external search
- Must not propose changes to `fixed_surfaces`

---

## 5. Failure Modes & Diagnostic Signals

| Failure mode | Trace signal | Example issue |
|---|---|---|
| **Calendar-time estimates in output** — Researcher includes "8-10 weeks" or similar human-timeline estimates in report | Output file contains patterns like "N weeks", "N months", "N sprints", "Q1/Q2". CEO will REDIRECT. | — |
| **Excessive web search** — Researcher exceeds 5–8 WebSearch query limit, burning tokens on broad surveys | `WebSearch` tool call count > 8 in standard mode or > 5 in targeted mode. Session runs longer than expected. | — |
| **Missing local study** — Researcher jumps straight to WebSearch without running `factory study` first | No `Bash` call with `factory study` before first `WebSearch` call. Report lacks local context. | — |
| **General research in Mode 4** — Researcher does broad domain research instead of failure-targeted search | WebSearch queries don't reference failure categories from `failure_analysis.md`. Report sections don't map to failure modes. | — |
| **Empty or minimal report** — Researcher produces report missing required sections | Output file missing required sections (Project Summary + Recommended Focus Areas for Mode 2; Context + Solution Research for Mode 4). CEO will REDIRECT. | — |
| **Fixed surface recommendations in Mode 4** — Researcher recommends changes to fixed_surfaces files | Report's Solution Research sections reference files in the `fixed_surfaces` list as recommended changes rather than constraints. | — |
| **Archive skip** — Researcher ignores `.factory/archive/` before doing web search | No `Read` calls to `.factory/archive/sources/` or `.factory/archive/patterns/` before `WebSearch` calls in Modes 3 or 4. Duplicates prior research. | — |

---

## 6. Interaction Protocol

**How results are communicated:**
- All output is written to files, not returned via stdout (except Mode 1 which writes eval artifacts directly)
- Standard output path: `.factory/strategy/research.md` or `.factory/strategy/research-local.md` (depending on CEO's task specification)
- Tagged outputs (Build/Design): `.factory/strategy/research-<tag>.md`
- Agent output auto-captured to `.factory/reviews/researcher-latest.md` (or `researcher-<tag>-latest.md`)
- Optional source notes written to `.factory/archive/sources/<source-name>.md`

**Output file format by mode:**

| Mode | Output file | Required sections |
|---|---|---|
| Mode 1 (Discovery) | `.factory/eval_profile.json`, `eval/score.py` | eval dimensions, weights, commands |
| Mode 2 (Research) | `.factory/strategy/research.md` | Project Summary, External Research Findings, Prior Knowledge, Recommended Focus Areas |
| Mode 3 (Self-Improvement) | `.factory/strategy/research.md` | Self-Improvement Context, External Research: Self-Evolution, Recommendations by Dimension, Recommended Focus Areas |
| Mode 4 (Failure Research) | `.factory/strategy/research.md` | Context, Prior Knowledge, Solution Research by Failure Mode, Cross-Cutting Findings, References |

**CEO review criteria for Researcher:**
- Covered the right topics? Enough depth?
- Web research included? (explicit in assessment criteria)
- Any gaps or blind spots in the analysis?
- **No calendar-time estimates** — REDIRECT immediately if present
- Mode-specific: technology landscape coverage (Build/Design), weak dimensions analysis (Improve), dominant failure mode solutions (Research), cross-project pattern support (Meta)

**Exit conditions:**
- Mode 1: All required files written (`eval_profile.json`, `eval/score.py`), or error reported to CEO
- Mode 2: `research.md` written with at least Project Summary and Recommended Focus Areas
- Mode 3: `research.md` written with Self-Improvement Context and Recommendations by Dimension tables populated
- Mode 4: `research.md` written with at least Context, one Solution Research section for dominant failure mode, and References
