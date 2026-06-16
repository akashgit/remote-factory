# Getting Started

This guide follows the lifecycle of a re:factory project — from a one-line idea through autonomous improvement and back to your steering wheel.

## Prerequisites

Make sure you've completed the [Setup](setup.md) steps:

- Python 3.11+
- Claude Code installed and authenticated
- re:factory installed (`factory --help` should work)

## The Lifecycle

Every re:factory project follows the same arc:

```
Idea → Build → Backlog appears → Improve (auto / focus / prompt / issues) → Steer → Loop
```

re:factory handles the transitions automatically. You decide when to intervene.

---

## 1. Start from an Idea

re:factory accepts three entry points depending on how far along your thinking is.

### Build — you know what you want

The simplest path. Describe what you want and re:factory handles everything else:

```bash
factory ceo "Build a CLI that converts CSV to JSON with streaming support"
```

This will:

1. Create a project directory at `~/factory-projects/cli-converts-csv-json/`
2. Initialize a git repo and scaffold the project
3. Save your prompt as the build spec (`.factory/strategy/current.md`)
4. Launch the CEO agent in Build mode

The directory name is auto-derived from your prompt — filler words (verbs, articles, adjectives like "comprehensive" or "simple") are stripped and the result is capped at 4 words. Override with `--dir`:

```bash
factory ceo "Build a CLI that converts CSV to JSON" --dir csv2json
```

Set `FACTORY_PROJECTS_DIR` to change the parent directory.

You can also pass a spec file or a GitHub URL:

```bash
factory ceo ~/ideas/weather-dashboard.md      # longer spec as markdown
factory ceo https://github.com/user/repo      # clone and improve
```

### Design — you have a rough idea

When you want to brainstorm before committing to a design:

```bash
factory ceo "distributed eval runner" --mode design
```

Design mode runs a three-step loop before any code is written:

1. **Research** — the Researcher surveys similar projects, tech stacks, and pitfalls
2. **Distill** — the Strategist synthesizes the research into a structured plan (features, architecture, phased build steps)
3. **Iterate** — the CEO presents the draft to you for feedback. Revise until you approve.

Once you sign off, the spec is persisted and re:factory proceeds to Build mode. Incompatible with `--headless` and `--focus`.

### Research — you have a metric to optimize

For projects where the goal is to improve a measurable metric against a dataset — benchmarks, model tuning, prompt optimization:

```bash
factory ceo "SWE-bench solver agent" --mode research
```

Research ideation works like design mode but the Strategist collects additional configuration:

- **Research Target** — the metric to improve, the command to run evaluation, where results are written
- **Mutable Surfaces** — files the Builder is allowed to modify
- **Fixed Surfaces** — ground truth data and eval infrastructure that must never be touched
- **Research Constraints** — additional rules (e.g., "do not use GPT-4 for cost reasons")

