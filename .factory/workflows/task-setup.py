"""task-setup: Scaffold a new Task class through interactive research and generation.

v2 director topology — 9 nodes, 11 edges. Directors (CEO agents) dynamically
spawn sub-agents to adapt research depth to domain complexity. Mandatory outer
loop feedback researcher and evolution-readiness QA ensure every generated Task
has rich VerifyResult.details for the factory's reflection and improvement
mechanisms.
"""

from __future__ import annotations

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

# ── Module-level metadata (required for WorkflowRegistry discovery) ──

meta = {
    "name": "task-setup",
    "description": (
        "Scaffold a new Task class (instances, setup, prompt, verify) through "
        "interactive research, strategy synthesis, and generation with rich "
        "VerifyResult.details for outer loop evolution"
    ),
}

# ── Prompt constants ────────────────────────────────────────────

_RESEARCH_DIRECTOR_PROMPT = """\
You are the Research Director for the task-setup workflow. Your job is to
decide what research dimensions are needed for this domain, then spawn
researcher agents to investigate each dimension.

## Step 1: Read the Domain Focus

Read .factory/strategy/user-intent.md to understand the user's focus domain
(e.g., "chess engine evaluation against Stockfish", "drug candidate binding
affinity scoring", "optimize sorting algorithm for speed").

## Step 2: Decide Research Dimensions

You MUST always spawn at least these 3 research dimensions:

1. **Domain researcher** — What to measure, what instances to evaluate,
   what setup is needed, what prompts should say
2. **Verification researcher** — How to verify results, scoring method
   (exit_code vs json), TOML vs Python decision, timeout and constraints
3. **Outer loop feedback researcher** — What VerifyResult.details dimensions
   to expose for the factory's reflection and improvement mechanisms.
   This is CRITICAL: details is the learning signal for the factory's
   reflection and improvement mechanisms — rich domain-specific dimensions
   enable any algorithm to learn what distinguishes good solutions from bad
   ones. If details is empty or has only "passed"/"score", no reflection
   mechanism can learn what mutations improve fitness.

For complex domains, you MAY add additional dimensions:
- **Tooling/dependencies researcher** — When the domain needs specific
  libraries, external tools, or complex environment setup (e.g., drug
  discovery needs RDKit, docking software; chess needs python-chess + Stockfish)
- **Dataset/benchmark researcher** — When the domain has established
  benchmarks or datasets to align with (e.g., SWE-bench instances, PDBbind
  datasets, standard chess test suites)

## Step 3: Spawn Researchers

For each research dimension, spawn a researcher agent using this command:

```bash
factory agent researcher --task "<TASK_DESCRIPTION>" --project $PROJECT_PATH
```

Where <TASK_DESCRIPTION> is a detailed prompt for that specific dimension.

### Researcher 1: Domain Research

Spawn with a task like:
```bash
factory agent researcher --task "Research the domain for a new Task definition.

Read .factory/strategy/user-intent.md for the focus domain.

Investigate:
1. What are the instances? What distinct items/cases/problems should this task
   iterate over? (e.g., chess positions, code repos, molecules, test cases)
2. What does each instance look like? What metadata does each instance need?
   (e.g., FEN strings, problem statements, input parameters)
3. How are instances discovered? Directory scan, hardcoded list, API query,
   combinatorial generation?
4. What does setup() need to do? What environment preparation is needed per
   instance? (e.g., pip install, write seed files, clone repos)
5. What does prompt() need to say? What instructions should the agent receive
   for each instance?

Write findings to .factory/strategy/research-domain.md with sections:
- Domain Summary (1-2 paragraphs)
- Measurement Dimensions (primary metric + secondary metrics)
- Proposed Instances (table: id, metadata fields, description — at least 3)
- Instance Details (per-instance breakdown)
- Existing Resources (benchmarks, datasets, frameworks)
- Setup Requirements
- Prompt Strategy (static vs dynamic)

Reference patterns from factory/task.py, examples/chess_evolve_task.py,
and examples/swe_bench_task.py." --project $PROJECT_PATH
```

### Researcher 2: Verification Research

Spawn with a task like:
```bash
factory agent researcher --task "Research verification approaches for a new Task.

Read .factory/strategy/user-intent.md for the focus domain.

Investigate:
1. How is success measured? Binary pass/fail, continuous score (0.0-1.0), or both?
2. What scoring method? 'exit_code' (shell command, exit 0 = pass) or 'json'
   (structured output with metric_path)?
3. What verify command or logic? Shell command (pytest, diff) or in-Python
   evaluation (self-play, comparison, statistical test)?
4. What is the metric_path? If JSON scoring, what key holds the score?
5. What timeout? How long should verification run per instance?
6. TOML or Python? Can verify be a single shell command (TOML), or does it
   need custom Python logic (Python subclass)?

Write findings to .factory/strategy/research-verification.md with sections:
- Verification Strategy (1 paragraph)
- Scoring Decision (method, metric_path, rationale)
- TOML vs Python Decision (recommendation + rationale)
- Verification Implementation (exact command or pseudocode)
- Constraints (timeout, max_retries, required_capabilities)
- VerifyResult Shape (passed, score, details fields)

Reference ScoringContract (method: 'exit_code'|'json', metric_path: str)
and VerifyResult (passed: bool, score: float, details: dict) from factory/task.py." --project $PROJECT_PATH
```

### Researcher 3: Outer Loop Feedback Research (MANDATORY)

This researcher is ALWAYS required. Spawn with:
```bash
factory agent researcher --task "Research what VerifyResult.details should contain
for optimal outer loop evolution.

Read .factory/strategy/user-intent.md for the focus domain.

CONTEXT: VerifyResult.details is the learning signal for the factory's
reflection and improvement mechanisms — rich domain-specific dimensions
enable any algorithm to learn what distinguishes good solutions from bad
ones. The reflection mechanism examines VerifyResult.details dicts to
extract patterns such as:
- 'Low-scoring individuals had high error_count while high-scoring ones had low error_count'
- 'Individual abc123 failed 3/5 verify checks'
- 'Individual def456 had blunder_count=12 while winners had blunder_count=2'

These patterns drive improvement suggestions for the next generation. If
details is sparse (just {passed, score}), the reflection mechanism has
nothing to compare and evolution stalls.

INVESTIGATE:
1. What per-instance dimensions enable meaningful comparison between good
   and bad solutions? Think about: what numeric metrics differ between good
   and bad solutions?
   Examples from chess-evolve:
   - wins, draws, losses (game outcomes)
   - blunder_count (quality of moves)
   - avg_eval (position evaluation average)
   - total_moves (game length signal)
   - game_results (per-game breakdown list)

2. What granularity is needed? Should details include:
   - Per-instance breakdowns (list of dicts)?
   - Aggregate statistics (counts, averages)?
   - Error categorization (what kinds of failures)?
   - Timing information (execution duration)?

3. What keys should the reflection mechanism compare between good and bad
   solutions? The most useful details keys are NUMERIC (averages, counts,
   rates) because the mechanism can compute differences. Boolean and string
   fields are less useful for numeric comparison but good for error
   categorization.

4. What details enable the reflection mechanism to suggest specific
   improvements? Rich details help it say 'focus on reducing blunder_count'
   instead of just 'improve score'.

Write findings to .factory/strategy/research-outer-loop.md with sections:
- Outer Loop Context (how VerifyResult.details feeds the reflection mechanism)
- Recommended Details Keys (table: key, type, what it measures, learning value)
- Per-Instance Granularity (what per-instance data to include)
- Evolution Signal Quality (how these keys enable better improvements)
- Example VerifyResult (complete Python dict showing all recommended fields)

Reference factory/outer_loop/reflector.py and factory/outer_loop/verify_adapter.py
for how details flows through the pipeline." --project $PROJECT_PATH
```

### Additional Researchers (for complex domains only)

If the domain requires specialized tooling, dependencies, or has established
benchmarks, spawn additional researchers:

```bash
factory agent researcher --task "<specific research task>" --project $PROJECT_PATH
```

Write any additional findings to .factory/strategy/research-<dimension>.md.

## Step 4: Verify Completeness

After all researchers complete, verify that:
1. .factory/strategy/research-domain.md exists and has concrete instances (≥3)
2. .factory/strategy/research-verification.md exists and has a scoring decision
3. .factory/strategy/research-outer-loop.md exists and has recommended details keys
4. All research is consistent (no contradictions between files)

If any research is inadequate, re-run that specific researcher with more
detailed instructions.

## Output

Your output is the set of research files. Do not write a summary —
the gate_research node will evaluate the research quality.
"""

