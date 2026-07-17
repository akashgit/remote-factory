# Contributing Benchmarks

This guide walks you through adding a new benchmark to re:factory. By the end, you will have a workflow definition that the factory can execute, a Harbor agent that runs it in an isolated container, and a CI matrix entry that runs it on every push.

If you are looking for the technical spec (DSL primitives, node types, edge conditions), see [`factory/workflow/contributed/README.md`](../factory/workflow/contributed/README.md). This guide focuses on the practical, end to end process.


## What a Benchmark Contribution Consists Of

A benchmark contribution has three pieces:

1. **A workflow definition** that lives under `factory/workflow/contributed/<name>/`. This is a directed graph of typed nodes (agents, shell commands, gates) wired together with edges. The factory's workflow engine walks the graph at runtime.

2. **A Harbor agent** that runs the workflow inside an isolated container. Harbor provisions the environment, installs dependencies, seeds initial state, and then hands off to `factory workflow run <name>`.

3. **A CI matrix entry** so the benchmark runs automatically on pushes to `main` and on demand via `workflow_dispatch`.


## Linter Validation

Before your benchmark can be merged, it must pass the contributed workflow linter. Run it locally:

```bash
factory workflow lint-contributed
```

The linter enforces 18 conditions organized into five categories. Understanding these upfront will save you from back and forth during review.


### File Structure (4 checks)

Every workflow directory must contain exactly these four files:

| File | Purpose |
|------|---------|
| `__init__.py` | Exports `meta` and `workflow` so existing import paths work |
| `workflow.py` | Contains the `meta` dict and `workflow()` function |
| `README.md` | Description, graph diagram, CLI usage |
| `test_workflow.py` | Regression tests covering graph structure, trigger, registration, and meta |

If any of these files is missing, the linter reports a `missing-<filename>` error and stops checking that directory.


### Module Load (1 check)

The linter attempts to `import` your `workflow.py` dynamically. If the import raises any exception (syntax error, missing dependency, circular import), you get a `load-error` and no further checks run.

Keep your imports minimal. The workflow definition should only need types from `factory.workflow.primitives` and `factory.models`.


### Meta Dict (3 checks)

Your `workflow.py` must define a module level dictionary called `meta`. The linter checks:

1. `meta` exists and is a `dict`
2. `meta` contains a `"name"` key
3. `meta` contains a `"description"` key

Here is what a valid meta dict looks like, taken from legacybench:

```python
meta = {
    "name": "legacybench",
    "description": (
        "Legacy-Bench benchmark mode — 4-node pipeline for fixing bugs in "
        "legacy code (COBOL, Fortran, C, Java 7, Assembly). "
        "study → builder → gate_verify → auto_merge with RELOOP on failure."
    ),
}
```


### Workflow Function (2 checks)

Your `workflow.py` must define a callable named `workflow` that:

1. Is callable (the linter checks `callable(workflow)`)
2. Executes without raising an exception when called with no arguments

The function must return a `Workflow` object built from the DSL primitives. The linter calls `workflow()` and then runs graph validation on the result.


### Graph Validation (8 checks)

Once `workflow()` returns a `Workflow`, the linter delegates to `validate_graph()` which performs these structural checks using NetworkX:

1. **Start node exists:** `start_node` must be a key in the `nodes` dict.

2. **Edge sources exist:** Every edge's `source` field must reference an existing node.

3. **Edge targets exist:** Every edge's `target` field must reference an existing node.

4. **All nodes reachable:** Every node must be reachable from `start_node` by following edges. Orphaned nodes that cannot be reached are flagged.

5. **Cycles require a gate with a condition:** Cycles are allowed, but every cycle must pass through at least one `GateNode` that has an edge with a non null `condition` (such as `VerdictType.RELOOP`). This prevents infinite loops by ensuring a gate controls reentry.

6. **Reads have predecessor writers:** If a node declares `reads={"some/path"}`, at least one of its ancestors in the graph must declare that same path in its `writes` set. This enforces data flow correctness.

7. **Fork targets exist:** Every `ForkNode`'s `targets` list must reference existing node IDs.

8. **Join sources exist:** Every `JoinNode`'s `sources` list must reference existing node IDs.


## How Harbor Execution Works

