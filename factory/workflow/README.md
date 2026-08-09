# Workflow Graph Engine

The workflow graph engine defines all factory modes as directed graphs of typed nodes. One source of truth, two execution formats: headless automation via `WorkflowExecutor` and interactive CEO sessions via auto-generated `SKILL.md` files.

## How it works

```
definitions.py          (8 Python functions, each returning a Workflow)
       │
       ├──► WorkflowExecutor    (headless: walks the DAG deterministically)
       │       factory workflow run improve --project /path
       │
       └──► skill_export.py     (interactive: converts graph → SKILL.md)
               skills/workflow-improve/SKILL.md
               └── CEO reads this at runtime as its mode-specific playbook
```

In interactive mode, `factory ceo` launches a Claude Code session with `ceo.md` as the system prompt. The CEO detects project state, then reads the appropriate `SKILL.md` into context and follows it step by step. The SKILL.md files are prose translations of the same graph the executor walks — so both paths execute the same pipeline.

## Node types

Every workflow is a graph of 6 node types connected by edges:

| Node | Class | Purpose | Example |
|------|-------|---------|---------|
| Agent | `AgentNode` | Spawn a Claude Code specialist agent | Researcher, Builder, QA |
| Function | `FnNode` | Run a shell command | `factory eval {project_path}` |
| Gate | `GateNode` | Decision point producing PROCEED / RELOOP / HALT | CEO reviewing research quality |
| Fork | `ForkNode` | Launch multiple targets in parallel | 3 researchers simultaneously |
| Join | `JoinNode` | Barrier — wait for all parallel branches | Wait for all researchers |
| Study | `Study` | Distinguished `FnNode` wrapping `factory study` | Local codebase analysis |

Each node declares `reads` and `writes` — the set of files it consumes and produces. The graph validator (`validation.py`) uses these to verify data flow: every file a node reads must be written by a predecessor. Pre-existing project files (e.g. `CLAUDE.md`, `factory.md`) should not be declared as reads since no workflow node produces them.

## Edges and verdicts

Edges connect nodes. Unconditional edges always fire. Conditional edges fire only on a specific verdict from a `GateNode`:

```python
Edge(source="gate_qa", target="gate_precheck", condition=VerdictType.PROCEED)
Edge(source="gate_qa", target="builder",        condition=VerdictType.RELOOP)
```

Three verdict types:
- **PROCEED** — output is satisfactory, continue to the next step
- **RELOOP** — output needs improvement, go back to a target node (max 3 iterations)
- **HALT** — something is fundamentally wrong, stop the workflow

## Workflows

8 workflows are registered in `definitions.py`:

| Name | Function | Trigger | Purpose |
|------|----------|---------|---------|
| `build` | `build_workflow()` | `no_repo` or `incomplete` | Build a new project from idea/spec |
| `design` | `design_workflow()` | `no_repo` + interactive | Same as build but with user approval gate at strategy |
| `improve` | `improve_workflow()` | `has_factory` | Improve an existing project through experiments |
| `research` | `research_workflow()` | `has_factory` + `research_target` | Research-driven optimization with failure analysis |
| `meta` | `meta_workflow()` | `has_factory` + `mode=meta` | Improve the factory itself + ACE playbook evolution |
| `discover` | `discover_workflow()` | `no_factory` | Auto-discover eval dimensions |
| `review` | `review_workflow()` | `evals_pending_review` | Verify eval dimensions and initialize factory config |
| `refine` | `refine_workflow()` | `has_factory` + `--refine` | Lightweight pipeline for user-directed refinements |

Relationships: W2 (design) = W1 (build) with `gate_strategy.evaluator_type = "user"`. W4 (research) extends W3 (improve) with baseline measurement, failure analyst, surface constraints, and plateau detection.

## Creating a new workflow

Here is the discover workflow (simplest — 3 nodes) as an example:

```python
from factory.workflow.primitives import (
    AgentRole, Edge, FnNode, GateNode, VerdictType, Workflow,
)

def discover_workflow() -> Workflow:
    nodes = {}
    edges = []

    # Step 1: Run discovery command
    nodes["discover"] = FnNode(
        id="discover",
        command="factory discover {project_path}",
        writes={".factory/eval_profile.json", "eval/score.py"},
    )

    # Step 2: CEO reviews the result
    nodes["gate_discover"] = GateNode(
        id="gate_discover",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt="Verify the discovered eval profile makes sense...",
        reads={".factory/eval_profile.json", "eval/score.py"},
    )

    # Step 3: Re-detect project state
    nodes["redetect"] = FnNode(
        id="redetect",
        command="factory detect {project_path}",
        reads={".factory/eval_profile.json"},
    )

    # Wire them: discover → gate → redetect (on PROCEED)
    #                         └→ discover (on RELOOP — retry)
    edges = [
        Edge(source="discover", target="gate_discover"),
        Edge(source="gate_discover", target="redetect", condition=VerdictType.PROCEED),
        Edge(source="gate_discover", target="discover", condition=VerdictType.RELOOP),
    ]

    # Auto-select when project has no factory setup
    def trigger(state, ctx):
        return state == ProjectState.NO_FACTORY

    return Workflow(
        name="discover",
        nodes=nodes,
        edges=edges,
        start_node="discover",
        trigger=trigger,
    )
```

