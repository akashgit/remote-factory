# Package Ecosystem: Composable Workflow Packages for Remote Factory

*Design proposal -- August 2026*

## Implementation Status

Phases 1-2 of the migration path (Section 6) are implemented in `factory/workflow/package.py`. Phase 3 is partially implemented.

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1: Package primitive | **Complete** | `Package`, `Port`, `StateContract`, `OptKnob`, `MemoryDeclaration` |
| Phase 2: Composition operators | **Complete** | `Sequential`, `Parallel`, `Conditional`, `Loop`, `compile()` |
| Phase 3: Optimization integration | **Partial** | `KNOB_MUTATE` operator, `knob_values`/`knob_bounds`/`knob_expandable` on `Workflow`, `compute_features` includes knob values, `default_knob_expander` for expandable knobs. `save()`/`Package.load()` not yet implemented. |
| Phase 4: Registry | Not started | |
| Phase 5: Architect agent | Not started | |

**Model drifts from this design doc:**
- `OptKnob.bounds` is `list[str | float]` (not `tuple`). Added `expandable: bool` and `expansion_hint: str` fields.
- `StateContract.requires`/`produces` use `frozenset[str]` (not `set[str]`).
- `MemoryDeclaration.schema` renamed to `schema_def` to avoid shadowing the Pydantic `schema` method.

## 1. Vision

Remote factory today defines workflow modes as hand-wired DAGs of typed nodes. Each mode encodes a fixed topology: build, design, improve, research. This works, but the knowledge embedded in a workflow -- which agents to call, in what order, with what review gates -- is trapped inside monolithic Python functions. A team that builds a factory optimized for drug simulation cannot share their topology with a team doing compiler fuzzing without copy-pasting hundreds of lines of graph definitions.

The next step is to make the workflow subgraph a first-class, publishable, composable primitive: the **Package**. A Package is to a factory what `nn.Module` is to a neural network. It declares typed inputs and outputs, hides its internal graph, exposes tunable parameters, and composes with other Packages through standard operators. A drug-research Package might produce scored compound candidates; a molecular-simulation Package might consume those candidates and produce binding affinity predictions. Wiring them together is a single composition call, and the outer loop can optimize the joint pipeline end-to-end.

This draws on the lesson from PyTorch and TensorFlow: the frameworks that won were the ones that made the unit of reuse beautiful. Not the ones with the best runtime, but the ones where `nn.Sequential(encoder, bottleneck, decoder)` read like a sentence. We want `Sequential(deep_research, drug_sim, qa_review)` to do the same for agentic workflows.

## 2. The Package Primitive

A Package wraps a subgraph behind a typed interface. It is the unit of publishing, composition, and optimization.

```python
class Port(BaseModel):
    """A named artifact slot -- the data plane of a package interface."""
    name: str                           # e.g. "research_summary"
    artifact_path: str                  # e.g. ".factory/strategy/research.md"
    media_type: str = "text/markdown"   # content type hint

class StateContract(BaseModel):
    """Preconditions and postconditions on project state -- the control plane."""
    requires: set[str] = set()          # e.g. {"config_exists", "eval_profile_reviewed"}
    produces: set[str] = set()          # e.g. {"strategy_complete", "research_complete"}
    capabilities: list[str] = []        # semantic tags: ["deep-research", "python-eval"]

class OptKnob(BaseModel):
    """A parameter the outer loop is allowed to mutate."""
    name: str
    kind: Literal["prompt", "model", "threshold", "topology"]
    node_id: str                        # which internal node it belongs to
    default: str | float
    bounds: tuple[str | float, str | float] | list[str] = ()

class MemoryDeclaration(BaseModel):
    """Declares what a package persists and how it should be stored."""
    namespace: str                      # scoping key, e.g. "deep-research"
    kind: Literal["kv", "vector", "graph", "log"]
    schema: dict = {}                   # what gets stored (field names, types)
    retention: Literal["ephemeral", "run", "persistent"] = "persistent"
    # ephemeral: discarded after each node execution
    # run: preserved within a single factory run, discarded after
    # persistent: survives across runs (learned patterns, calibration)

class Package(BaseModel):
    """A composable workflow subgraph with a typed interface."""

    name: str
    version: str                        # semver
    description: str = ""

    # -- data interface --
    inputs: list[Port]                  # artifact slots this package reads
    outputs: list[Port]                 # artifact slots this package writes

    # -- state contract --
    contract: StateContract = StateContract()  # preconditions, postconditions, capabilities

    # -- internals --
    graph: Workflow                     # the subgraph (nodes + edges)
    entry_node: str                     # first node in the subgraph
    exit_node: str                      # last node (output handoff)

    # -- optimization surface --
    knobs: list[OptKnob] = []           # tunable parameters
    frozen: bool = False                # if True, outer loop cannot mutate internals

    # -- memory --
    memory: list[MemoryDeclaration] = []  # what this package persists
    # The runtime maps declarations to backends: files for local dev,
    # vector DB for semantic retrieval, graph DB for knowledge graphs.
    # Packages declare *what* they store; the runtime decides *where*.
```

