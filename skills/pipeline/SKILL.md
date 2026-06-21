---
name: pipeline
description: "Design and execute a custom multi-agent pipeline for any goal. Analyzes the goal, selects appropriate specialist agents, designs a DAG of steps with dependencies, and executes them via factory CLI with gate decisions between steps. Use when the user says 'run a pipeline for X', 'orchestrate X', or wants a custom multi-agent workflow."
disable-model-invocation: true
argument-hint: "<goal>"
---

# Pipeline — Dynamic Multi-Agent Orchestrator

You design and execute custom multi-agent pipelines to accomplish the user's goal.

The user wants: **$ARGUMENTS**

## Prerequisites

The `factory` CLI must be installed:

```bash
command -v factory >/dev/null 2>&1 || uv tool install "${CLAUDE_PLUGIN_ROOT}"
mkdir -p .factory/pipeline
```

## Your Agents

Spawn specialists via the CLI. Each agent gets a fresh context window.

```bash
factory agent <role> --task "<task description>" --project "$(pwd)" [--timeout N]
```

| Role | Purpose |
|------|---------|
| researcher | Web research, codebase analysis, domain studies |
| strategist | Generate prioritized hypotheses from observations |
| builder | Implement code changes on a feature branch, open PRs |
| qa | Health checks, code review, adversarial QA verification |
| archivist | Record findings to `.factory/archive/` |
| distiller | Refine vague ideas into buildable specs |

### Invocation Rules

Each `factory agent` call is synchronous and blocking — it returns only when the agent finishes. Do not shell-background (`&`) individual commands.

To run steps in parallel, issue multiple `factory agent` commands as **separate bash tool calls in the same message turn**. Claude Code executes them concurrently. This is parallel tool calls, not shell backgrounding.

The runner captures agent stdout to `.factory/reviews/<role>-latest.md`.

## Phase 1: Design the Pipeline

1. **Understand the goal** — what outcome is desired? Which agents are needed?
2. **Inspect project state:**
   ```bash
   factory detect "$(pwd)"
   cat .factory/config.json 2>/dev/null
   ```
3. **Write the pipeline plan** to `.factory/pipeline/plan.md`:

```markdown
## Pipeline: <goal summary>

### Steps

| Step | Role | Task Summary | Depends On |
|------|------|-------------|-----------|
| S1 | researcher | ... | - |
| S2 | qa | ... | - |
| S3 | strategist | ... | S1, S2 |
| ... | ... | ... | ... |

### Gate Rules
- After S1: PROCEED if ...; REDIRECT if ...
- After S3: PROCEED if ...; ABORT if ...
```

### Design Principles

- **Minimize invocations** — only agents needed for this goal
- **Maximize parallelism** — steps whose dependencies are all satisfied and that don't depend on each other can be issued as parallel tool calls
- **Mandatory archival** — always include at least one archivist step at the end
- **Gate rules** — define PROCEED/REDIRECT/ABORT criteria for critical transitions

## Phase 2: Execute the Pipeline

Process steps in topological order:

1. **Identify next batch** — steps whose dependencies are all complete
2. **Build task strings** — incorporate output from prior steps by reading `.factory/reviews/<role>-latest.md`
3. **Invoke agents** — single or parallel batch
4. **Read output** — `cat .factory/reviews/<role>-latest.md`
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
