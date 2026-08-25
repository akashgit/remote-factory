#!/usr/bin/env python3
"""Package Ecosystem Demo — Composable Workflow Subgraphs

Run: uv run python examples/package_ecosystem_demo.py
"""

from __future__ import annotations

import json
import textwrap

from factory.workflow.package import (
    Conditional,
    Loop,
    MemoryDeclaration,
    OptKnob,
    Package,
    Parallel,
    Port,
    Sequential,
    StateContract,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
    Workflow,
)


# ── pretty printing ────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"


def header(text: str) -> None:
    print(f"\n{'─' * 70}")
    print(f"{BOLD}{CYAN}{text}{RESET}")
    print(f"{'─' * 70}\n")


def subheader(text: str) -> None:
    print(f"\n  {BOLD}{WHITE}{text}{RESET}\n")


def info(label: str, value: str) -> None:
    print(f"  {DIM}{label}:{RESET} {value}")


def bullet(text: str) -> None:
    print(f"  {DIM}•{RESET} {text}")


def code_block(text: str) -> None:
    for line in text.strip().split("\n"):
        print(f"    {DIM}{line}{RESET}")


def show_package(pkg: Package, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{BOLD}{MAGENTA}{pkg.name}{RESET} v{pkg.version}")
    if pkg.inputs:
        print(f"{pad}  {GREEN}inputs:{RESET}  {', '.join(p.name for p in pkg.inputs)}")
    if pkg.outputs:
        print(f"{pad}  {GREEN}outputs:{RESET} {', '.join(p.name for p in pkg.outputs)}")
    if pkg.contract.capabilities:
        print(f"{pad}  {YELLOW}capabilities:{RESET} {', '.join(pkg.contract.capabilities)}")
    if pkg.knobs:
        knob_strs = [f"{k.name}={k.default}" for k in pkg.knobs]
        print(f"{pad}  {CYAN}knobs:{RESET} {', '.join(knob_strs)}")
    if pkg.memory:
        mem_strs = [f"{m.namespace}({m.kind})" for m in pkg.memory]
        print(f"{pad}  {DIM}memory:{RESET} {', '.join(mem_strs)}")


def show_graph(pkg: Package) -> None:
    wf = pkg.compile()
    edge_map: dict[str, list[str]] = {}
    for e in wf.edges:
        label = f" --{e.condition.value}-->" if e.condition else " ──>"
        edge_map.setdefault(e.source, []).append(f"{label} {e.target}")

    visited: set[str] = set()
    queue = [wf.start_node]
    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        node = wf.nodes[nid]
        ntype = type(node).__name__
        targets = edge_map.get(nid, [])
        target_str = "  ".join(targets) if targets else f"  {DIM}(terminal){RESET}"
        print(f"    {DIM}[{ntype}]{RESET} {BOLD}{nid}{RESET}{target_str}")
        for e in wf.edges:
            if e.source == nid and e.target not in visited:
                queue.append(e.target)


# ── standard library packages ─────────────────────────────────────


def make_study_pkg() -> Package:
    nodes = {
        "graph_update": FnNode(id="graph_update", command="factory graph update {project_path}", writes={"graph.json"}),
        "study": Study(id="study", command="factory study {project_path}", writes={".factory/strategy/observations.md"}),
        "graph_explorer": AgentNode(
            id="graph_explorer", role=AgentRole.RESEARCHER,
            prompt_template="Explore the code graph and extract architectural patterns.",
            reads={".factory/strategy/observations.md"}, writes={".factory/strategy/graph-context.md"},
        ),
        "concat_study": FnNode(
            id="concat_study",
            command="cat observations.md graph-context.md > study-combined.md",
            reads={".factory/strategy/observations.md", ".factory/strategy/graph-context.md"},
            writes={".factory/strategy/study-combined.md"},
        ),
    }
    edges = [
        Edge(source="graph_update", target="study"),
        Edge(source="study", target="graph_explorer"),
        Edge(source="graph_explorer", target="concat_study"),
    ]
    return Package(
        name="study", version="1.0.0",
        description="Graph-powered codebase analysis",
        outputs=[Port(name="study_combined", artifact_path=".factory/strategy/study-combined.md")],
        contract=StateContract(
            produces=frozenset({"study_complete"}),
            capabilities=["codebase-analysis", "observation"],
        ),
        graph=Workflow(name="study", nodes=nodes, edges=edges, start_node="graph_update"),
        entry_node="graph_update", exit_node="concat_study",
        knobs=[
            OptKnob(name="explorer_model", kind="model", node_id="graph_explorer",
                    default="sonnet", bounds=["haiku", "sonnet", "opus"]),
        ],
    )


def make_research_pkg() -> Package:
    nodes = {
        "r_similar": AgentNode(
            id="r_similar", role=AgentRole.RESEARCHER,
            prompt_template="Research similar projects and prior art.",
            reads={".factory/strategy/study-combined.md"},
            writes={".factory/strategy/research-similar.md"},
        ),
        "r_techstack": AgentNode(
            id="r_techstack", role=AgentRole.RESEARCHER,
            prompt_template="Research technology stack options and tradeoffs.",
            reads={".factory/strategy/study-combined.md"},
            writes={".factory/strategy/research-techstack.md"},
        ),
        "r_pitfalls": AgentNode(
            id="r_pitfalls", role=AgentRole.RESEARCHER,
            prompt_template="Research common pitfalls and failure modes.",
            reads={".factory/strategy/study-combined.md"},
            writes={".factory/strategy/research-pitfalls.md"},
        ),
        "fork_research": ForkNode(id="fork_research", targets=["r_similar", "r_techstack", "r_pitfalls"]),
        "join_research": JoinNode(
            id="join_research", sources=["r_similar", "r_techstack", "r_pitfalls"],
            reads={".factory/strategy/research-similar.md", ".factory/strategy/research-techstack.md",
                   ".factory/strategy/research-pitfalls.md"},
            writes={".factory/strategy/research-combined.md"},
        ),
    }
    edges = [Edge(source="fork_research", target="join_research")]
    return Package(
        name="deep-research", version="1.0.0",
        description="Three parallel researchers with contrastive synthesis",
        inputs=[Port(name="study_combined", artifact_path=".factory/strategy/study-combined.md")],
        outputs=[Port(name="research_combined", artifact_path=".factory/strategy/research-combined.md")],
        contract=StateContract(
            requires=frozenset({"study_complete"}),
            produces=frozenset({"research_complete"}),
            capabilities=["research", "literature-review", "parallel-search"],
        ),
        graph=Workflow(name="deep-research", nodes=nodes, edges=edges, start_node="fork_research"),
        entry_node="fork_research", exit_node="join_research",
        knobs=[
            OptKnob(name="researcher_model", kind="model", node_id="r_similar",
                    default="sonnet", bounds=["haiku", "sonnet", "opus"]),
        ],
        memory=[
            MemoryDeclaration(namespace="deep-research", kind="vector",
                              schema_def={"finding": "str", "source": "str", "relevance": "float"},
                              retention="persistent"),
        ],
    )


def make_strategy_pkg() -> Package:
    node = AgentNode(
        id="strategist", role=AgentRole.STRATEGIST,
        reads={".factory/strategy/research-combined.md"},
        writes={".factory/strategy/current.md"},
    )
    return Package(
        name="strategy", version="1.0.0",
        description="Hypothesis generation and planning",
        inputs=[Port(name="research", artifact_path=".factory/strategy/research-combined.md")],
        outputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        contract=StateContract(
            requires=frozenset({"research_complete"}),
            produces=frozenset({"strategy_complete"}),
            capabilities=["planning", "hypothesis-generation"],
        ),
        graph=Workflow(name="strategy", nodes={"strategist": node}, edges=[], start_node="strategist"),
        entry_node="strategist", exit_node="strategist",
    )


def make_build_pkg() -> Package:
    node = AgentNode(
        id="builder", role=AgentRole.BUILDER,
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )
    return Package(
        name="build", version="1.0.0",
        description="Code generation and implementation",
        inputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        outputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        contract=StateContract(
            requires=frozenset({"strategy_complete"}),
            produces=frozenset({"build_complete"}),
            capabilities=["code-generation", "implementation"],
        ),
        graph=Workflow(name="build", nodes={"builder": node}, edges=[], start_node="builder"),
        entry_node="builder", exit_node="builder",
    )


def make_qa_pkg() -> Package:
    nodes = {
        "health_checker": AgentNode(
            id="health_checker", role=AgentRole.HEALTH_CHECKER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/health-check.md"},
        ),
        "code_reviewer": AgentNode(
            id="code_reviewer", role=AgentRole.CODE_REVIEWER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/code-review.md"},
        ),
        "adversarial_tester": AgentNode(
            id="adversarial_tester", role=AgentRole.ADVERSARIAL_TESTER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/adversarial-qa.md"},
        ),
        "fork_qa": ForkNode(id="fork_qa", targets=["health_checker", "code_reviewer", "adversarial_tester"]),
        "join_qa": JoinNode(
            id="join_qa", sources=["health_checker", "code_reviewer", "adversarial_tester"],
            reads={".factory/reviews/health-check.md", ".factory/reviews/code-review.md",
                   ".factory/reviews/adversarial-qa.md"},
        ),
    }
    edges = [Edge(source="fork_qa", target="join_qa")]
    return Package(
        name="deep-qa", version="1.0.0",
        description="Parallel health check, code review, and adversarial QA",
        inputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        outputs=[
            Port(name="health_check", artifact_path=".factory/reviews/health-check.md"),
            Port(name="code_review", artifact_path=".factory/reviews/code-review.md"),
            Port(name="adversarial_qa", artifact_path=".factory/reviews/adversarial-qa.md"),
        ],
        contract=StateContract(
            requires=frozenset({"build_complete"}),
            produces=frozenset({"qa_complete"}),
            capabilities=["health-check", "code-review", "adversarial-qa"],
        ),
        graph=Workflow(name="deep-qa", nodes=nodes, edges=edges, start_node="fork_qa"),
        entry_node="fork_qa", exit_node="join_qa",
        knobs=[
            OptKnob(name="adversarial_timeout", kind="threshold", node_id="adversarial_tester",
                    default=1800, bounds=[600, 3600]),
        ],
    )


def make_archive_pkg() -> Package:
    node = AgentNode(
        id="archivist", role=AgentRole.ARCHIVIST, blocking=False,
        reads={".factory/reviews/health-check.md", ".factory/reviews/code-review.md"},
        writes={".factory/archive/latest.md"},
    )
    return Package(
        name="archive", version="1.0.0",
        description="Record experiment results and learnings",
        inputs=[Port(name="qa_results", artifact_path=".factory/reviews/health-check.md")],
        outputs=[Port(name="archive_entry", artifact_path=".factory/archive/latest.md")],
        contract=StateContract(
            requires=frozenset({"qa_complete"}),
            produces=frozenset({"archived"}),
            capabilities=["record-keeping", "knowledge-persistence"],
        ),
        graph=Workflow(name="archive", nodes={"archivist": node}, edges=[], start_node="archivist"),
        entry_node="archivist", exit_node="archivist",
        memory=[
            MemoryDeclaration(namespace="archive", kind="log",
                              schema_def={"experiment_id": "int", "outcome": "str", "score": "float"},
                              retention="persistent"),
        ],
    )


# ── demo scenarios ─────────────────────────────────────────────────


def demo_1_standard_library():
    header("1. THE STANDARD LIBRARY")
    print("  Factory ships with reusable packages extracted from today's workflows.")
    print("  Each package is a typed, composable unit — the nn.Module of agent workflows.\n")

    study = make_study_pkg()
    research = make_research_pkg()
    strategy = make_strategy_pkg()
    build = make_build_pkg()
    qa = make_qa_pkg()
    archive = make_archive_pkg()

    for pkg in [study, research, strategy, build, qa, archive]:
        show_package(pkg)
        print()


def demo_2_composition():
    header("2. COMPOSITION — Sequential(study, research, strategy, build, qa, archive)")
    print("  One line composes the full improve pipeline from 6 packages.\n")

    study = make_study_pkg()
    research = make_research_pkg()
    strategy = make_strategy_pkg()
    build = make_build_pkg()
    qa = make_qa_pkg()
    archive = make_archive_pkg()

    pipeline = Sequential(study, research, strategy, build, qa, archive, name="improve")

    subheader("Composed pipeline")
    show_package(pipeline)

    subheader("Compiled graph (20 nodes)")
    show_graph(pipeline)

    wf = pipeline.compile()
    issues = wf.validate_graph()
    print(f"\n  {GREEN}Graph validation: {'PASS' if not issues else 'FAIL'}{RESET}")
    info("Nodes", str(len(wf.nodes)))
    info("Edges", str(len(wf.edges)))


def demo_3_design_mode():
    header("3. DESIGN MODE — as a package composition")

    study = make_study_pkg()
    research = make_research_pkg()
    strategy = make_strategy_pkg()
    build = make_build_pkg()
    qa = make_qa_pkg()
    archive = make_archive_pkg()

    subheader("Before: 250 lines in definitions.py")
    code_block("""\
def design_workflow() -> Workflow:
    wf = build_workflow()
    wf.nodes["gate_has_factory"] = GateNode(...)
    wf.nodes["discover"] = FnNode(...)
    s_nodes, s_edges = _study_subgraph()
    wf.nodes.update(s_nodes)
    # ... 200 more lines of manual node wiring ...
    wf.edges.extend([
        Edge(source="gate_has_factory", target="graph_update", ...),
        Edge(source="gate_has_factory", target="discover", ...),
        Edge(source="discover", target="graph_update"),
        # ... 20 more edges ...
    ])
    return wf""")

    subheader("After: 10 lines of composition")
    code_block("""\
design = Sequential(
    study_pkg,
    research_pkg,
    strategy_pkg,
    build_pkg,
    qa_pkg,
    archive_pkg,
)""")

    pipeline = Sequential(study, research, strategy, build, qa, archive, name="design")
    wf = pipeline.compile()
    print(f"\n  {GREEN}Both produce the same executable graph: {len(wf.nodes)} nodes, {len(wf.edges)} edges{RESET}")


def demo_4_knobs_and_optimization():
    header("4. OPTIMIZATION SURFACE — knobs the outer loop can tune")

    study = make_study_pkg()
    research = make_research_pkg()
    strategy = make_strategy_pkg()
    build = make_build_pkg()
    qa = make_qa_pkg()
    archive = make_archive_pkg()

    pipeline = Sequential(study, research, strategy, build, qa, archive, name="improve")

    subheader("Declared knobs across the composition")
    for knob in pipeline.knobs:
        print(f"  {CYAN}{knob.name}{RESET}  ({knob.kind})  node={knob.node_id}  default={knob.default}  bounds={knob.bounds}")

    subheader("configure() overrides knob defaults")
    tuned_qa = qa.configure(adversarial_timeout=600)
    print(f"  Before: adversarial_timeout = {qa.knobs[0].default}")
    print(f"  After:  adversarial_timeout = {tuned_qa.knobs[0].default}")

    subheader("The three-representation model")
    print(f"  {WHITE}Author-time:{RESET}   pipeline = Sequential(study, research, build, qa)")
    print(f"  {WHITE}Optimize-time:{RESET} ir = pipeline.compile()  # mutable Workflow IR")
    print(f"  {WHITE}Distribute:{RESET}    ir.to_dict()             # JSON for registry")
    print()

    ir = pipeline.compile()
    data = ir.to_dict()
    restored = Workflow.from_dict(data)
    print(f"  Compile -> serialize -> restore round-trip: "
          f"{GREEN}{'PASS' if len(restored.nodes) == len(ir.nodes) else 'FAIL'}{RESET}")
    info("IR nodes", str(len(ir.nodes)))
    info("JSON size", f"{len(json.dumps(data)):,} bytes")


def demo_5_parallel_and_conditional():
    header("5. PARALLEL AND CONDITIONAL — first-class composition operators")

    study = make_study_pkg()
    research = make_research_pkg()

    subheader("Parallel: run packages concurrently")
    code_block("""\
ensemble = Parallel(
    research_pkg,
    web_search_pkg,
    arxiv_pkg,
)""")

    web_search = Package(
        name="web-search", version="1.0.0",
        inputs=[Port(name="query", artifact_path=".factory/strategy/study-combined.md")],
        outputs=[Port(name="results", artifact_path=".factory/strategy/web-results.md")],
        contract=StateContract(capabilities=["web-search"]),
        graph=Workflow(name="web-search",
                       nodes={"web_search": FnNode(id="web_search", command="search_web",
                                                   writes={".factory/strategy/web-results.md"})},
                       edges=[], start_node="web_search"),
        entry_node="web_search", exit_node="web_search",
    )

    ensemble = Parallel(research, web_search, name="research-ensemble")
    show_package(ensemble, indent=1)

    wf = ensemble.compile()
    print(f"\n    {GREEN}Compiles to: ForkNode -> [{len(wf.nodes) - 2} workers] -> JoinNode{RESET}")

    subheader("Conditional: route by project state")
    code_block("""\
router = Conditional(
    gate=GateNode(evaluator_command='detect_language'),
    branches={
        "PROCEED": python_qa_pkg,
        "HALT":    rust_qa_pkg,
    },
)""")

    qa = make_qa_pkg()
    gate = GateNode(id="gate_lang", evaluator_type="fn", evaluator_command="detect_language")
    lite_qa = Package(
        name="lite-qa", version="1.0.0",
        inputs=qa.inputs, outputs=qa.outputs,
        contract=qa.contract,
        graph=Workflow(name="lite-qa",
                       nodes={"lite_check": FnNode(id="lite_check", command="run_lint",
                                                   writes={".factory/reviews/health-check.md"})},
                       edges=[], start_node="lite_check"),
        entry_node="lite_check", exit_node="lite_check",
    )

    router = Conditional(gate, {"PROCEED": qa, "HALT": lite_qa}, name="qa-router")
    wf = router.compile()
    print(f"\n  {GREEN}Compiles to: GateNode -> [full QA | lite QA] -> join{RESET}")
    info("Total nodes", str(len(wf.nodes)))


def demo_6_nested_composition():
    header("6. NESTED — packages containing packages")
    print("  The real power: compositions are packages. They nest arbitrarily.\n")

    study = make_study_pkg()
    research = make_research_pkg()
    strategy = make_strategy_pkg()
    build = make_build_pkg()
    qa = make_qa_pkg()
    archive = make_archive_pkg()

    gate = GateNode(id="gate_precheck", evaluator_type="fn",
                    evaluator_command="factory precheck {project_path}")
    build_loop = Loop(
        Sequential(build, qa, name="build-and-verify"),
        gate,
        max_iterations=5,
        name="iterative-build",
    )

    design = Sequential(study, research, strategy, build_loop, archive, name="design-v2")

    subheader("Composition tree")
    code_block("""\
design-v2 = Sequential(
    study,
    deep-research,
    strategy,
    Loop(                          # <-- iterative build
        Sequential(build, deep-qa),
        gate=precheck,
        max_iterations=5,
    ),
    archive,
)""")

    subheader("Compiled to flat executable graph")
    show_graph(design)

    wf = design.compile()
    issues = wf.validate_graph()
    print(f"\n  {GREEN}Validation: {'PASS' if not issues else 'FAIL — ' + str(issues)}{RESET}")
    info("Total nodes", str(len(wf.nodes)))
    info("Total edges", str(len(wf.edges)))
    info("Knobs exposed", str(len(design.knobs)))
    info("Memory stores", str(len(design.memory)))

    subheader("State contract (auto-merged)")
    info("Requires", str(design.contract.requires) if design.contract.requires else "(nothing)")
    info("Produces", ", ".join(sorted(design.contract.produces)))
    info("Capabilities", ", ".join(design.contract.capabilities))


def demo_7_the_punchline():
    header("7. THE PUNCHLINE")

    print(textwrap.dedent("""\
      Today's factory defines workflows as 300-line Python functions
      that manually wire nodes and edges. Knowledge is trapped in code.

      With packages, the same workflows become declarative compositions:

        {bold}Sequential(study, research, strategy, build, qa, archive){reset}

      Each package is independently:
        • publishable to a registry
        • optimizable via knobs
        • swappable by the outer loop
        • composable into larger factories

      The optimizer never touches Python source. It mutates the compiled
      IR — the same graph the executor already runs. Author in Python,
      optimize in IR, distribute as packages.

      {cyan}nn.Module was to neural networks what Package is to agent workflows.{reset}
    """.format(bold=BOLD, reset=RESET, cyan=CYAN)))


if __name__ == "__main__":
    demo_1_standard_library()
    demo_2_composition()
    demo_3_design_mode()
    demo_4_knobs_and_optimization()
    demo_5_parallel_and_conditional()
    demo_6_nested_composition()
    demo_7_the_punchline()