### Example: a deep-research package

```python
deep_research = Package(
    name="deep-research",
    version="1.0.0",
    description="Three parallel researchers with contrastive synthesis",
    inputs=[
        Port(name="project_context", artifact_path=".factory/strategy/observations.md"),
    ],
    outputs=[
        Port(name="research_summary", artifact_path=".factory/strategy/research-combined.md"),
    ],
    contract=StateContract(
        requires={"observations_exist"},
        produces={"research_complete"},
        capabilities=["research", "literature-review", "contrastive-synthesis"],
    ),
    graph=_build_research_graph(),   # fork_research -> [3 researchers] -> join -> synthesize
    entry_node="fork_research",
    exit_node="synthesize_research",
    knobs=[
        OptKnob(name="researcher_count", kind="topology", node_id="fork_research",
                default=3, bounds=(1, 7)),
        OptKnob(name="model", kind="model", node_id="researcher_similar",
                default="sonnet", bounds=["haiku", "sonnet", "opus"]),
        OptKnob(name="synthesis_prompt", kind="prompt", node_id="synthesize_research",
                default="Synthesize the research findings into...",
                bounds=("", "")),  # free-form
    ],
    memory=[
        MemoryDeclaration(
            namespace="deep-research",
            kind="vector",
            schema={"finding": "str", "source": "str", "relevance": "float"},
            retention="persistent",
        ),
    ],
)
```

The internal graph is a standard `Workflow` -- the same nodes and edges the executor already knows how to run. The Package just adds the interface layer on top.

## 3. System Architecture

The package ecosystem decomposes into eight components. Each is independently developable and testable.

### 3.1 Core Engine

The graph traversal and validation layer. Resolves `Package` compositions into flat `Workflow` DAGs, validates port compatibility between connected packages, checks `StateContract` preconditions before execution, and tracks postcondition fulfillment as nodes complete. The engine is purely structural -- it decides *what* runs in *what order*, but never spawns a process or touches the filesystem.

Key responsibilities:
- Flatten nested compositions (Sequential/Parallel/Conditional/Loop) into executable DAGs
- Validate port wiring: output artifact paths of package A match input artifact paths of package B
- Insert adapter nodes when ports are structurally compatible but use different paths
- Verify `StateContract.requires` are satisfied before entering a package
- Mark `StateContract.produces` as fulfilled when a package's exit node completes

### 3.2 Runtime

The execution layer that actually runs nodes. Spawns agent subprocesses via the runner protocol, manages worktree isolation, handles credential forwarding, enforces timeouts, and collects output artifacts. The runtime is the boundary between the graph abstraction and the real world.

Key responsibilities:
- Agent invocation (currently `ClaudeRunner`, extensible via the runner protocol)
- Shell command execution for `FnNode`s
- Worktree lifecycle (create, sync, cleanup)
- Parallel execution of forked branches
- Event emission (`node.started`, `node.completed`, `node.failed`)

The runtime receives a flat DAG from the core engine -- it has no knowledge of packages or composition. This separation means the runtime doesn't change when the composition model evolves.

### 3.3 Memory Runtime

A pluggable storage layer that fulfills `MemoryDeclaration`s from packages. Each declaration specifies a `kind` (kv, vector, graph, log) and the memory runtime maps it to a concrete backend.

Default backends:
- `kv` -- JSON files in `.factory/packages/<namespace>/`
- `vector` -- ChromaDB (local) or a hosted vector service
- `graph` -- NetworkX serialized to JSON (local) or a graph DB
- `log` -- Append-only JSONL files

Configuration in `~/.factory/config.toml`:

```toml
[memory]
vector_backend = "chroma"        # or "qdrant", "pinecone"
graph_backend = "networkx"       # or "neo4j"
kv_backend = "json"              # or "sqlite", "redis"
```

The memory runtime handles namespace isolation when multiple instances of the same package run in parallel, scoping by run ID or branch name. It also enforces `retention` -- ephemeral state is cleaned after node execution, run-scoped state after the factory run completes.

### 3.4 Standard Library