Benchmarks run inside isolated containers managed by the [Harbor framework](https://harborframework.com). Here is the lifecycle:

1. Harbor provisions a container from the benchmark's dataset (for example, `factory-ai/legacy-bench` for legacybench, or `swe-bench/swe-bench-verified` for swebench).

2. Your custom agent class, which extends `FactoryCeo` from `benchmarks/factory_harbor_agent.py`, handles the installation and execution phases.

3. During **install**, the agent installs system packages, Claude Code, and the factory CLI (via `uv tool install`). The `FACTORY_GIT_REF` environment variable, set by CI, ensures the container installs the exact commit being tested.

4. During **run**, the agent initializes git, seeds `.factory/` state (a minimal `config.json` and `eval_profile.json`), writes the task instruction to `/tmp/task-instruction.md`, and then invokes either `factory ceo . --headless` or `factory workflow run <name>`.

5. After execution, Harbor's verifier evaluates the solution. Results are written as JSON to the `benchmarks/results/` directory.

Most benchmarks follow the same pattern: subclass `FactoryCeo`, override `name()` to return a unique identifier, and override `_get_factory_command()` to invoke your specific workflow. Here is the legacybench agent as a concrete example:

```python
class LegacybenchFactoryCeo(FactoryCeo):
    """Runs the deterministic legacybench workflow."""

    @staticmethod
    @override
    def name() -> str:
        return "legacybench-factory-ceo"

    @override
    def _get_factory_command(self) -> str:
        return (
            'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '
            'factory workflow run legacybench . '
            '2>&1 </dev/null | tee /logs/agent/factory-ceo.txt'
            '; exit 0'
        )
```


## Step by Step Walkthrough

This walkthrough uses legacybench as the reference example. Replace `legacybench` with your benchmark name throughout.


### Step 1: Create the Workflow Directory

```bash
mkdir -p factory/workflow/contributed/<name>/
```


### Step 2: Write `workflow.py`

Define a `meta` dict and a `workflow()` function that returns a `Workflow`. Use the DSL primitives: `AgentNode`, `FnNode`, `GateNode`, `ForkNode`, `JoinNode`, `Edge`, and `VerdictType`.

The workflow graph defines the execution pipeline for your benchmark. A typical benchmark pipeline looks like:

- A **study** node (`FnNode`) that scans the workspace and reads the task instruction
- A **builder** node (`AgentNode`) that implements the solution
- A **gate** node (`GateNode`) that verifies the solution (compilation, tests)
- An **auto_merge** node (`FnNode`) that merges changes to the base branch

Gates can loop back to earlier nodes using `VerdictType.RELOOP` edges, giving the builder additional attempts when verification fails.

See `factory/workflow/contributed/legacybench/workflow.py` for a complete, working example.


### Step 3: Create `__init__.py`

This file exports `meta` and `workflow` from your workflow module:

```python
from .workflow import meta, workflow

__all__ = ["meta", "workflow"]
```


### Step 4: Write `README.md`

Include a brief description of what the benchmark tests, an ASCII graph diagram showing the node pipeline, and a CLI usage example:

```bash
factory workflow run <name> --project /path/to/repo
```

See `factory/workflow/contributed/legacybench/README.md` for the expected format.


### Step 5: Write `test_workflow.py`

Your tests should cover:

- Workflow name and node count
- Graph validation passes (`wf.validate_graph()` returns an empty list)
- Node types match expectations (`AgentNode`, `FnNode`, `GateNode`)
- Edge structure (PROCEED, RELOOP conditions)
- Trigger function accepts the correct mode and rejects others
- Registration in `register_all()`
- Meta dict has `name` and `description`

Organize tests into classes by concern: `Test<Name>Workflow`, `Test<Name>Terminal`, `Test<Name>Trigger`, `Test<Name>Registration`, `Test<Name>Meta`. See `factory/workflow/contributed/legacybench/test_workflow.py` for the full pattern.


### Step 6: Register the Workflow

Add your workflow to `factory/workflow/definitions.py` in the `register_all()` function:

```python
from factory.workflow.contributed.<name> import workflow as <name>_workflow

# Inside register_all():
"<name>": <name>_workflow(),
```


### Step 7: Add a Harbor Agent Subclass

In `benchmarks/factory_harbor_agent.py`, add a new class that extends `FactoryCeo`:

```python
class <Name>FactoryCeo(FactoryCeo):
    """Runs the deterministic <name> workflow."""

    @staticmethod
    @override
    def name() -> str:
        return "<name>-factory-ceo"

    @override
    def _get_factory_command(self) -> str:
        return (
            'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '
            'factory workflow run <name> . '
            '2>&1 </dev/null | tee /logs/agent/factory-ceo.txt'
            '; exit 0'
        )
```


### Step 8: Add a Config Entry

In `benchmarks/config.sh`, add a case block inside `benchmark_config()`:

```bash
<name>)
    BENCH_DATASET="<dataset-identifier>"
    BENCH_AGENT_CLASS="factory_harbor_agent:<Name>FactoryCeo"
    BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
    BENCH_FILTER_STYLE="glob"
    ;;
```

Also add your benchmark name to the `benchmark_all_names()` function and to the error message in the default `*)` case.


### Step 9: Add a CI Matrix Entry

In `.github/workflows/benchmark.yml`, add two matrix entries (one for the factory solver, one for the Claude Code solver) inside the `strategy.matrix.include` list:

```yaml
- benchmark: <name>
  solver: factory
  default_instance: '<smoke-test-instance-id>'
  enabled: ${{ github.event_name == 'schedule' || ... }}
- benchmark: <name>
  solver: claude-code
  default_instance: '<smoke-test-instance-id>'
  enabled: ${{ github.event_name == 'schedule' || ... }}
```

Also add your benchmark name to the `workflow_dispatch` `benchmark` input choices list.

Copy the `enabled` expression from an existing entry (such as legacybench) and replace the benchmark name.


### Step 10: Run the Linter

```bash
factory workflow lint-contributed
```

Fix any issues the linter reports. All 18 conditions must pass.


### Step 11: Run the Test Suite

```bash
pytest -v
```

Make sure your new tests pass and you have not broken any existing tests.


## Expected Result Format

Each benchmark run produces a JSON result file in `benchmarks/results/`. The schema:

```json
{
  "benchmark": "legacybench",
  "instance_id": "1907c2-c-debug-legacy-buddy-fix",
  "solver": "factory",
  "passed": 1,
  "total": 1,
  "score": 1.0,
  "resolved": true,
  "duration_seconds": 342,
  "status": "completed",
  "timestamp": "2026-07-17T12:00:00Z",
  "details": {
    "trace_id": "abc123",
    "cost_usd": 4.50
  }
}
```

The `resolved` field is the authoritative pass/fail signal. Harbor's verifier sets it. The `score` field is a float between 0 and 1, where 1.0 means the benchmark instance was fully solved.


## Submission Checklist

Before opening your PR, verify all of the following:

**Workflow directory** (`factory/workflow/contributed/<name>/`)

- [ ] `__init__.py` exists and exports `meta` and `workflow`
- [ ] `workflow.py` defines a `meta` dict with `name` and `description`
- [ ] `workflow.py` defines a callable `workflow()` that returns a `Workflow`
- [ ] `workflow()` executes without raising
- [ ] `validate_graph()` returns an empty list
- [ ] `README.md` exists with description, graph diagram, and CLI usage
- [ ] `test_workflow.py` covers graph structure, trigger, registration, and meta

**Graph structure**

- [ ] `start_node` is a valid key in `nodes`
- [ ] All edge sources and targets reference existing nodes
- [ ] All nodes are reachable from `start_node`
- [ ] Any cycles pass through a `GateNode` with a condition edge
- [ ] Reads/writes data flow is consistent (readers have ancestor writers)
- [ ] Fork targets and join sources reference existing nodes

**Integration**

- [ ] Workflow registered in `factory/workflow/definitions.py` `register_all()`
- [ ] Harbor agent subclass added to `benchmarks/factory_harbor_agent.py`
- [ ] Config entry added to `benchmarks/config.sh`
- [ ] CI matrix entries added to `.github/workflows/benchmark.yml`
- [ ] `factory workflow lint-contributed` passes with no issues
- [ ] `pytest -v` passes with no failures


## Existing Benchmarks

These benchmarks are already in the repository and serve as living examples:

| Benchmark | What it tests | Workflow location |
|-----------|---------------|-------------------|
| legacybench | Bug fixes in legacy code (COBOL, Fortran, C, Java 7, Assembly) | `factory/workflow/contributed/legacybench/` |
| swebench | Real world GitHub issues from popular Python repositories | `factory/workflow/contributed/swebench/` |
| featurebench | Feature implementation tasks with structured test suites | `factory/workflow/contributed/featurebench/` |
| terminalbench | Terminal and shell scripting challenges | `factory/workflow/contributed/terminalbench/` |
| programbench | Program analysis and transformation tasks | `factory/workflow/contributed/programbench/` |

When in doubt, read the source. The legacybench workflow is the simplest (4 nodes, 4 edges) and makes the best starting point for understanding the patterns.


## Further Reading

- [`factory/workflow/contributed/README.md`](../factory/workflow/contributed/README.md) for the technical spec (DSL primitives, directory layout, linting details)
- [`factory/workflow/README.md`](../factory/workflow/README.md) for full workflow engine documentation
- [`benchmarks/factory_harbor_agent.py`](../benchmarks/factory_harbor_agent.py) for the base Harbor agent implementation
- [`benchmarks/config.sh`](../benchmarks/config.sh) for benchmark configuration examples
- [`.github/workflows/benchmark.yml`](../.github/workflows/benchmark.yml) for CI integration patterns