_GATE_RESEARCH_PROMPT = """\
Evaluate the quality of all research outputs for task setup.

Read the research files:
- .factory/strategy/research-domain.md (domain analysis)
- .factory/strategy/research-verification.md (verification strategy)
- .factory/strategy/research-outer-loop.md (outer loop feedback design)

Check each criterion:

1. **All three core research files exist and are substantive** (not stubs or TODOs)

2. **Domain research identifies concrete instances** — at least 3 instances with
   id, metadata fields, and descriptions. Not vague 'we could test X' — actual
   instance specifications with specific metadata dicts.

3. **Verification research identifies a scoring approach** — either 'exit_code'
   or 'json' with a clear rationale. A concrete TOML vs Python recommendation
   with justification. Not 'it depends' — a definitive recommendation.

4. **Outer loop feedback research specifies VerifyResult.details keys** — at
   least 4 numeric/categorical keys that enable meaningful comparison between
   good and bad solutions. The keys must go beyond just {passed, score} — they
   need to include domain-specific dimensions that the reflection mechanism can
   compare across individuals. Examples of good keys: win_rate, blunder_count,
   avg_eval, execution_time, error_count, coverage_pct.

5. **Measurement is defined** — the domain research specifies a primary metric
   and how it maps to VerifyResult(passed, score).

6. **Cross-research consistency** — the verification approach aligns with the
   domain instances, and the outer loop details keys align with the verification
   outputs. No contradictions.

7. **Coverage is sufficient** — a Strategy Director could synthesize these files
   into a complete task specification without needing additional research.

PROCEED if all criteria are met.
RELOOP if any criterion is missing — specify which ones and what needs
investigation. Be specific about what's inadequate.
HALT only if the focus domain is fundamentally unsuitable for a Task
(e.g., requires human judgment with no programmatic proxy).
"""

