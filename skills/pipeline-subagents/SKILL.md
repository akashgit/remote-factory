---
name: pipeline-subagents
description: "Design and execute a custom multi-agent pipeline using Claude Code subagents directly. Spawns factory-researcher, factory-builder, etc. via the Agent tool with native parallel and background execution. Use when the user says 'run a pipeline for X' and factory subagents are available."
argument-hint: "<goal>"
---

# Pipeline (Subagents) — Dynamic Multi-Agent Orchestrator

You design and execute custom multi-agent pipelines using Claude Code's native Agent tool to spawn factory subagents directly.

The user wants: **$ARGUMENTS**

## Your Agents

Spawn specialists using the **Agent tool**:

```
Agent({
  description: "<short description>",
  prompt: "<detailed task>",
  subagent_type: "factory-<role>"
})
```

| Subagent Type | Purpose |
|---------------|---------|
| factory-researcher | Web research, codebase analysis, domain studies |
| factory-strategist | Generate prioritized hypotheses from observations |
| factory-builder | Implement code changes on a feature branch, open PRs |
| factory-reviewer | Review PRs, guard checks, keep/revert verdicts |
| factory-evaluator | Run evals, compare before/after scores |
| factory-archivist | Record findings to `.factory/archive/` |
| factory-distiller | Refine vague ideas into buildable specs |
| factory-failure_analyst | Classify experiment failures by root cause |

### Parallel Execution

Issue multiple Agent tool calls in the **same message** — they run concurrently:

```
Agent({ subagent_type: "factory-researcher", prompt: "Research the auth bug..." })
Agent({ subagent_type: "factory-evaluator", prompt: "Run baseline eval..." })
```

### Background Execution

For non-blocking steps (e.g., archival):

```
Agent({ subagent_type: "factory-archivist", prompt: "Archive findings...", run_in_background: true })
```

## Phase 1: Design the Pipeline

1. **Understand the goal** — what outcome is desired? Which agents are needed?
2. **Inspect project state** (use Bash tool):
   ```bash
   ls .factory/config.json 2>/dev/null && cat .factory/config.json
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
- **Maximize parallelism** — steps with shared dependencies and no mutual dependency → spawn in same message
- **Mandatory archival** — always include at least one archivist step at the end
- **Gate rules** — define PROCEED/REDIRECT/ABORT criteria for critical transitions

## Phase 2: Execute the Pipeline

Process steps in topological order:

1. **Identify next batch** — steps whose dependencies are all complete
2. **Build prompts** — incorporate output from prior steps (agent results are returned directly)
3. **Invoke agents:**
   - Single step: one Agent call
   - Parallel batch: multiple Agent calls in same message
   - Archival: can use `run_in_background: true`
4. **Read results** — Agent tool returns subagent output directly
5. **Apply gate rule:**
   - **PROCEED**: Move to next step
   - **REDIRECT**: Re-invoke with corrections (max 2 per step)
   - **ABORT**: Skip downstream steps, jump to summary
6. **Repeat** until done

### Error Recovery

- Agent error: retry once with simpler prompt
- 2 consecutive failures: ABORT pipeline

### Final Summary

Write `.factory/pipeline/summary.md` with goal, status, step results, and key findings.
