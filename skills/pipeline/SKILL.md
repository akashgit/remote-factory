---
name: pipeline
description: "Design and execute a custom multi-agent pipeline for any goal. Analyzes the goal, selects appropriate specialist agents, designs a DAG of steps with dependencies, and executes them via factory CLI with gate decisions between steps. Use when the user says 'run a pipeline for X', 'orchestrate X', or wants a custom multi-agent workflow."
argument-hint: "<goal>"
---

# Pipeline — Dynamic Multi-Agent Orchestrator

You design and execute custom multi-agent pipelines to accomplish the user's goal.

The user wants: **$ARGUMENTS**

## Prerequisites

The `factory` CLI must be installed:

```bash
command -v factory >/dev/null 2>&1 || uv tool install "${CLAUDE_PLUGIN_ROOT}"
```

Set `PROJECT_PATH` to the current working directory.

## Your Agents

Spawn specialists via the CLI. Each agent gets a fresh context window.

```bash
factory agent <role> --task "<task description>" --project "$PROJECT_PATH" [--timeout N]
```

| Role | Purpose | Default Timeout |
|------|---------|-----------------|
| researcher | Web research, codebase analysis, domain studies | 300s |
| strategist | Generate prioritized hypotheses from observations | 300s |
| builder | Implement code changes on a feature branch, open PRs | 600s |
| reviewer | Review PRs, guard checks, keep/revert verdicts | 300s |
| evaluator | Run evals, compare before/after scores | 300s |
| archivist | Record findings to `.factory/archive/` | 300s |
| distiller | Refine vague ideas into buildable specs | 300s |
| failure_analyst | Classify experiment failures by root cause | 300s |

### Invocation Rules

All invocations MUST be synchronous — no `&`, no `run_in_background`.

The runner captures stdout to `.factory/reviews/<role>-latest.md` and returns only when the agent finishes.

**For parallel steps:** Issue multiple `factory agent` commands as separate bash tool calls in the same message turn. They execute concurrently.

## Phase 1: Design the Pipeline

1. **Understand the goal** — what outcome is desired? Which agents are needed?
2. **Inspect project state:**
   ```bash
   factory detect "$PROJECT_PATH"
   cat "$PROJECT_PATH/.factory/config.json" 2>/dev/null
   ```
3. **Write the pipeline plan** to `.factory/pipeline/plan.md`:

```markdown
## Pipeline: <goal summary>

### Steps

| Step | Role | Task Summary | Depends On | Timeout |
|------|------|-------------|-----------|---------|
| S1 | researcher | ... | - | 300 |
| S2 | evaluator | ... | - | 300 |
| S3 | strategist | ... | S1, S2 | 300 |
| ... | ... | ... | ... | ... |

### Gate Rules
- After S1: PROCEED if ...; REDIRECT if ...
- After S3: PROCEED if ...; ABORT if ...
```

### Design Principles

- **Minimize invocations** — only agents needed for this goal
- **Maximize parallelism** — steps with shared dependencies and no mutual dependency run together
- **Mandatory archival** — always include at least one archivist step at the end
- **Gate rules** — define PROCEED/REDIRECT/ABORT criteria for critical transitions

## Phase 2: Execute the Pipeline

Process steps in topological order:

1. **Identify next batch** — steps whose dependencies are all complete
2. **Build task strings** — incorporate output from prior steps by reading `.factory/reviews/<role>-latest.md`
3. **Invoke agents** — single or parallel batch
4. **Read output** — `cat "$PROJECT_PATH/.factory/reviews/<role>-latest.md"`
5. **Apply gate rule:**
   - **PROCEED**: Move to next step
   - **REDIRECT**: Re-invoke with corrections (max 2 per step)
   - **ABORT**: Skip downstream steps, jump to summary
6. **Repeat** until done

### Error Recovery

- Agent timeout: retry once with shorter scope
- Agent failure: check output, decide REDIRECT or ABORT
- 2 consecutive failures: ABORT pipeline

### Final Summary

Write `.factory/pipeline/summary.md` with goal, status, step results, and key findings.