_STRATEGY_DIRECTOR_PROMPT = """\
You are the Strategy Director for the task-setup workflow. Your job is to
synthesize the research findings into a concrete, buildable Task specification
by spawning strategist agents with different perspectives.

## Input Files

Read these research outputs:
- .factory/strategy/user-intent.md — the user's focus domain
- .factory/strategy/research-domain.md — domain analysis, instances, metrics
- .factory/strategy/research-verification.md — verification strategy, scoring
- .factory/strategy/research-outer-loop.md — VerifyResult.details design for evolution

## Strategy Perspectives

You MUST spawn at least these 2 strategy perspectives:

### Perspective 1: Architecture Strategy

Spawn a strategist to design the Task class structure:
```bash
factory agent strategist --task "Design the Task class architecture for a new task.

Read:
- .factory/strategy/user-intent.md (focus domain)
- .factory/strategy/research-domain.md (domain analysis)
- .factory/strategy/research-verification.md (verification strategy)

Produce an architecture strategy covering:
1. Task format decision: TOML or Python, with rationale
2. Task name (kebab-case) and class name (PascalCase + 'Task')
3. instances() implementation: how instances are discovered, metadata schema
4. setup() implementation: environment preparation per instance
5. prompt() implementation: static vs dynamic, template design
6. verify() implementation: verification logic, scoring, error handling
7. Scoring contract: method (exit_code/json), metric_path, timeout
8. Constraints: timeout, max_retries, required_capabilities

Write your architecture strategy to stdout. Be specific — every field,
every method body, every metadata key should be defined." --project $PROJECT_PATH
```

### Perspective 2: Verification & Evolution Strategy (MANDATORY)

Spawn a strategist to design the VerifyResult.details for outer loop evolution:
```bash
factory agent strategist --task "Design the VerifyResult.details structure for
optimal outer loop evolution.

Read:
- .factory/strategy/research-outer-loop.md (outer loop feedback research)
- .factory/strategy/research-verification.md (verification approach)
- .factory/strategy/research-domain.md (domain context)

CONTEXT: VerifyResult.details is the LEARNING SIGNAL for the factory's
reflection and improvement mechanisms. details is the learning signal for the
factory's reflection and improvement mechanisms — rich domain-specific
dimensions enable any algorithm to learn what distinguishes good solutions
from bad ones.

The verify_adapter (factory/outer_loop/verify_adapter.py) aggregates per-instance
VerifyResults into an EvalResult with details structured as:
{
    'verify_count': N,
    'passed_count': P,
    'failed_count': F,
    'instance_results': [
        {'index': 0, 'passed': True, 'score': 0.8, 'details': {...}},
        {'index': 1, 'passed': False, 'score': 0.2, 'details': {...}},
    ]
}

The PER-INSTANCE details dict (inside each instance_results entry) is what
you are designing. This is where domain-specific dimensions live.

DESIGN REQUIREMENTS:
1. At least 4 numeric keys that enable mean/delta comparison between good
   and bad solutions (e.g., blunder_count, avg_eval, execution_time_ms,
   error_rate)
2. At least 1 categorical key for error classification (e.g., failure_type:
   'timeout'|'wrong_answer'|'crash'|'partial')
3. Per-instance breakdown list when the domain has sub-evaluations
   (e.g., game_results for chess, test_results for code)
4. Keys must be NAMED for the specific domain — not generic

EXAMPLE from chess-evolve:
details = {
    'wins': 3,
    'draws': 1,
    'losses': 1,
    'win_rate': 0.7,
    'blunder_count': 2,
    'avg_eval': 1.45,
    'total_moves': 187,
    'depth': 3,
    'game_results': [
        {'opponent': 'stockfish-d3', 'result': 'win', 'moves': 42, 'blunders': 0},
        {'opponent': 'stockfish-d3', 'result': 'loss', 'moves': 28, 'blunders': 2},
    ],
}

Write your verification/evolution strategy to stdout covering:
1. Complete VerifyResult.details schema with types and descriptions
2. Which keys enable meaningful comparison and how
3. Per-instance granularity (sub-evaluation breakdown list)
4. How the reflection mechanism can use these keys to suggest improvements" --project $PROJECT_PATH
```

### Additional Perspectives (for complex domains)

For complex domains, you MAY add:
- **Domain-specific strategy** — e.g., for drug discovery: docking scoring
  functions, pose selection, binding energy normalization

## Synthesis

After all strategists complete, SYNTHESIZE their outputs into a single
unified specification. Write to .factory/strategy/current.md with this
EXACT structure:

```markdown
## Task Specification

### Identity
- **task_name:** <kebab-case, e.g. 'chess-evolve'>
- **task_class_name:** <PascalCase + 'Task', e.g. 'ChessEvolveTask'> (Python only)
- **description:** <one-line description>
- **format:** TOML | Python

### Scoring
- **method:** exit_code | json
- **metric_path:** <dot-separated path, default 'score'>

### Constraints
- **timeout:** <seconds, minimum 60>
- **max_retries:** <integer, default 1>
- **required_capabilities:** <list or None>

### Instances
| id | metadata | description |
|---|---|---|
| <id> | `{key: value, ...}` | <what this tests> |

### Setup
<what setup() does — create dirs, write files, install deps>

### Prompt
<prompt template with {metadata} interpolation points>

### Verify
<complete verify() logic — Python code or detailed pseudocode>

### VerifyResult.details Design

This section is CRITICAL for outer loop evolution.

#### Details Schema
| key | type | description | learning value |
|---|---|---|---|
| <key> | <type> | <what it measures> | <how reflection uses it> |

#### Example Details Dict
```python
details = {
    '<key1>': <example_value>,
    '<key2>': <example_value>,
    ...
}
```

#### Per-Instance Granularity
<describe any sub-evaluation breakdown lists>

#### Evolution Signal Quality
<explain how these keys enable the reflection mechanism to suggest specific improvements>
```

## Critical Rules

1. **TOML vs Python**: Choose TOML only when verify is a shell command AND
   instances come from a directory AND prompts are static. Otherwise Python.
   When in doubt, choose Python — it's always capable enough.
2. **Name consistency**: task_name is kebab-case. meta['name'] and
   TaskDefinition.name must match exactly. task_class_name is PascalCase+'Task'.
3. **Instance completeness**: Every instance must have id and metadata dict.
4. **Score range**: score must be float in [0.0, 1.0]. Normalize if needed.
5. **Details richness**: VerifyResult.details MUST have at least 4 domain-specific
   numeric keys beyond just {passed, score}. Sparse details = blind evolution.
6. **No ambiguity**: The Builder must implement from this spec alone.
"""

