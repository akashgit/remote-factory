# External Research — 2026-04-13

Compiled from 1 paper + 5 GitHub repos. These findings should inform the Strategist's hypotheses for the next improvement cycle.

## Known Bugs Found During Research Setup

### BUG: Obsidian CLI `_obsidian_create` uses `name=` for nested paths — silently fails
**File:** `factory/obsidian/notes.py` lines 138-150
**Issue:** The `_obsidian_create` function passes `name={path_with_slashes}` but the obsidian CLI rejects `/` in `name=`. It requires `path=` for nested paths. This means ALL vault writes to subdirectories (experiments, strategies, dashboards) silently fail via CLI and fall back to direct file write. The archivist thinks it's using the CLI but it's always falling back.
**Fix:** Use `path=` parameter instead of `name=` for paths containing `/`, or always use `path=`.

### BUG: `_obsidian_create` overwrites without checking existence
Experiment notes get rewritten every time `factory archive` runs, even if unchanged. Should check if note exists first and skip.

---

## Source 1: Meta-Harness (arxiv 2603.28052)

**What:** A system that optimizes LLM "harnesses" (the surrounding code infrastructure) by giving a coding agent access to a queryable filesystem archive of prior experiments — full execution traces, not compressed metrics.

**Key findings:**
- Full trace access achieved **50.0% accuracy** vs 34.6% with scores-only and 34.9% with LLM summaries
- Harness design creates **6x performance variation** — the scaffolding matters more than the model
- System independently discovered a four-route retrieval strategy with subject-specific policies — no human specification
- Achieves equivalent performance using **0.1x evaluations** by leveraging richer diagnostics

**Actionable ideas for Factory:**
1. **Filesystem as queryable archive**: Expose full experiment traces (tool calls, outputs, errors) via standard file operations (grep, diff, read) instead of compressing into metrics
2. **No mutation operators**: Let agents rewrite code freely based on diagnosed issues — no pre-specified transformation templates
3. **Confound isolation**: Track which changes were bundled together; when regressions occur, decompose compound interventions to identify harmful components
4. **Additive modification strategy**: After identifying confounds, pivot to changes that extend rather than replace working code
5. **Don't compress too early**: Preserve raw logs/traces until the agent queries them

---

## Source 2: karpathy/autoresearch

**What:** The original autonomous ML research framework. Agent iteratively modifies code, trains for fixed 5-minute intervals, evaluates, keeps or reverts, loops forever.

**Key patterns:**
1. **Single-metric optimization** with fixed evaluation budget ensures comparability
2. **Git as experiment ledger**: Each commit = one experiment. Discard via `git reset --hard HEAD~1`
3. **Markdown-as-instructions** (`program.md`): Natural language replaces brittle config files
4. **Simplicity preference**: When metrics are comparable, simpler code wins
5. **Multi-phase emergence**: When scaled to 16 GPUs, agents independently developed factorial grid search strategies without instruction
6. **TSV experiment ledger** — simpler than JSON for grep/analysis (Factory already uses this)

**Actionable ideas:**
- Formalize "one commit = one experiment" more strictly
- Add "code simplification" as tiebreaker in strategy scoring
- Explicit "never ask permission" loop instruction in SKILL.md

---

## Source 3: HKUDS/OpenSpace

**What:** A self-evolving skill engine for AI agents. Agents autonomously learn reusable skills from successful executions, auto-repair broken workflows, and share improvements across a community database.

**Key architecture:**
- **Four-layer system**: MCP interface → Evolution Engine → Skill Database (SQLite + version DAG) → Discovery Layer
- **Three evolution modes**: FIX (repair degraded), DERIVED (create specialized variants), CAPTURED (extract novel patterns)
- **Results**: 72.8% value capture on 50 real tasks, 46% token reduction through skill reuse, 165 skills autonomously evolved

**Actionable ideas:**
1. **Version DAG over linear history**: Enable skill/pattern specialization without destroying generalizations — Factory's linear experiment list could become a DAG
2. **Three-trigger evolution system**: Post-execution analysis + tool degradation monitoring + periodic health scanning
3. **Hybrid retrieval** (BM25 + embeddings + LLM ranking) for finding relevant prior experiments
4. **Minimal diffs for repairs**: Auto-fix generates patches, not full rewrites
5. **Cascading quality monitoring**: When upstream breaks, trigger batch evolution across dependent components
6. **Token cost as evolution driver**: Track and optimize token spend per experiment

---

## Source 4: uditgoenka/autoresearch

**What:** Generalized autoresearch framework as a Claude Code skill. Transforms AI assistants into "relentless improvement engines" through constraint-driven loops: Modify → Verify → Keep/Discard → Repeat.