The built-in packages that ship with factory, extracted from today's monolithic workflow definitions. These are the building blocks most compositions start from.

| Package | Current source | Capabilities |
|---------|---------------|-------------|
| `study` | `_study_subgraph()` | codebase-analysis, observation |
| `deep-research` | `_research_subgraph()` | research, parallel-search |
| `build` | builder + precheck nodes | code-generation, implementation |
| `deep-qa` | `_deep_qa_subgraph()` | health-check, code-review, adversarial-qa |
| `archive` | archivist node | record-keeping, knowledge-persistence |
| `bootstrap` | discover + factory_init | project-initialization |
| `strategy` | strategist + gate nodes | planning, hypothesis-generation |

Also includes the composition operators (`Sequential`, `Parallel`, `Conditional`, `Loop`) and standard join strategies (`CONCATENATE`, `BEST_SCORE`, `VOTE`, `ALL_MUST_PASS`, `WEIGHTED_MERGE`).

Today's monolithic workflow definitions become thin compositions:

```python
def design_workflow():
    return Sequential(
        Conditional(gate_has_factory, {
            "HALT": Sequential(bootstrap_pkg),
            "PROCEED": identity,
        }),
        study_pkg,
        deep_research_pkg,
        strategy_pkg.with_gate(evaluator_type="user"),
        Loop(
            body=Sequential(build_pkg, deep_qa_pkg),
            gate=precheck_gate,
        ),
        archive_pkg,
    ).as_workflow(name="design", terminal=True)
```

### 3.5 CLI

The user-facing command surface. Extends the existing `factory` CLI with package ecosystem commands:

```bash
# Package management
factory pkg search --capability "drug-simulation"
factory pkg install deep-research@1.2.0
factory pkg publish .                        # publish from current directory
factory pkg info deep-research               # show ports, contract, knobs

# Composition
factory compose show design                  # visualize a composition as a DAG
factory compose validate my-pipeline.py      # check port and contract compatibility

# Architect
factory ceo "drug discovery pipeline" --mode architect
```

Existing commands (`factory ceo`, `factory run`, `factory workflow`) continue to work unchanged. The CLI is a thin layer over the other components.

### 3.6 Package Registry

The discovery and distribution layer. Registries are Git repositories with a known structure (like Homebrew taps or Helm chart repos). Each registry contains package manifests, graph definitions, and a searchable index.

Public and private registries coexist, searched in priority order:

```toml
[[registries]]
name = "public"
url = "https://github.com/akashgit/factory-packages"
priority = 10

[[registries]]
name = "internal"
url = "https://github.com/our-org/factory-packages"
priority = 20   # searched first
```

Key responsibilities:
- Package manifest format (`package.toml`) with interface declarations
- Semantic capability matching -- the index includes capability embeddings so "molecular dynamics" matches a query for "drug simulation"
- Semver versioning with port-aware compatibility rules (port additions are minor; removals are major)
- Dependency resolution when packages declare `requires_packages`

### 3.7 Architect Agent

A factory mode (`--mode architect`) that automates composition. Takes a user goal and produces a runnable factory.

Pipeline:

1. **Goal decomposition.** The Architect agent breaks the user's goal into capability requirements: "Build a drug discovery pipeline" becomes `[research, molecular-simulation, scoring, qa-review]`.
2. **Registry search.** For each required capability, query all registries. Rank candidates by version, download count, compatibility score, and semantic similarity to the goal.
3. **Port matching.** Validate that candidate packages can wire together. Propose adapter nodes where ports are structurally compatible but use different artifact paths.
4. **Composition proposal.** Propose a composed factory using Sequential/Parallel/Conditional operators. The user reviews the composition graph (visualized as a DAG) before running.
5. **Handoff.** Once approved, the composition is either executed directly or handed to the optimizer for evolutionary tuning.

```bash
factory ceo "drug discovery pipeline targeting EGFR inhibitors" --mode architect
# Architect searches registries, proposes:
#   Sequential(deep-research, Parallel(docking-sim, md-sim), scoring, qa-review)
# User approves -> run or optimize
```

The Architect is itself a Package -- self-hosting the abstraction.

### 3.8 Optimizer

The outer loop, extended to understand package boundaries. Evolves composed factories by mutating topology and tuning knobs, while respecting package encapsulation.

#### The three-representation model

Graphs exist in three forms, and the source of truth shifts depending on the phase:

**Author-time: Python compositions.** Developers write readable, version-controlled Python. `Sequential(research_pkg, build_pkg, qa_pkg)` reads like a sentence. This is the authoring format -- optimized for humans.