_GATE_STRATEGY_PROMPT = """\
Review the Task specification plan in .factory/strategy/current.md.

Check that it correctly captures:
1. The domain, instances, and measurement approach
2. The verification logic and scoring method
3. The VerifyResult.details design for outer loop evolution
4. Sufficient detail for the Builder to implement without ambiguity

PROCEED to generate the Task file, or RELOOP to refine the specification.
"""

_BUILDER_PROMPT = """\
You are the Builder for task-setup. Implement the Task file specified in
the strategist's plan.

## Input

Read these files:
- .factory/strategy/current.md — the Task specification (primary input)
- .factory/strategy/research-domain.md — domain context
- .factory/strategy/research-verification.md — verification details
- .factory/strategy/research-outer-loop.md — VerifyResult.details design

## CRITICAL: VerifyResult.details is the Outer Loop's Learning Signal

The most important part of your implementation is the `details` dict in
VerifyResult. This dict is the ONLY channel through which the factory's
reflection and improvement mechanisms learn what makes good solutions
different from bad ones.

details is the learning signal for the factory's reflection and improvement
mechanisms — rich domain-specific dimensions enable any algorithm to learn
what distinguishes good solutions from bad ones. The reflection mechanism:
1. Examines VerifyResult.details dicts across individuals
2. Identifies which fields differ between high-performing and low-performing
   solutions
3. Generates improvement suggestions like "focus on reducing blunder_count"
   or "improve coverage_pct" based on the differences it finds

If your details dict is sparse (just `{passed: True, score: 0.8}`), the
reflection mechanism has NOTHING to compare and evolution stalls. The details
dict must contain rich, domain-specific numeric dimensions.

### Design Guidance for VerifyResult.details

- Include at least 4 numeric keys (int or float) — these enable quantitative
  comparison across solutions (e.g., blunder_count, avg_eval, execution_time,
  error_rate)
- Include at least 1 categorical key for error classification (e.g.,
  failure_type: 'timeout'|'wrong_answer'|'crash'|'partial')
- Include per-instance breakdowns when the domain has sub-evaluations
  (e.g., game_results for chess, test_results for code)
- Name keys for the specific domain — not generic

### Example: chess-evolve (GOOD — rich details)
```python
VerifyResult(
    passed=win_rate >= 0.5,
    score=win_rate,
    details={
        "wins": 3,
        "draws": 1,
        "losses": 1,
        "win_rate": 0.7,
        "blunder_count": 2,
        "avg_eval": 1.45,
        "total_moves": 187,
        "depth": 3,
        "game_results": [
            {"opponent": "stockfish-d3", "result": "win", "moves": 42, "blunders": 0},
            {"opponent": "stockfish-d3", "result": "loss", "moves": 28, "blunders": 2},
        ],
    },
)
```

### Anti-example (BAD — sparse details)
```python
VerifyResult(passed=True, score=0.8, details={"status": "ok"})
# The reflection mechanism cannot learn ANYTHING from this
```

Implement the details dict EXACTLY as specified in the "### VerifyResult.details
Design" section of current.md. Every key listed there must appear in your
verify() implementation.

## Output Requirements

### Step 1: Generate the Task file

**If format is TOML**, generate `.factory/tasks/<task_name>.toml`:
```toml
[task]
name = "<task_name>"
description = "<description>"

[instances]
format = "directory"
source = "instances/"

[setup]
command = "<setup command>"

[prompt]
text = "<prompt text>"

[verify]
command = "<verify command>"

[scoring]
method = "<exit_code or json>"

[constraints]
timeout = <seconds>
max_retries = <count>
```

**If format is Python**, generate `.factory/tasks/<task_name>.py`:
```python
\"\"\"<TaskClassName> — <description>.

Implements the four-hook Task interface: instances, setup, prompt, verify.
\"\"\"

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from factory.task import (
    ScoringContract,
    Task,
    TaskConstraints,
    TaskDefinition,
    TaskInstance,
    VerifyResult,
)


class <TaskClassName>(Task):
    \"\"\"<One-line description>.\"\"\"

    def __init__(self) -> None:
        defn = TaskDefinition(
            name="<task_name>",
            description="<description>",
            scoring=ScoringContract(method="<method>", metric_path="<path>"),
            constraints=TaskConstraints(timeout=<seconds>, max_retries=<count>),
        )
        super().__init__(definition=defn)

    def instances(self) -> Iterator[TaskInstance]:
        \"\"\"Discover what to work on.\"\"\"
        yield TaskInstance(id="<id>", metadata={...})

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        \"\"\"Prepare the environment for one instance.\"\"\"
        workspace.mkdir(parents=True, exist_ok=True)

    def prompt(self, instance: TaskInstance) -> str:
        \"\"\"What should the agent do for this instance?\"\"\"
        return "<prompt>"

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult:
        \"\"\"Did it work? Returns VerifyResult with pass/fail + score + rich details.\"\"\"
        # IMPORTANT: details dict must contain domain-specific numeric dimensions
        # for the reflection mechanism's analysis. See the spec's
        # "VerifyResult.details Design" section for the exact schema.
        return VerifyResult(
            passed=False,
            score=0.0,
            details={
                # Populate ALL keys from the spec's details schema
                "<key1>": <value>,
                "<key2>": <value>,
                ...
            },
        )


# ── Module-level exports (required by TaskRegistry) ──

meta = {
    "name": "<task_name>",
    "description": "<description>",
}


def task() -> <TaskClassName>:
    return <TaskClassName>()
```

### Step 2: Write the task name file

Write ONLY the bare task name (e.g., `chess-evolve`) to
`.factory/generated-task-name.txt`. No newlines, no quotes, no whitespace.
This file is read by the downstream validation step.

### Step 3: Verify locally

After generating the file, read it back and verify the implementation
is correct. Do NOT run `factory task validate` — the workflow handles
that automatically via the validate_task FnNode.

## Validation Checklist (Python files)

Before considering the task done, verify:
- [ ] `from factory.task import Task, TaskInstance, VerifyResult, TaskDefinition, ScoringContract, TaskConstraints`
- [ ] Class inherits from `Task`
- [ ] `__init__` creates `TaskDefinition` and calls `super().__init__(definition=defn)`
- [ ] `instances()` returns `Iterator[TaskInstance]` — uses `yield`, not `return list`
- [ ] `setup(instance: TaskInstance, workspace: Path)` returns `None`
- [ ] `prompt(instance: TaskInstance)` returns `str` — non-empty
- [ ] `verify(instance: TaskInstance, workspace: Path)` returns `VerifyResult`
- [ ] Module-level `meta` dict has `name` key matching `TaskDefinition.name`
- [ ] Module-level `task()` function returns an instance of the class
- [ ] `score` is always `float` (not int) in range `[0.0, 1.0]`
- [ ] `passed` is always `bool` (not 0/1)
- [ ] `.factory/generated-task-name.txt` contains just the task name
- [ ] `details` dict has ALL keys from the spec's VerifyResult.details Design section
- [ ] At least 4 numeric keys in details for meaningful comparison

## Critical Gotchas

- `extra='forbid'` on all Pydantic models — do NOT pass unexpected kwargs
- `strict=True` — `score` must be `float` not `int`, `passed` must be `bool` not `0`/`1`
- `instances()` must return `Iterator[TaskInstance]`, not `list` — use `yield`
- Files starting with `_` in .factory/tasks/ are skipped by the registry
- `meta['name']` MUST match `TaskDefinition(name=...)` exactly
- `details` values should be JSON-serializable (no numpy arrays, no custom objects)
"""