To register it, add the function to `register_all()` in `definitions.py`:

```python
def register_all() -> dict[str, Workflow]:
    return {
        ...
        "discover": discover_workflow(),
    }
```

### Common patterns

**QA iteration loop** (builder → QA → gate with RELOOP back to builder, max 3 iterations):

```python
nodes["builder"] = AgentNode(id="builder", role=AgentRole.BUILDER, ...)
nodes["gate_build"] = GateNode(id="gate_build", ...)
nodes["qa"] = AgentNode(id="qa", role=AgentRole.QA, ...)
nodes["gate_qa"] = GateNode(id="gate_qa", ...)
nodes["gate_precheck"] = GateNode(id="gate_precheck", ...)

edges = [
    Edge(source="builder", target="gate_build"),
    Edge(source="gate_build", target="qa", condition=VerdictType.PROCEED),
    Edge(source="qa", target="gate_qa"),
    Edge(source="gate_qa", target="gate_precheck", condition=VerdictType.PROCEED),
    Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),  # retry
]
```

**Parallel research** (fork 3 researchers, join, then gate):

```python
nodes["fork_research"] = ForkNode(
    id="fork_research",
    targets=["researcher_a", "researcher_b", "researcher_c"],
)
nodes["join_research"] = JoinNode(
    id="join_research",
    sources=["researcher_a", "researcher_b", "researcher_c"],
)
```

**Non-blocking archivist** (fire-and-forget):

```python
nodes["archivist"] = AgentNode(
    id="archivist", role=AgentRole.ARCHIVIST,
    model="haiku", blocking=False,
)
```

## Composition

The `factory/workflow/composition.py` module provides functions for building
new workflows from existing ones without copying code.

### Inspecting workflows

Before composing, inspect the workflows to find node IDs:

```python
from factory.workflow.composition import describe_nodes
from factory.workflow.definitions import improve_workflow

# Fast mode (default) — deterministic extraction
nodes = describe_nodes(improve_workflow())

# Rich mode — LLM-powered one-liner summaries
nodes = describe_nodes(improve_workflow(), use_llm=True)

for node in nodes:
    print(f"{node['id']:20s} {node['type']:16s} {node['description']}")
```

Example output:

```
study                Study            factory study {project_path}
researcher           Agent(researcher) Deep research for the project...
gate_research        Gate(agent)       Are observations grounded in data?...
strategist           Agent(strategist) Generate prioritized hypotheses...
...
```

### API

| Function | Purpose |
|----------|---------|
| `describe_nodes(wf, *, use_llm=False)` | List all nodes with ID, type, and description (topo-sorted). Fast extraction by default; `use_llm=True` for LLM-powered rich summaries |
| `compose_serial(w1, w2, ...)` | Chain two workflows end-to-end |
| `trim_nodes(wf, node_ids)` | Remove nodes and reconnect (linear only) |
| `prefix_nodes(wf, prefix)` | Namespace all node IDs with a prefix |
| `find_terminal_nodes(wf)` | List nodes with no unconditional outgoing edges |
| `validate_composition(wf)` | Run graph + composition-specific validation |

### Example: chaining discover → review

```python
from factory.workflow.composition import compose_serial
from factory.workflow.definitions import discover_workflow, review_workflow

def discover_then_review() -> Workflow:
    return compose_serial(
        discover_workflow(),
        review_workflow(),
        end_node_w1="redetect",
        name="discover-then-review",
    )
```

### Example: extracting and trimming

```python
from factory.workflow.composition import trim_nodes

wf = improve_workflow()
# Extract the QA subgraph
qa = wf.subgraph(
    {"health_checker", "code_reviewer", "gate_review",
     "adversarial_tester", "gate_qa"},
    name="qa-only",
    start_node="health_checker",
)
```

### ID conflict resolution

When composing workflows with overlapping node IDs, provide an explicit
`rename` dict:

```python
composed = compose_serial(
    discover_workflow(),
    discover_workflow(),
    end_node_w1="redetect",
    name="double-discover",
    rename={"discover": "discover2", "gate_discover": "gate_discover2",
            "redetect": "redetect2"},
)
```

Without a rename dict, `compose_serial` raises `ValueError` listing conflicts.

### Constraints

- **trim_nodes**: MVP supports linear nodes only (1 unconditional in-edge,
  1 unconditional out-edge). Raises `ValueError` for GateNodes with conditional
  edges, ForkNodes, JoinNodes, or nodes whose writes are read by downstream nodes.