**Optimize-time: mutable graph IR.** When the outer loop runs, `compile()` lowers the Python composition into a mutable intermediate representation -- a flat `Workflow` with fully resolved nodes, edges, and annotated `OptKnob` references back to their source packages. The optimizer mutates the IR directly. It never touches Python source code.

**Distribution-time: serialized packages.** When optimization converges, the best IR is promoted via `save()` into a serialized package (`package.toml` + `graph.json`). This is a new publishable, composable Package that can be loaded and wired into larger compositions.

```python
# 1. Author writes a composition in Python
pipeline = Sequential(research_pkg, build_pkg, qa_pkg)

# 2. compile() lowers to mutable IR
ir = pipeline.compile()
# ir is a Workflow with resolved nodes, edges, and
# OptKnob annotations tracing back to source packages

# 3. Optimizer mutates the IR (never touches Python source)
best_ir = outer_loop.evolve(ir, objective=score_fn)

# 4. Best result is serialized as a distributable package
best_ir.save("optimized-pipeline/")
# Produces package.toml + graph.json

# 5. The optimized package can be loaded and composed further
optimized = Package.load("optimized-pipeline/")
bigger_pipeline = Sequential(optimized, scoring_pkg)
```

This draws on the same lesson as PyTorch's evolution: early TensorFlow used static graphs (great for optimization, bad for authoring). PyTorch won with eager Python. Then `torch.compile` bridged both -- write eager code, compile to an optimized graph when needed. The factory equivalent is: write compositions in Python, compile to mutable IR for optimization, serialize the result for distribution.

#### Package-aware mutation operators

| Operator | What it does | Respects `frozen`? |
|----------|-------------|-------------------|
| `KNOB_MUTATE` | Adjust an `OptKnob` within its bounds | Yes -- knobs are always tunable unless the whole package is frozen |
| `PACKAGE_SWAP` | Replace a package with a registry alternative that has compatible ports | Yes -- frozen packages cannot be swapped |
| `TOPOLOGY_MUTATE` | Reorder, parallelize, or serialize packages in the composition | Yes -- frozen packages stay in place |
| `PACKAGE_INSERT` | Add a new package from the registry into the pipeline | N/A |
| `PACKAGE_REMOVE` | Remove a non-essential package | Yes |

The optimizer searches over the joint space of knob values and topology structure, using the same MAP-Elites quality-diversity framework from the current outer loop. Each candidate is a compiled IR; each evaluation is a full run against the user's objective. The `frozen` flag on a Package constrains which regions of the IR the optimizer can touch -- frozen internals are read-only, but frozen packages' knobs remain tunable unless explicitly locked.

## 4. Composition Model

Four composition operators, mirroring the patterns neural network frameworks settled on.

### Sequential