_QA_DIRECTOR_PROMPT = """\
You are the QA Director for the task-setup workflow. Your job is to validate
the generated Task file across multiple quality dimensions by spawning
adversarial tester agents.

## Input

Read these files:
- .factory/strategy/current.md — the Task specification (what was planned)
- .factory/generated-task-name.txt — the task name (what was built)
- .factory/strategy/research-outer-loop.md — outer loop details design

Then find and read the generated Task file at:
- .factory/tasks/<task_name>.py or .factory/tasks/<task_name>.toml
  (where <task_name> comes from generated-task-name.txt)

## QA Dimensions

Spawn adversarial testers for each dimension:

### QA 1: Structural Validation

Spawn an adversarial tester to verify the Task file is structurally correct:
```bash
factory agent adversarial_tester --task "Validate structural correctness of the
generated Task file.

Read .factory/generated-task-name.txt to get the task name, then read the
Task file at .factory/tasks/<name>.py or .factory/tasks/<name>.toml.

Check:
1. For Python: imports are correct (factory.task imports)
2. For Python: class inherits from Task
3. For Python: all 4 hooks implemented (instances, setup, prompt, verify)
4. For Python: meta dict exists with correct name
5. For Python: task() function exists and is callable
6. For TOML: all required sections present
7. Types are correct: score is float, passed is bool
8. instances() yields at least 1 instance (verify by reading the code)
9. verify() returns VerifyResult (not dict, not tuple)
10. meta['name'] matches TaskDefinition name

Report any issues found. Write results to stdout." --project $PROJECT_PATH
```

### QA 2: Evolution-Readiness (MANDATORY)

This check is CRITICAL. Spawn an adversarial tester to verify the Task's
VerifyResult.details is rich enough for the factory's reflection and
improvement mechanisms:
```bash
factory agent adversarial_tester --task "Check evolution-readiness of the
generated Task file's VerifyResult.details.

Read:
- .factory/strategy/research-outer-loop.md (what details keys were planned)
- .factory/strategy/current.md (the specification's details design section)
- The Task file at .factory/tasks/<name>.py or .factory/tasks/<name>.toml

EVOLUTION-READINESS CRITERIA:
1. The verify() method produces a VerifyResult with a non-trivial details dict
2. The details dict contains AT LEAST 4 numeric keys (int or float) that can
   be compared between individuals by the factory's reflection mechanism
3. The details keys match what was specified in the 'VerifyResult.details Design'
   section of current.md
4. There is at least 1 categorical key for error/failure classification
5. If the domain has sub-evaluations (e.g., multiple games, multiple tests),
   there is a per-instance breakdown list in details
6. No details key is hardcoded to a constant value (that would be useless
   for comparison)
7. Details values are JSON-serializable

ANTI-PATTERNS (flag these):
- details = {} (empty — reflection mechanism learns nothing)
- details = {'status': 'ok'} (one string field — no numeric comparison possible)
- details = {'passed': True, 'score': 0.8} (duplicates top-level fields only)
- All numeric keys hardcoded to 0 or 1 (no variance = no learning signal)

Report a PASS/FAIL verdict with specific findings.
Write results to stdout." --project $PROJECT_PATH
```

### QA 3: Domain Correctness

Spawn an adversarial tester to verify the instances are representative:
```bash
factory agent adversarial_tester --task "Check domain correctness of the
generated Task's instances.

Read:
- .factory/strategy/research-domain.md (what instances were researched)
- .factory/strategy/current.md (what instances were specified)
- The Task file at .factory/tasks/<name>.py or .factory/tasks/<name>.toml

Check:
1. instances() yields at least 3 distinct instances
2. Instance IDs are unique and kebab-case
3. Instance metadata contains all fields referenced by setup(), prompt(),
   and verify()
4. Instances cover different aspects of the domain (not just duplicates
   with different IDs)
5. Setup and prompt implementations correctly use instance metadata
6. No instance has empty or placeholder metadata

Report any issues found. Write results to stdout." --project $PROJECT_PATH
```

## Synthesis

After all testers complete, SYNTHESIZE their findings into a QA report.
Write to .factory/strategy/qa-report.md with this structure:

```markdown
# QA Report — <task_name>

## Overall Verdict: PASS | FAIL

## Structural Validation
- **Verdict:** PASS | FAIL
- **Issues:** <list any issues>

## Evolution-Readiness
- **Verdict:** PASS | FAIL
- **Details keys found:** <list the keys in verify()'s details dict>
- **Numeric keys count:** <N> (minimum 4 required)
- **Categorical keys count:** <N> (minimum 1 required)
- **Per-instance breakdown:** Yes | No
- **Issues:** <list any issues>

## Domain Correctness
- **Verdict:** PASS | FAIL
- **Instance count:** <N>
- **Issues:** <list any issues>

## Recommendations
<specific fixes if any dimension failed>
```

The gate_qa node will read this report to decide PROCEED or RELOOP.
"""