Once you approve the spec, re:factory builds the project and transitions to the research improvement loop. See [Research Mode in Detail](#research-mode-in-detail) below.

---

## 2. The Build Phase

Whichever entry point you chose, Build mode follows the same sequence:

1. The Researcher does a focused research pass ("how do we build this?")
2. The Strategist creates a phased implementation plan
3. The Builder implements each phase, opening PRs along the way
4. An E2E verification gate confirms the project actually runs

When Build completes, the project has code, tests, a `factory.md` configuration, and a discovered eval profile. Items that were deferred during build — performance improvements, edge cases, nice-to-haves — appear in the **backlog**.

---

## 3. The Backlog Appears

After the first build, re:factory creates `.factory/strategy/backlog.md` — a unified work queue that feeds all future improvement. The backlog accumulates items from several sources:

- Features deferred during initial build
- Issues you file on GitHub
- Items the Researcher discovers during observation
- Ideas you add manually with `factory backlog-add`

```bash
factory backlog-list ~/my-project                     # see what's queued
factory backlog-add ~/my-project "add rate limiting"  # add your own item
factory backlog-remove ~/my-project "old item"        # remove a completed item
```

---

## 4. Improve — The Core Loop

Point re:factory at an existing codebase and it runs the improvement cycle:

```bash
factory ceo ~/my-project
```

If the project already has a `.factory/` directory, re:factory resumes where it left off. If not, it runs discovery first — detecting the language, framework, and test setup — then starts improving.

### What happens in a cycle

1. **Observe** — the Researcher analyzes the project and searches for best practices
2. **Hypothesize** — the Strategist generates ranked hypotheses from the backlog using FEEC priority (Fix > Exploit > Explore > Combine)
3. **Build** — the Builder implements one hypothesis on an experiment branch
4. **Guard** — the Reviewer checks for guard violations and code quality
5. **Measure** — the Evaluator scores before and after using the three-tier eval system
6. **Decide** — the CEO runs precheck (non-overridable hard gate) then keeps (score went up) or reverts (score went down)
7. **Record** — the Archivist records the outcome for future learning

Each cycle produces a numbered experiment directory under `.factory/experiments/` with the hypothesis, diffs, eval results, and verdict.

---

## 5. Steering re:factory

re:factory runs autonomously, but you have four ways to steer it:

### `--focus` — build exactly one thing

When you know exactly what you want, `--focus` pins a single backlog item, generates one hypothesis, runs one experiment, and exits:

```bash
factory ceo ~/my-project --focus "add authentication middleware"
factory ceo ~/my-project --focus "fix the CSV export bug"
```

The entire pipeline is scoped to that single target — the Researcher focuses its research, the Strategist generates exactly one hypothesis, and after the keep/revert decision the cycle ends. Mutually exclusive with `--loop`.

### `--prompt` — give general direction

Nudge the Strategist's hypothesis generation without pinning a specific item:

```bash
factory ceo ~/my-project --prompt "focus on performance improvements"
```

### GitHub Issues — async steering

File issues on the project's GitHub repo. The Strategist reads open issues and factors them into hypothesis ranking:

```bash
gh issue create --title "Add WebSocket support" --body "Need real-time updates for the dashboard"
```

### `backlog-add` — queue an item

Add items directly to the backlog for the next cycle to pick up:

```bash
factory backlog-add ~/my-project "add structured logging"
```

### `--refine` — targeted post-cycle changes

After a cycle completes (or anytime on an existing project), request a single refinement:

```bash
factory ceo ~/my-project --refine "add rate limiting to the API"
```

This routes through the **Refiner** agent (scopes the change) → **Builder** (implements) → full review pipeline → keep/revert verdict. Mutually exclusive with `--mode`, `--prompt`, and `--focus`.

### `--clean-pr` — strip artifacts for upstream

When contributing factory-managed code to an external repository, use `--clean-pr` to strip eval scripts, benchmarks, `.factory/` data, and eval test files from the PR before pushing. The welcome wizard auto-suggests this flag when the input is a GitHub URL.

```bash
factory ceo https://github.com/user/repo --clean-pr
factory clean-pr ~/my-project --exp 3
```

### Post-cycle refinement loop

In foreground mode, the CEO doesn't exit after a cycle — it stays active and waits for follow-up requests. Just type what you want changed:

> "Fix the typo in the header"
> "Add error handling to the upload endpoint"

Each request is a tracked experiment with its own PR, review pipeline, and keep/revert verdict. There's no cap on refinements — advisory warnings appear at 5 and 10 to flag context growth, but you decide when to stop. Say "done" or "looks good" to exit.

---

## 6. Continuous Loop

For unattended operation, wrap the CEO in a heartbeat loop:

```bash
factory run ~/my-project --loop                    # every 30 min (default)
factory run ~/my-project --loop --interval 900     # every 15 min
factory run ~/my-project --loop --max-cycles 5     # stop after 5 cycles
```

For long-running sessions, use tmux:

```bash
factory tmux ~/my-project --loop              # launches in a detached tmux session
factory tmux-ls                               # list active factory sessions
factory tmux-stop --path ~/my-project         # stop a session
```

### Interactive vs headless

By default, `factory ceo` launches an interactive Claude Code session — you can see what the agents are doing and intervene if needed:

```bash
factory ceo ~/my-project              # interactive (default)
factory ceo ~/my-project --headless   # pipe mode, no interaction
```

---

## Research Mode in Detail

Research mode replaces the standard Improve loop with a specialized cycle designed for metric optimization against a dataset. It adds the Failure Analyst agent, leakage guards, and monotonic improvement enforcement.

### When to use it

Use research mode when your project has a measurable target metric and a reproducible evaluation command — benchmarks (SWE-bench, HumanEval), model accuracy, prompt optimization, CAD query systems, mathematical reasoning.

### Configuring a research project

The research target is configured in `factory.md`:

```markdown
## Research Target
- objective: maximize SWE-bench resolve rate
- metric: resolved/total
- target: 0.35
- run_command: python run_benchmark.py
- result_path: results/output.json
- timeout: 3600

## Mutable Surfaces
- src/agent.py
- src/localization.py
- prompts/*.md

## Fixed Surfaces
- eval/
- data/ground_truth.json
- tests/

## Inner Loop
- runs_per_cycle: 5
- aggregate: mean
- plateau_threshold: 3
- max_inner_runs_per_cycle: 10

## Outer Loop Surfaces
- max_outer_cycles: 5
- inner: prompts/*.md
- outer: src/**/*.py
```

**Mutable surfaces** are files the Builder can change. **Fixed surfaces** are ground truth data and eval infrastructure that must never be modified. Fixed surfaces are fingerprinted for leakage detection.

**Inner Loop** is for stochastic harnesses — it runs the eval N times per cycle, aggregates via `mean`/`median`/`max`/`all_pass`, and detects plateau after N consecutive non-improving cycles. **Outer Loop Surfaces** defines narrow (inner) and wide (outer) scopes — when the inner loop plateaus, re:factory expands mutable surfaces to include outer surfaces for architectural changes. Both sections are optional and independent.

### The research cycle

Research mode follows seven phases:

| Phase | Agent | What happens |
|-------|-------|-------------|
| **R0 — Baseline** | Evaluator | Run `run_command`, record starting metric |
| **R1 — Failure Analysis** | Failure Analyst | Classify failures by root cause, aggregate into categories, suggest interventions |
| **R1.5 — Research** | Researcher | Search web for targeted solutions to dominant failure patterns |
| **R2 — Strategy** | Strategist | Generate 1–3 hypotheses targeting dominant failure modes |
| **R3 — Build** | Builder | Implement hypothesis, modifying only mutable surfaces |
| **R4 — Run** | Evaluator | Re-run `run_command`, extract new metric |
| **R5 — Verdict** | CEO | Keep if metric improved monotonically; revert otherwise |

### Cycle progression example

A SWE-bench solver agent improving over five cycles:

| Cycle | Metric | Failure Mode Targeted | Verdict | Cumulative |
|-------|--------|----------------------|---------|------------|
| 000 | 0.18 | — (baseline) | — | 0.18 |
| 001 | 0.22 | FILE_NOT_FOUND — agent searched wrong directories | KEEP | 0.22 |
| 002 | 0.24 | SYNTAX_ERROR — generated patches had indentation bugs | KEEP | 0.24 |
| 003 | 0.21 | TIMEOUT — overly broad search strategy | REVERT | 0.24 |
| 004 | 0.27 | INCOMPLETE_EDIT — partial file modifications | KEEP | 0.27 |
| 005 | 0.30 | WRONG_FILE — localization errors | KEEP | 0.30 |

Cycle 003 regressed below the previous best (0.24), so it was automatically reverted. The metric ratchets forward — it can never go below the previous best.

### Leakage guards

Research mode enforces three layers of ground truth protection:

1. **Token overlap** — fingerprints fixed surface files and checks hypothesis/diff text for suspicious token overlap using Jaccard similarity
2. **Negation hints** — detects patterns like "do NOT use subtraction" that encode ground truth by exclusion
3. **Specific values** — extracts numeric literals and quoted strings from fixed surfaces, flags if they appear in hypothesis text

Leakage checks run at three hard gates: Strategy review, Builder review, and Precheck. A medium or high leakage risk triggers an automatic redirect or revert.

### Running research mode

```bash
# New research project (ideation → build → research loop)
factory ceo "SWE-bench solver agent" --mode research

# Existing research project (skip ideation, run research loop)
factory ceo ~/my-swe-bench-solver --mode research

# Focus on a specific hypothesis within research mode
factory ceo ~/my-swe-bench-solver --mode research --focus "try chain-of-thought prompting"

# Continuous research loop
factory run ~/my-swe-bench-solver --mode research --loop
```

### Named use cases

| Project | Metric | Mutable Surfaces | What improves |
|---------|--------|-----------------|---------------|
| **SWE-bench solver** | resolve rate | agent logic, prompts, localization | Patch generation accuracy |
| **Mathematical reasoning** | solve rate | chain-of-thought templates, tool calls | Proof strategy selection |
| **CAD query optimization** | query accuracy | query builder, schema mapping | Entity resolution, join logic |

---

## Writing a `factory.md`

Once the CEO creates your project, it auto-generates a `factory.md` configuration file. You can also write one manually for more control:

```markdown
## Goal
A CLI tool that converts CSV files to JSON with streaming support.

## Scope
### Modifiable
- src/**
- tests/**

## Guards
- Do not delete existing tests
- Do not modify files outside scope

## Eval
### Command
pytest --tb=short -q

### Threshold
0.8
```

See the [Configuration Reference](configuration.md) for all available sections.

## Next Steps

- [Configuration Reference](configuration.md) — all `factory.md` options
- [Architecture](architecture.md) — how the CEO and specialist agents work
- [Eval System](eval.md) — how projects are scored
- [Self-Improvement Loop](self-improvement.md) — how agents evolve over time
