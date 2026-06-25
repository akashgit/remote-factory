---
name: workflow-build
description: "Build a new project from scratch. Runs parallel research, strategy synthesis, implementation, QA verification, and archival. Use when the user says 'build X', 'create X', or the project state is no_repo or incomplete."
disable-model-invocation: true
argument-hint: "<project_path> [idea or spec]"
---

# Build Workflow

The user wants: **$ARGUMENTS**

## Phase 1: Research (Parallel)


Spawn 3 researchers in parallel using a SINGLE Bash tool call with shell `&` + `wait`. Do NOT use `run_in_background: True` or separate Bash calls — all commands must run in one shell:

```bash
factory agent researcher --review-tag similar --task "Similar projects research. Search the web for similar projects, existing solutions, and prior art. Analyze their strengths, weaknesses, and market positioning. Check .factory/archive/ for prior knowledge on similar builds. Write findings to .factory/strategy/research-similar.md covering: similar projects found (with links), what they do well and what's missing, differentiation opportunities. Write output to: .factory/strategy/research-similar.md" --project "$PROJECT_PATH" --timeout 600 &
factory agent researcher --review-tag techstack --task "Tech stack research. Identify the best technology stack for this type of project. Find architecture patterns and best practices. Evaluate framework/library options with trade-offs. Write findings to .factory/strategy/research-techstack.md covering: recommended tech stack with rationale, architecture patterns, framework comparisons. Write output to: .factory/strategy/research-techstack.md" --project "$PROJECT_PATH" --timeout 600 &
factory agent researcher --review-tag pitfalls --task "Pitfalls and scope research. Identify potential pitfalls and common mistakes for this type of project. Research MVP scope best practices. Check .factory/archive/ for lessons from past builds. Write findings to .factory/strategy/research-pitfalls.md covering: potential pitfalls to avoid, MVP scope recommendation, lessons from similar past builds. Write output to: .factory/strategy/research-pitfalls.md" --project "$PROJECT_PATH" --timeout 600 &
wait
echo "All researchers complete"
```

## Barrier: Research


Wait for all parallel agents to complete: `researcher_similar`, `researcher_techstack`, `researcher_pitfalls`

Read combined outputs: `.factory/strategy/research-pitfalls.md`, `.factory/strategy/research-similar.md`, `.factory/strategy/research-techstack.md`

Write combined result to: `.factory/strategy/research-combined.md`

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research-combined.md`
3. Assess: Is the research relevant? Does it cover the technology landscape adequately? Check for gaps in similar projects, tech stack analysis, and pitfall coverage.
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `fork_research` (max 3 iterations)*

## Phase 2: Strategist


```bash
factory agent strategist --task "Synthesize a project specification from research. Read ALL tagged research files at .factory/strategy/research-*.md. Produce a complete phased build plan. Phase 1 must be project scaffold + eval harness. Every Phase must have substantive What/Why/Expected impact fields. Build EVERYTHING in this pass. Only defer items requiring human intervention. Write the plan to .factory/strategy/current.md.
Read: .factory/strategy/research-combined.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Strategy

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/current.md`
3. Assess: HARD GATE — Builder MUST NOT start until approved. Check: 1) Depth: every hypothesis has Category/What/Why/Expected impact. 2) Research grounding: architecture and rationale cite research findings. 3) Buildability: a Builder could implement each phase without clarifying questions. 4) Phase 1 is scaffold + eval harness. 5) Deferred section only contains items requiring human intervention. Write PLAN APPROVED in verdict if all checks pass.
4. Write verdict to `.factory/reviews/ceo-verdict-strategy.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `strategist` (max 3 iterations)*

## Phase 3: Archivist Plan


```bash
factory agent archivist --task "Archive the approved research and strategy.
Read: .factory/strategy/current.md
Write output to: .factory/archive/plan.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

## Phase 4: Builder


```bash
factory agent builder --task "Implement the next phase from .factory/strategy/current.md. Read the CEO's plan approval at .factory/reviews/ceo-verdict-strategist.md. Read CLAUDE.md and factory.md if they exist. Implement exactly what the current phase describes. Run tests. Commit changes and open a draft PR.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Build

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: Read builder output. Check git log and diff. Does the work match the plan for this phase? If the Builder opened a PR, read it. REDIRECT if off-scope or missed key requirements.
4. Write verdict to `.factory/reviews/ceo-verdict-build.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

## Phase 5: Evaluator


```bash
factory agent evaluator --task "Run eval: factory eval $PROJECT_PATH. Capture composite score and per-dimension breakdown. Report delta from baseline. Interpret which dimensions improved/regressed.
Read: .factory/reviews/builder-latest.md
Write output to: .factory/reviews/evaluator-latest.md" --project "$PROJECT_PATH" --timeout 600
```

### Gate — Precheck (Automated)

```bash
factory precheck $PROJECT_PATH --score-before 0 --score-after 0
```

## Phase 6: Archivist Build


```bash
factory agent archivist --task "Archive the build phase results.
Read: .factory/reviews/evaluator-latest.md
Write output to: .factory/archive/build.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*