Output ports of one package connect to input ports of the next. The compositor auto-generates bridge edges between exit_node of package A and entry_node of package B, validating that output artifact paths match input artifact paths (or inserting adapter nodes when they don't).

```python
pipeline = Sequential(
    study_pkg,           # produces observations.md
    deep_research_pkg,   # consumes observations.md, produces research-combined.md
    strategy_pkg,        # consumes research-combined.md, produces current.md
    build_pkg,           # consumes current.md, produces code changes
)
```

### Parallel

Fork into N packages, each receiving the same inputs. A join strategy selects or merges outputs.

```python
ensemble = Parallel(
    deep_research_pkg,
    web_search_pkg,
    arxiv_search_pkg,
    join=JoinStrategy.CONCATENATE,  # or BEST_SCORE, VOTE, CUSTOM
)
```

This lowers to a ForkNode -> [package_0, package_1, package_2] -> JoinNode structure in the underlying graph, using existing executor primitives.

### Conditional

A gate inspects state and routes to one of several packages.

```python
router = Conditional(
    gate=GateNode(evaluator_command='check_project_language'),
    branches={
        "python": python_qa_pkg,
        "rust": rust_qa_pkg,
        "default": generic_qa_pkg,
    },
)
```

### Nested

Packages contain packages. A "design-mode" package is itself a composition of study, research, strategy, build, and QA packages:

```python
design_mode = Sequential(
    Conditional(
        gate=gate_has_factory,
        branches={
            "HALT": Sequential(discover_pkg, bootstrap_pkg),
            "PROCEED": identity,
        },
    ),
    study_pkg,
    deep_research_pkg,
    strategy_pkg,
    build_pkg,
    Parallel(health_check_pkg, code_review_pkg, adversarial_qa_pkg,
             join=JoinStrategy.ALL_MUST_PASS),
    archive_pkg,
)
```

### Concrete scenario: drug research + simulation

```python
drug_factory = Sequential(
    # Phase 1: deep research on target proteins and existing literature
    deep_research_pkg.configure(
        knobs={"synthesis_prompt": "Focus on binding site geometry and known inhibitors"}
    ),
    # Phase 2: parallel simulation of top candidates
    Parallel(
        molecular_dynamics_pkg,
        docking_sim_pkg,
        pharmacokinetics_pkg,
        join=JoinStrategy.WEIGHTED_MERGE,
    ),
    # Phase 3: review and score
    Sequential(
        scoring_pkg,
        qa_review_pkg,
    ),
)
```

The outer loop can evolve this entire composition: swapping `docking_sim_pkg` for an alternative from the registry, adjusting `researcher_count` in the research phase, or parallelizing the scoring step. Frozen packages hold steady while the topology around them mutates.

## 5. Package Manifest

Each published package includes a `package.toml` for registry distribution:

```toml
[package]
name = "deep-research"
version = "1.2.0"
description = "Three parallel researchers with contrastive synthesis"
authors = ["team@example.com"]
license = "Apache-2.0"
repository = "https://github.com/org/deep-research-pkg"

[interface]
capabilities = ["research", "literature-review", "contrastive-synthesis"]

[[interface.inputs]]
name = "project_context"
artifact_path = ".factory/strategy/observations.md"

[[interface.outputs]]
name = "research_summary"
artifact_path = ".factory/strategy/research-combined.md"

[[memory]]
namespace = "deep-research"
kind = "vector"
retention = "persistent"

[compatibility]
factory_version = ">=0.8.0"
requires_packages = []
```

## 6. Migration Path

### Phase 1: Package primitive (no registry)

Add the `Package` model to `factory/workflow/primitives.py`. Refactor `_study_subgraph()`, `_deep_qa_subgraph()`, and `_research_subgraph()` into Package instances. The executor gains a `PackageNode` type that delegates to a sub-executor. Existing `Workflow` definitions continue to work unchanged.

### Phase 2: Composition operators and compile()

Add `Sequential`, `Parallel`, `Conditional`, `Loop` to `factory/compose.py`. Each operator takes Package instances and returns a new Package. Add `compile()` to lower a composition into a flat mutable `Workflow` IR. Rewrite one workflow definition (e.g., `design_workflow`) as a composition to validate the abstraction. Both old and new definition styles coexist.

### Phase 3: Optimization integration

Wire `compile()` into the outer loop. The optimizer receives IR, mutates it, and evaluates candidates. Add `save()` to serialize a mutated IR back into a distributable package (`package.toml` + `graph.json`). Add `Package.load()` to round-trip saved packages back into composable units. This closes the author -> compile -> optimize -> save -> reload loop.

### Phase 4: Registry

Implement the registry client (`factory/registry/`). Add `factory pkg publish`, `factory pkg search`, `factory pkg install` commands. The public registry is a GitHub repo with package manifests and graph JSON. Private registries use the same format. Optimized packages produced by the outer loop can be published directly.

### Phase 5: Architect agent

Add `--mode architect` backed by a workflow that decomposes goals, searches registries, proposes compositions, and hands off to the optimizer. This is itself a Package (self-hosting the abstraction).

## 7. Open Questions

1. **Port type system.** Are artifact paths sufficient as port types, or do we need a richer schema (JSON Schema for structured outputs, MIME types for content)? Richer types enable better auto-matching but increase the packaging burden.

2. **Memory isolation.** When two instances of the same package run in parallel (different branches), how is their memory namespace scoped? Per-instance IDs? Content-addressed?

3. **Versioning and breaking changes.** If a package changes its output port, every downstream consumer breaks. Do we need an adapter registry, or is semver + capability matching enough?

4. **Trust model.** Public registry packages run arbitrary agent prompts and shell commands. What is the trust boundary? Code review on publish? Sandboxed execution? Reputation scores?

5. **Optimization scope.** When the outer loop mutates a composed factory, should it respect package boundaries (only tweak knobs) or be allowed to crack open packages and mutate their internals? The `frozen` flag is binary; a middle ground might be useful.

6. **Runtime cost model.** Packages that spawn expensive agents (Opus, long timeouts) should declare expected cost so the compositor and outer loop can budget. What does that declaration look like?

7. **Stateful vs. stateless packages.** Some packages benefit from memory across runs (learned review patterns, calibration data). Others should be pure functions. Should the package declare this, and should the executor enforce it?

8. **Graph serialization format.** Packages need a portable serialization (currently `Workflow.to_dict()` produces JSON). Should this become a standalone spec that non-Python clients could consume?
