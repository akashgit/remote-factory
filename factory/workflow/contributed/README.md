# Contributed Workflows

Community-contributed workflow definitions for the Factory workflow engine.

## Directory Layout

Each contributed workflow lives in its own directory with the following structure:

```
factory/workflow/contributed/<name>/
├── __init__.py          # Re-exports: from .workflow import meta, workflow
├── workflow.py          # meta dict + workflow() function (the DSL definition)
├── README.md            # What it does, how to invoke it, graph overview
└── test_workflow.py     # Behavioral regression test
```

### Required Artifacts

| File | Purpose |
|---|---|
| `__init__.py` | Re-exports `meta` and `workflow` so existing import paths work |
| `workflow.py` | Contains a `meta` dict (`name`, `description`) and a `workflow()` function returning a `Workflow` |
| `README.md` | Human-readable description, CLI invocation example, and graph diagram |
| `test_workflow.py` | Regression tests validating graph structure, node types, edges, and trigger behavior |

## Adding a New Workflow

1. Create a directory: `factory/workflow/contributed/<name>/`

2. Write `workflow.py` with:
   - A module-level `meta` dict containing `name` and `description`
   - A `workflow()` function that returns a `Workflow` built from DSL primitives (`AgentNode`, `FnNode`, `GateNode`, `ForkNode`, `JoinNode`, `Edge`)
   - A `trigger` function that activates the workflow based on `ProjectState` and context

3. Create `__init__.py`:
   ```python
   from .workflow import meta, workflow

   __all__ = ["meta", "workflow"]
   ```

4. Register the workflow in `factory/workflow/definitions.py` `register_all()`:
   ```python
   from factory.workflow.contributed.<name> import workflow as <name>_workflow
   # ...
   "<name>": <name>_workflow(),
   ```

5. Write `test_workflow.py` covering (see existing tests for patterns):
   - Workflow name and node count
   - Graph validation (`wf.validate_graph()`)
   - Node types and key properties
   - Edge structure (PROCEED, RELOOP conditions)
   - Trigger function behavior (matches correct mode, rejects others)
   - Registration in `register_all()`
   - Meta dict has `name` and `description`

6. Write `README.md` with a description, ASCII graph diagram, and CLI usage example.

7. Run the full test suite: `pytest -v`

## Regression Test Structure

Tests should be organized into test classes by concern:

- `Test<Name>Workflow` — graph structure: node count, node types, edge count, key properties
- `Test<Name>Terminal` — terminal flag on the workflow
- `Test<Name>Trigger` — trigger function accepts/rejects modes correctly
- `Test<Name>Registration` — workflow appears in `register_all()` and validates
- `Test<Name>Meta` — meta dict has required keys

## Linting

A built-in linter validates that every contributed workflow directory has the required artifacts and passes basic structural checks.

**Run locally:**

```bash
factory workflow lint-contributed
```

To lint a custom directory:

```bash
factory workflow lint-contributed --path /path/to/workflows/
```

**What it checks (per directory):**

- `__init__.py` exists
- `workflow.py` exists
- `README.md` exists
- `test_workflow.py` exists
- `workflow.py` has a module-level `meta` dict with `name` and `description`
- `workflow.py` has a callable `workflow()` function
- `workflow()` returns a graph that passes `validate_graph()`

CI runs this automatically on every pull request.

## Workflow DSL Primitives

Workflows are built from typed node primitives defined in `factory/workflow/primitives.py`:

- `AgentNode` — spawns a Claude Code agent with a role, model, timeout, and prompt template
- `FnNode` — runs a shell command
- `GateNode` — evaluates pass/fail/reloop conditions via a shell command or agent
- `ForkNode` / `JoinNode` — parallel execution (fan-out / fan-in)
- `Edge` — connects nodes, optionally with a `VerdictType` condition

See `factory/workflow/README.md` for full DSL documentation.