**Key patterns:**
1. **Priority heuristic**: Fix crashes → Exploit successes → Explore new → Combine strategies (FEEC)
2. **Git-based experiment memory**: Full diff history, atomic revert via pre-verification commits
3. **Mechanical-only metrics**: No subjective judgment — only measurable metrics
4. **Stuck protocol**: "Re-read everything, combine strategies, attempt radical changes"
5. **Planning wizard**: Converts plain language goals into configured runs with dry-run validation

**Actionable ideas:**
- Adopt FEEC priority heuristic in Strategist — currently strategy selection is ad-hoc
- Pre-verification commits enable cleaner state management than post-facto rollback
- "Stuck protocol" is missing from Factory — need escape hatch when improvement stalls
- Multi-persona security audits (STRIDE + OWASP + red-team) for guard enhancement

---

## Source 5: yibie/awesome-autoresearch (curated list)

**What:** Curated list of 143+ autonomous research projects across 9 categories.

**Critical patterns across projects:**
1. **Fixed-budget experiments** (2-10 min runtime caps) prevent unbounded compute waste
2. **Evaluator hardening required**: Agents game metrics unless isolated with walk-forward validation — 71 experiments showing loops drift unless experiments are isolated (Cerebras blog)
3. **Git-backed persistence**: Most successful implementations use commits as experiment ledgers
4. **Multi-stage screening**: Cheap simulations filter before expensive validation (scout-promote)
5. **Variance-aware acceptance**: Rejecting noisy wins prevents drift

**Notable projects worth studying:**
- **Sibyl**: Dual-loop (inner research-iteration + outer self-evolution) architecture
- **autoresearch-autoresearch**: Bilevel system where outer loops rewrite search mechanisms themselves
- **EvoSkill**: Analyzes failed trajectories, proposes skill changes, keeps better variants
- **Litmus**: Multi-agent lab with branch-isolated workers
- **Paper Lantern**: Connects 2M-paper MCP server for experiment-time citation

---

## Source 6: paperclipai/paperclip

**What:** Multi-agent orchestration as "company OS". If Claude Code is an employee, Paperclip is the company. Provides governance: budgets, org charts, approval workflows, goal alignment.

**Key architecture — Stateless Heartbeats:**
1. Agents sleep by default, wake on schedules or events
2. Each beat starts a fresh session with a curated **context packet** (memory + task queue + recent events + config)
3. Agent executes, writes results to external storage, terminates
4. **Atomic task checkout** prevents duplicate work across agents

**Actionable ideas:**
1. **Four Identity Files**: AGENTS.md (core identity), HEARTBEAT.md (execution checklist), SOUL.md (decision style), TOOLS.md (available tools) — Factory could split strategy/current.md similarly
2. **Goal inheritance**: Tasks carry complete ancestry from mission → strategy → experiment. Agents see strategic "why" behind every ticket
3. **Context packet architecture**: Build minimal packets from persistent storage instead of dumping full logs
4. **Budget enforcement**: Per-agent monthly spending caps with atomic checkout
5. **Approval gates**: Human approval for high-cost experiments or destructive operations

---

## Synthesis: Top Priority Ideas for Factory

### Tier 1 — High impact, clearly actionable

| # | Idea | Source | Why |
|---|------|--------|-----|
| 1 | **FEEC priority heuristic** (Fix → Exploit → Explore → Combine) | autoresearch | Strategist currently picks hypotheses ad-hoc; this gives a principled ordering |
| 2 | **Queryable experiment archive** — expose full traces via filesystem, not just metrics | Meta-Harness | 0.1x eval budget with better results; Factory only stores verdict + TSV row |
| 3 | **Evaluator hardening** — variance-aware acceptance, walk-forward validation | awesome-autoresearch | Factory blindly trusts single eval runs; noisy wins cause drift |
| 4 | **Stuck protocol** — detect stalls, trigger radical strategy shifts | autoresearch | No escape hatch when improvement plateaus |
| 5 | **Context packet architecture** — minimal curated context per agent invocation | paperclip | Current SKILL.md dumps everything; agents waste tokens on irrelevant context |

### Tier 2 — Valuable, needs more design

| # | Idea | Source |
|---|------|--------|
| 6 | **Skill version DAG** — track experiment lineage as graph, not list | OpenSpace |
| 7 | **Multi-stage screening** — cheap scout eval before expensive full eval | awesome-autoresearch |
| 8 | **Goal inheritance chain** — experiments trace lineage back to factory.md mission | paperclip |
| 9 | **Token cost tracking** as first-class eval dimension | OpenSpace |
| 10 | **Self-evolution outer loop** — periodically rewrite the factory's own search strategy | Sibyl / autoresearch-autoresearch |

### Anti-patterns to avoid
- **Don't compress diagnostic data too early** — preserve raw traces (Meta-Harness)
- **Don't trust single eval runs** — run multiple times or use variance-aware acceptance (awesome-autoresearch)
- **Don't allow compound changes** — one atomic change per experiment to enable causal attribution (karpathy, Meta-Harness)
