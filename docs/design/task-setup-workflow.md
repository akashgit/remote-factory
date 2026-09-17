# ADR: task-setup Workflow — Director Topology and Prompt Design

*Status: Proposed — under discussion in PR #1509*

---

## Context

Creating a new `Task` (the 4-hook interface: `instances()`, `setup()`, `prompt()`, `verify()`) is the entry point for applying the outer loop to a new domain. Without a good Task, the outer loop has no signal to learn from.

The hard part is not code generation. It is `VerifyResult.details`.

`VerifyResult.details` is a dict returned by `verify()` on every evaluation. The outer loop's reflection mechanism reads these dicts across the population to extract patterns like:

> "High-scoring individuals had low blunder_count while low-scoring ones had high blunder_count"

If `details` is sparse (`{"passed": True, "score": 0.7}`), the reflector has nothing to compare. Evolution stalls — mutation suggestions are generic and untargeted. The outer loop can only improve what it can observe.

This creates a design problem: a developer building a new Task is focused on *correctness* (does verify() return the right score?), not on *observability* (does verify() expose enough signal for evolution to improve?). These are different concerns and without explicit guidance, observability is consistently neglected.

A second problem: Task design involves domain research that developers cannot be expected to carry in their heads. What are the right instances? What granularity of `details` enables meaningful comparison? What verification strategy (exit_code vs json) fits the domain? These questions have non-obvious answers that benefit from structured research before any code is written.

---

## Decision

Use a **director topology** — CEO agents that spawn sub-agents dynamically based on domain complexity — rather than a fixed pipeline of specialist agents.

### Topology (proposed two-phase split)

**Phase 1 — Core scaffold:**
```
research_director → gate_research → strategy_director → gate_strategy (user) → builder → archivist
```

**Phase 2 — Quality layer:**
```
... → builder → qa_director → gate_qa → validate_task (GateNode) → archivist
```

### Why directors instead of a fixed pipeline

A fixed pipeline (researcher_1 → researcher_2 → researcher_3 → synthesizer) works when the number of research dimensions is known in advance. It doesn't work for Task design because:

- A chess task needs a tooling researcher (python-chess, Stockfish setup) that a code-generation task doesn't
- A drug discovery task needs a dataset researcher (PDBbind, existing benchmarks) that a sorting task doesn't
- Adding fixed nodes for every possible research dimension produces a bloated pipeline where most nodes are no-ops for most domains

Directors adapt. The Research Director reads `user-intent.md` and decides which research dimensions are needed. Mandatory dimensions (domain, verification, outer loop feedback) are always spawned. Optional dimensions (tooling, dataset) are spawned when the domain warrants it.

### The mandatory outer loop feedback researcher

Every task-setup run must include a researcher specifically focused on what `VerifyResult.details` should contain for effective outer loop evolution. This is non-negotiable even if the developer thinks it's unnecessary.

**Why mandatory:** The failure mode is silent. A Task with sparse `details` passes all functional tests — it produces correct scores, verify() works, the CLI validates it. But the outer loop's reflection mechanism learns nothing from it. The first sign of the problem is that generations plateau immediately with no improvement signal. By then the Task is deployed and the cost of retrofitting richer details is high.

Making this researcher mandatory creates an explicit forcing function: you must think about observability before implementation.

### Prompt design principles

**1. Generic framing for reflection mechanisms**

Prompts must not hardcode references to the current reflector algorithm. Write:

> "rich domain-specific dimensions enable any algorithm to learn what distinguishes good solutions from bad ones"

Not:

> "contrastive analysis comparing top-K vs bottom-K"

Reason: the reflector algorithm evolves. Prompts that describe it in terms of specific implementation details (`top-K`, `contrastive`, `bottom-K`) will drift and mislead future builders. The invariant — rich details enable better learning — is stable.

**2. Show, don't tell — the chess-evolve example**

Every prompt that explains `VerifyResult.details` design must include the concrete chess example:

