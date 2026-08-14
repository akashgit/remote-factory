# Factory Workflow DSL

Factory modes are directed graphs of typed domain nodes. The Factory DSL and validator remain the public API; LangGraph is the single execution runtime. `SKILL.md` export is documentation and a legacy fallback, not the authoritative scheduler.

## How it works

```
definitions.py          (Python functions returning Workflow objects)
       │
       ├──► compile_langgraph()  (direct DSL → StateGraph compiler)
       │            │
       │       LangGraph runtime
       │       SQLite checkpoints
       │            │
       │       ┌────┴────┐
       │       │         │
       │    headless   interactive CEO client
       │
       └──► skill_export.py     (Workflow → SKILL.md rendering)
```

`factory workflow run` executes the compiled graph directly. Interactive `factory ceo` uses `workflow tool next/submit/status` as a thin client over the same persisted graph thread. LangGraph owns routing, loops, fork/join scheduling, checkpoints, interrupts, and resume.

## Node types

Every workflow is a graph of typed nodes connected by edges:

| Node | Class | Purpose | Example |
|------|-------|---------|---------|
| Agent | `AgentNode` | Spawn a Claude Code specialist agent | Researcher, Builder, QA |
| Function | `FnNode` | Run a shell command | `factory eval {project_path}` |
| Gate | `GateNode` | Decision point producing PROCEED / RELOOP / HALT | CEO reviewing research quality |
| Fork | `ForkNode` | Launch multiple targets in parallel | 3 researchers simultaneously |
| Join | `JoinNode` | Barrier — wait for all parallel branches | Wait for all researchers |
| Study | `Study` | Distinguished `FnNode` wrapping `factory study` | Local codebase analysis |
| LLM | `LLMNode` | Run the in-process tool-use loop | Focused LLM transform |
| Subgraph fork | `SubgraphForkNode` | Run N isolated worktree subgraphs | Parallel experiments |
| Selection | `SelectionNode` | Select and merge an experiment winner | Best-score selection |

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

Built-in workflows are registered in `definitions.py`; user and project workflows are discovered through `WorkflowRegistry`:

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

**Legacy non-blocking annotation**:

```python
nodes["archivist"] = AgentNode(
    id="archivist", role=AgentRole.ARCHIVIST,
    model="haiku", blocking=False,
)
```

`blocking=False` is retained in the DSL for compatibility. LangGraph still runs the node durably; it is no longer launched as a cancellable in-process background task.

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
factory workflow validate              # All workflows
python -c "from factory.workflow.definitions import register_all
for name, wf in register_all().items():
    issues = wf.validate_graph()
    print(f'{name}: {\"CLEAN\" if not issues else issues}')"
```

## CLI commands

```bash
# Run through LangGraph + SQLite checkpointing
factory workflow run improve /path/to/project
factory workflow run build /path/to/project --dry-run

# Resume a durable user gate
factory workflow resume design /path/to/project \
  --thread-id <thread-id> --value PROCEED

# Resume multiple parallel interrupts by their emitted IDs
factory workflow resume improve /path/to/project \
  --thread-id <thread-id> \
  --resume-json '{"<interrupt-id-a>": "done", "<interrupt-id-b>": "done"}'

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

### Interactive mode (CEO as LangGraph client)

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
4. Compiles the selected Factory workflow and starts a persisted SQLite thread
5. Launches the CEO with the current graph task/interrupt protocol
6. CEO outputs resume the graph through `Command(resume=...)`
7. Completed worktrees are cleaned up; interrupted worktrees are preserved for resume

### Headless mode (graph executor)

```bash
# Direct graph execution — no CEO agent
factory workflow run improve /path/to/project

# With dry-run (no actual agent spawns or commands)
factory workflow run build /path/to/project --dry-run

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
├── __init__.py          # Public workflow API
├── primitives.py        # Pydantic models: Node types, Edge, Verdict, Workflow
├── definitions.py       # Built-in Workflow definitions
├── langgraph.py         # Direct StateGraph compiler + serializable state
├── executor.py          # Factory domain operations + LangGraph facade
├── tool.py              # Thin interactive adapters over graph threads
├── validation.py        # NetworkX-based graph validator
├── events.py            # Structured event types for .factory/events.jsonl
├── skill_export.py      # Workflow → SKILL.md renderer
└── cli.py               # CLI subcommands, including run/resume/tool

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