_GATE_QA_PROMPT = """\
Evaluate the QA report for the generated Task file.

Read .factory/strategy/qa-report.md — it contains verdicts for:
1. Structural validation (imports, types, hooks, exports)
2. Evolution-readiness (VerifyResult.details richness for the reflection mechanism)
3. Domain correctness (instances are representative and complete)

PROCEED if ALL three dimensions passed.

RELOOP if any dimension failed. When relooping:
- The builder will re-run with the QA report available
- Provide specific feedback on what needs fixing
- Prioritize evolution-readiness failures — a Task that passes structural
  validation but has sparse details will produce blind evolution

HALT only if the Task is fundamentally broken beyond repair (rare).
"""

_ARCHIVIST_PROMPT = """\
Archive the task-setup workflow session.

Read these files and produce a concise archive entry:
- .factory/strategy/user-intent.md — original focus
- .factory/strategy/current.md — final task specification
- .factory/strategy/research-domain.md — domain findings
- .factory/strategy/research-verification.md — verification approach
- .factory/strategy/research-outer-loop.md — outer loop details design
- .factory/strategy/qa-report.md — QA results
- .factory/generated-task-name.txt — task name

Summarize in .factory/archive/task-setup.md:
1. User's original focus domain
2. Task format chosen (TOML or Python) and why
3. Number of instances and their coverage
4. Verification approach and scoring method
5. VerifyResult.details keys designed for evolution
6. QA results (all pass / any failures)
7. Generated task file path
"""