```python
# Good — rich, numeric, comparable
details = {
    "wins": 3, "draws": 1, "losses": 1,
    "blunder_count": 4,
    "avg_eval": -0.3,
    "total_moves": 87,
    "game_results": [{"game": 1, "result": "win", "moves": 18}, ...],
}

# Anti-example — sparse, unlearnable
details = {"passed": True, "score": 0.6}
```

Abstract descriptions of what "good details" look like are insufficient. Developers need a working model.

**3. At least 4 numeric keys**

The reflection mechanism computes differences between good and bad solutions. It needs numeric dimensions to compare. A minimum of 4 numeric keys in `details` is a concrete, checkable criterion. Boolean and string fields are less useful for numeric comparison but are acceptable for error categorization.

**4. Per-instance granularity**

`details` should include per-instance breakdowns (a list of dicts) in addition to aggregate statistics. This enables the reflector to identify which specific instances are hard vs easy, and which mutation patterns improve performance on hard instances specifically.

**5. Categorical error classification**

Include an `error_type` or `failure_category` field that distinguishes types of failures. "Failed on 3 instances" is less useful than "failed on 3 instances: 2 timeout, 1 wrong_output". The reflector can then suggest mutations that address specific failure modes.

### validate_task must be a GateNode, not an FnNode

`factory task validate` is a correctness check, not a terminal step. When validation fails, the builder should fix the issues and re-validate — not restart the entire 9-node workflow from scratch.

**Use:**
```python
GateNode(
    id="validate_task",
    evaluator_type="fn",
    evaluator_command="factory task validate --name $(cat {project_path}/.factory/generated-task-name.txt) --project {project_path}",
    reads={".factory/generated-task-name.txt"},
)
# With edges:
Edge(source="validate_task", target="builder", condition=VerdictType.RELOOP),
Edge(source="validate_task", target="archivist", condition=VerdictType.PROCEED),
```

**Not:**
```python
FnNode(id="validate_task", command="factory task validate ...")
# No recovery path — halt on failure forces full workflow restart
```

### Shell command quoting for project paths

Any FnNode or GateNode command that embeds `{project_path}` must handle paths with spaces. The executor substitutes via `shlex.quote(str(self.project_path))`, which on a path like `/Users/John Smith/project` produces `'/Users/John Smith/project'` — closing the outer `bash -c '...'` string prematurely.

**Safe pattern — use double-quote outer string:**
```python
command='bash -c "factory task validate --project {project_path}"'
```

Or pass as environment variable:
```python
command="PROJECT={project_path} bash -c 'factory task validate --project \"$PROJECT\"'"
```

---

## Alternatives considered

**Fixed specialist pipeline**
A fixed sequence (domain-researcher → verification-researcher → outer-loop-researcher → synthesizer → builder) is simpler to implement and test. Rejected because it doesn't adapt to domain complexity — a drug discovery Task needs 2–3 additional research dimensions a sorting Task doesn't. Fixed pipelines produce either bloat (always-no-op nodes) or inadequate coverage (missing dimensions for complex domains).

**Single-agent workflow**
One CEO agent that does all research, strategy, and implementation. Simpler topology, fewer moving parts. Rejected because the Tasks this workflow produces will be evaluated by the outer loop many times. The investment in structured research and adversarial QA pays off quickly when a poor `VerifyResult.details` design costs 50+ outer loop evaluations to diagnose.

**Prompt-heavy single builder**
All domain knowledge in one very detailed builder prompt. Rejected because prompts that try to teach domain research, verification strategy, AND details design simultaneously are consistently ignored in practice — the builder focuses on functional correctness and neglects observability.

---

## Consequences

- Longer workflow runs (20–40 minutes for complex domains vs 5–10 for a direct build)
- Higher cost per run (3+ researcher agents + strategy + QA)
- Significantly better Task quality: richer `VerifyResult.details`, validated before deployment
- The mandatory outer loop feedback researcher creates a cultural expectation that observability is a first-class concern in Task design

The investment makes sense for Tasks that will be used in multi-generation outer loop runs. For one-off Tasks or exploratory experiments, a direct build may be more appropriate.