- **compose_parallel**: Deferred to future work.
- All composition functions return new `Workflow` objects (immutable pattern)
  and call `validate_graph()` before returning. `describe_nodes` is a read-only
  introspection function returning `list[dict]`, not a `Workflow`.

## Validation

The graph validator (`validation.py`) checks:
- Start node exists in the node set
- All edge sources and targets reference existing nodes
- All nodes are reachable from the start node
- Cycles only pass through GateNodes with RELOOP edges
- Fork targets match their ForkNode's target list
- Join sources match their JoinNode's source list
- Every file a node reads is written by a predecessor (data flow integrity)

Run validation:

```bash
factory workflow validate              # All 8 workflows
python -c "from factory.workflow.definitions import register_all
for name, wf in register_all().items():
    issues = wf.validate_graph()
    print(f'{name}: {\"CLEAN\" if not issues else issues}')"
```

## CLI commands

```bash
# Run a workflow (headless, deterministic graph execution)
factory workflow run improve --project /path/to/project
factory workflow run build --project /path/to/project --dry-run

# List all registered workflows
factory workflow list

# Show a workflow's structure (nodes, edges, triggers)
factory workflow show improve

# Validate all workflow graphs
factory workflow validate

# Regenerate SKILL.md files from graph definitions
factory workflow export-skills
```

## Launching the factory

### Interactive mode (CEO + skills)

```bash
# Improve an existing project
factory ceo /path/to/project

# Build from an idea — brainstorm first
factory ceo "a weather CLI in Rust" --mode design

# Build directly (clear spec)
factory ceo "a weather CLI in Rust"

# Focus on one thing
factory ceo /path/to/project --focus "add auth"
factory ceo /path/to/project --focus 42  # GitHub issue number

# Research-driven optimization
factory ceo "SWE-bench solver" --mode research

# Self-improve the factory
factory ceo /path/to/factory --mode meta

# Quick refinement
factory ceo /path/to/project --refine "fix the login bug"
```

What happens under the hood:
1. `cmd_ceo()` resolves path, mode, focus directives
2. Creates a git worktree for isolation
3. Builds a task string describing what the CEO should do
4. Resolves the CEO system prompt from `factory/agents/prompts/ceo.md`
5. Launches `claude` (or another runner) with the CEO prompt + task
6. The CEO detects project state → reads the matching `skills/workflow-*/SKILL.md` → follows it step by step, spawning specialist agents via `factory agent <role>`
7. On exit, the worktree is cleaned up

### Headless mode (graph executor)

```bash
# Direct graph execution — no CEO agent, the executor walks the DAG
factory workflow run improve --project /path/to/project

# With dry-run (no actual agent spawns or commands)
factory workflow run build --project /path/to/project --dry-run

# Headless CEO (pipe mode — for scripting, cron, tmux)
factory ceo /path/to/project --headless
factory run /path/to/project --loop --interval 1800
```

### Continuous loop

```bash
# Heartbeat loop — run improve every 30 minutes
factory run /path/to/project --loop --interval 1800

# In a detached tmux session
factory tmux /path/to/project --loop
```

## File layout

```
factory/workflow/
├── __init__.py          # Public API: re-exports all primitives + executor
├── primitives.py        # Pydantic models: Node types, Edge, Verdict, Workflow
├── definitions.py       # 8 workflow functions returning Workflow objects
├── composition.py       # Composition: compose_serial, trim_nodes, describe_nodes, helpers
├── executor.py          # WorkflowExecutor — async graph walker
├── validation.py        # NetworkX-based graph validator
├── events.py            # Structured event types for .factory/events.jsonl
├── skill_export.py      # Graph → SKILL.md converter
└── cli.py               # CLI subcommands: run, list, show, validate, export-skills

skills/
├── workflow-build/SKILL.md       # Auto-generated from build_workflow()
├── workflow-design/SKILL.md      # Auto-generated from design_workflow()
├── workflow-discover/SKILL.md    # Auto-generated from discover_workflow()
├── workflow-improve/SKILL.md     # Auto-generated from improve_workflow()
├── workflow-meta/SKILL.md        # Auto-generated from meta_workflow()
├── workflow-refine/SKILL.md      # Auto-generated from refine_workflow()
├── workflow-research/SKILL.md    # Auto-generated from research_workflow()
└── workflow-review/SKILL.md      # Auto-generated from review_workflow()
```

## Agent pool

The default agent pool maps roles to models:

| Role | Model | Purpose |
|------|-------|---------|
| researcher | sonnet | Web research + local analysis |
| strategist | opus | Hypothesis generation |
| builder | opus | Code implementation |
| qa | opus | Health check + code review + adversarial QA |
| failure_analyst | opus | Research mode failure classification |
| ceo | opus | Orchestration + gate evaluation |
| archivist | haiku | Fast, cheap summarization |
| refiner | opus | Refinement scoping |

Configured in `DEFAULT_AGENT_POOL` in `primitives.py`. Override per-node with `AgentNode(model="sonnet")`.