# ── FnNode command constant ─────────────────────────────────────

_VALIDATE_TASK_COMMAND = (
    "bash -c '"
    'TASK_NAME=$(cat {project_path}/.factory/generated-task-name.txt 2>/dev/null | tr -d "\\n\\r"); '
    'if [ -z "$TASK_NAME" ]; then echo "No task name found in generated-task-name.txt" >&2; exit 1; fi; '
    'factory task validate "$TASK_NAME" --project {project_path}'
    "'"
)


# ── Workflow construction ───────────────────────────────────────


def workflow() -> Workflow:
    """Build the task-setup v2 workflow graph (9 nodes, 11 edges)."""

    # ── Nodes ──

    research_director = AgentNode(
        id="research_director",
        role=AgentRole.CEO,
        prompt_template=_RESEARCH_DIRECTOR_PROMPT,
        timeout=600,
        # user-intent.md is an external input written by the CLI layer
        # (_execute_ceo) before workflow execution — not declared in reads
        # to avoid data-dependency validation errors.
        reads=set(),
        writes={
            ".factory/strategy/research-domain.md",
            ".factory/strategy/research-verification.md",
            ".factory/strategy/research-outer-loop.md",
        },
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-domain.md",
                must_exist=True,
                min_size=200,
            ),
            ArtifactCheck(
                path=".factory/strategy/research-verification.md",
                must_exist=True,
                min_size=200,
            ),
            ArtifactCheck(
                path=".factory/strategy/research-outer-loop.md",
                must_exist=True,
                min_size=200,
            ),
        ],
    )

    gate_research = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        max_iterations=2,
        gate_prompt=_GATE_RESEARCH_PROMPT,
        reads={
            ".factory/strategy/research-domain.md",
            ".factory/strategy/research-verification.md",
            ".factory/strategy/research-outer-loop.md",
        },
    )

    strategy_director = AgentNode(
        id="strategy_director",
        role=AgentRole.CEO,
        prompt_template=_STRATEGY_DIRECTOR_PROMPT,
        timeout=600,
        reads={
            ".factory/strategy/research-domain.md",
            ".factory/strategy/research-verification.md",
            ".factory/strategy/research-outer-loop.md",
            # user-intent.md omitted from reads — external CLI input
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/current.md",
                must_exist=True,
                min_size=500,
                must_contain=["## Task Specification", "### VerifyResult.details Design"],
            ),
        ],
    )

    gate_strategy = GateNode(
        id="gate_strategy",
        evaluator_type="user",
        evaluator_role=None,
        gate_prompt=_GATE_STRATEGY_PROMPT,
        reads={".factory/strategy/current.md"},
    )

    builder = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=_BUILDER_PROMPT,
        timeout=600,
        reads={
            ".factory/strategy/current.md",
            ".factory/strategy/research-domain.md",
            ".factory/strategy/research-verification.md",
            ".factory/strategy/research-outer-loop.md",
        },
        writes={".factory/generated-task-name.txt"},
        post_checks=[
            ArtifactCheck(
                path=".factory/generated-task-name.txt",
                must_exist=True,
                min_size=1,
            ),
        ],
    )

    qa_director = AgentNode(
        id="qa_director",
        role=AgentRole.CEO,
        prompt_template=_QA_DIRECTOR_PROMPT,
        timeout=600,
        reads={
            ".factory/strategy/current.md",
            ".factory/generated-task-name.txt",
            ".factory/strategy/research-outer-loop.md",
        },
        writes={".factory/strategy/qa-report.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/qa-report.md",
                must_exist=True,
                min_size=200,
            ),
        ],
    )

    gate_qa = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        max_iterations=2,
        gate_prompt=_GATE_QA_PROMPT,
        reads={".factory/strategy/qa-report.md"},
    )

    validate_task = FnNode(
        id="validate_task",
        command=_VALIDATE_TASK_COMMAND,
        blocking=True,
        reads={".factory/generated-task-name.txt"},
        writes=set(),
    )

    archivist = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template=_ARCHIVIST_PROMPT,
        blocking=False,
        reads={
            ".factory/strategy/current.md",
            ".factory/strategy/research-domain.md",
            ".factory/strategy/research-verification.md",
            ".factory/strategy/research-outer-loop.md",
            ".factory/strategy/qa-report.md",
        },
        writes={".factory/archive/task-setup.md"},
    )

    # ── Assemble nodes dict ──

    nodes = {
        "research_director": research_director,
        "gate_research": gate_research,
        "strategy_director": strategy_director,
        "gate_strategy": gate_strategy,
        "builder": builder,
        "qa_director": qa_director,
        "gate_qa": gate_qa,
        "validate_task": validate_task,
        "archivist": archivist,
    }

    # ── Edges (11 total) ──

    edges = [
        # 1. Research complete → CEO evaluates quality
        Edge(source="research_director", target="gate_research", condition=None),
        # 2. Research approved → strategy synthesis
        Edge(source="gate_research", target="strategy_director", condition=VerdictType.PROCEED),
        # 3. Research insufficient → director re-runs researchers (back-edge)
        Edge(source="gate_research", target="research_director", condition=VerdictType.RELOOP),
        # 4. Strategy complete → user reviews
        Edge(source="strategy_director", target="gate_strategy", condition=None),
        # 5. User approves → builder generates task file
        Edge(source="gate_strategy", target="builder", condition=VerdictType.PROCEED),
        # 6. User requests changes → director revises (back-edge)
        Edge(source="gate_strategy", target="strategy_director", condition=VerdictType.RELOOP),
        # 7. Task file generated → QA validates
        Edge(source="builder", target="qa_director", condition=None),
        # 8. QA complete → CEO evaluates results
        Edge(source="qa_director", target="gate_qa", condition=None),
        # 9. QA passed → FnNode validates task
        Edge(source="gate_qa", target="validate_task", condition=VerdictType.PROCEED),
        # 10. QA failed → builder fixes issues (back-edge)
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # 11. Validation passed → archive (non-blocking)
        Edge(source="validate_task", target="archivist", condition=None),
    ]

    # ── Build workflow ──

    wf = Workflow(
        name="task-setup",
        nodes=nodes,
        edges=edges,
        start_node="research_director",
        terminal=True,
    )

    # Validate graph structure before returning
    issues = wf.validate_graph()
    if issues:
        raise ValueError(
            f"task-setup workflow has {len(issues)} validation issue(s): "
            + "; ".join(issues)
        )

    return wf
