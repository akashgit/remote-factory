#!/usr/bin/env python3
"""Package Ecosystem Demo — End-to-End Composability

Shows what Package composability unlocks, not just the API:

  1. Two teams independently build packages (security audit, perf optimizer)
  2. A third team composes them into a workflow neither team envisioned
  3. The optimizer searches over compositions and knobs to find the best one
  4. The winner is saved as a new distributable package

Run: uv run python examples/package_ecosystem_demo.py
"""

from __future__ import annotations

import json
import random
import hashlib

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


# ── terminal formatting ───────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
WHITE = "\033[97m"


def header(n: int, text: str) -> None:
    print(f"\n{'━' * 70}")
    print(f"  {BOLD}{CYAN}Part {n}:{RESET} {BOLD}{text}{RESET}")
    print(f"{'━' * 70}\n")


def show_package_card(pkg: Package, indent: int = 2) -> None:
    pad = " " * indent
    print(f"{pad}{BOLD}{MAGENTA}{pkg.name}{RESET} {DIM}v{pkg.version}{RESET}")
    if pkg.description:
        print(f"{pad}{DIM}{pkg.description}{RESET}")
    if pkg.inputs:
        paths = [p.artifact_path.split("/")[-1] for p in pkg.inputs]
        print(f"{pad}  {GREEN}in:{RESET}  {', '.join(paths)}")
    if pkg.outputs:
        paths = [p.artifact_path.split("/")[-1] for p in pkg.outputs]
        print(f"{pad}  {GREEN}out:{RESET} {', '.join(paths)}")
    if pkg.contract.capabilities:
        print(f"{pad}  {YELLOW}can:{RESET} {', '.join(pkg.contract.capabilities)}")
    if pkg.knobs:
        for k in pkg.knobs:
            bounds_str = f"  {DIM}bounds={k.bounds}{RESET}" if k.bounds else ""
            print(f"{pad}  {CYAN}{k.name}{RESET} = {k.default}{bounds_str}")
    print()


def show_dag(pkg: Package) -> None:
    wf = pkg.compile()
    edge_map: dict[str, list[tuple[str, str | None]]] = {}
    for e in wf.edges:
        edge_map.setdefault(e.source, []).append((e.target, e.condition.value if e.condition else None))

    visited: set[str] = set()
    queue = [wf.start_node]
    lines: list[str] = []
    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        node = wf.nodes[nid]
        ntype = type(node).__name__[:4]
        targets = edge_map.get(nid, [])
        if targets:
            arrows = []
            for tgt, cond in targets:
                if cond:
                    arrows.append(f"─{cond}─> {tgt}")
                else:
                    arrows.append(f"──> {tgt}")
            lines.append(f"    {DIM}[{ntype}]{RESET} {BOLD}{nid}{RESET}  {DIM}{'  '.join(arrows)}{RESET}")
        else:
            lines.append(f"    {DIM}[{ntype}]{RESET} {BOLD}{nid}{RESET}  {DIM}(end){RESET}")
        for e in wf.edges:
            if e.source == nid and e.target not in visited:
                queue.append(e.target)
    print("\n".join(lines))


def score_hash(data: str, base: float, variance: float) -> float:
    h = int(hashlib.md5(data.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return round(min(1.0, max(0.0, base + (h - 0.5) * variance)), 3)


# ── package definitions ───────────────────────────────────────────
# Imagine these are built by different teams, published independently.


def security_audit_pkg() -> Package:
    """Built by the security team. Runs static analysis + adversarial probing."""
    nodes = {
        "sast_scan": FnNode(
            id="sast_scan",
            command="semgrep --config auto {project_path} > {project_path}/.factory/reviews/sast.md",
            writes={".factory/reviews/sast.md"},
        ),
        "dependency_audit": FnNode(
            id="dependency_audit",
            command="pip-audit --format json > {project_path}/.factory/reviews/deps.md",
            writes={".factory/reviews/deps.md"},
        ),
        "security_reviewer": AgentNode(
            id="security_reviewer",
            role=AgentRole.CODE_REVIEWER,
            prompt_template=(
                "Review the SAST and dependency audit results. "
                "Classify each finding as critical/high/medium/low. "
                "Produce a prioritized remediation plan."
            ),
            reads={".factory/reviews/sast.md", ".factory/reviews/deps.md"},
            writes={".factory/reviews/security-review.md"},
        ),
        "fork_scans": ForkNode(id="fork_scans", targets=["sast_scan", "dependency_audit"]),
        "join_scans": JoinNode(
            id="join_scans",
            sources=["sast_scan", "dependency_audit"],
            reads={".factory/reviews/sast.md", ".factory/reviews/deps.md"},
        ),
    }
    edges = [
        Edge(source="fork_scans", target="join_scans"),
        Edge(source="join_scans", target="security_reviewer"),
    ]
    return Package(
        name="security-audit",
        version="2.1.0",
        description="SAST + dependency audit + agent-driven security review",
        inputs=[Port(name="codebase", artifact_path=".factory/strategy/observations.md")],
        outputs=[Port(name="security_review", artifact_path=".factory/reviews/security-review.md")],
        contract=StateContract(
            produces=frozenset({"security_reviewed"}),
            capabilities=["sast", "dependency-audit", "security-review"],
        ),
        graph=Workflow(name="security-audit", nodes=nodes, edges=edges, start_node="fork_scans"),
        entry_node="fork_scans",
        exit_node="security_reviewer",
        knobs=[
            OptKnob(name="reviewer_model", kind="model", node_id="security_reviewer",
                    default="opus", bounds=["sonnet", "opus"]),
            OptKnob(name="severity_threshold", kind="threshold", node_id="security_reviewer",
                    default="medium", bounds=["low", "medium", "high", "critical"]),
        ],
        memory=[
            MemoryDeclaration(namespace="security-audit", kind="kv",
                              schema_def={"cve": "str", "status": "str", "first_seen": "str"},
                              retention="persistent"),
        ],
    )


def perf_optimizer_pkg() -> Package:
    """Built by the platform team. Profiles + optimizes hot paths."""
    nodes = {
        "profiler": FnNode(
            id="profiler",
            command="py-spy record -o {project_path}/.factory/reviews/profile.md -- python -m pytest",
            writes={".factory/reviews/profile.md"},
        ),
        "perf_analyst": AgentNode(
            id="perf_analyst",
            role=AgentRole.RESEARCHER,
            prompt_template=(
                "Analyze the profiling data. Identify the top 3 hot paths. "
                "For each, propose a concrete optimization with expected speedup."
            ),
            reads={".factory/reviews/profile.md"},
            writes={".factory/reviews/perf-analysis.md"},
        ),
        "perf_builder": AgentNode(
            id="perf_builder",
            role=AgentRole.BUILDER,
            prompt_template="Implement the top optimization from the perf analysis.",
            reads={".factory/reviews/perf-analysis.md"},
            writes={".factory/reviews/perf-changes.md"},
        ),
    }
    edges = [
        Edge(source="profiler", target="perf_analyst"),
        Edge(source="perf_analyst", target="perf_builder"),
    ]
    return Package(
        name="perf-optimizer",
        version="1.3.0",
        description="Profile, analyze hot paths, implement optimizations",
        inputs=[Port(name="codebase", artifact_path=".factory/strategy/observations.md")],
        outputs=[Port(name="perf_changes", artifact_path=".factory/reviews/perf-changes.md")],
        contract=StateContract(
            produces=frozenset({"perf_optimized"}),
            capabilities=["profiling", "performance-analysis", "optimization"],
        ),
        graph=Workflow(name="perf-optimizer", nodes=nodes, edges=edges, start_node="profiler"),
        entry_node="profiler",
        exit_node="perf_builder",
        knobs=[
            OptKnob(name="analyst_model", kind="model", node_id="perf_analyst",
                    default="sonnet", bounds=["haiku", "sonnet", "opus"]),
            OptKnob(name="optimization_count", kind="threshold", node_id="perf_analyst",
                    default=3, bounds=[1, 5]),
        ],
    )


def study_pkg() -> Package:
    nodes = {
        "study": Study(
            id="study",
            command="factory study {project_path}",
            writes={".factory/strategy/observations.md"},
        ),
    }
    return Package(
        name="study",
        version="1.0.0",
        description="Codebase analysis and observation",
        outputs=[Port(name="observations", artifact_path=".factory/strategy/observations.md")],
        contract=StateContract(
            produces=frozenset({"study_complete"}),
            capabilities=["codebase-analysis"],
        ),
        graph=Workflow(name="study", nodes=nodes, edges=[], start_node="study"),
        entry_node="study",
        exit_node="study",
    )


def strategy_pkg() -> Package:
    node = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        reads={".factory/strategy/observations.md"},
        writes={".factory/strategy/current.md"},
    )
    return Package(
        name="strategy",
        version="1.0.0",
        description="Hypothesis generation and planning",
        inputs=[Port(name="observations", artifact_path=".factory/strategy/observations.md")],
        outputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        contract=StateContract(
            requires=frozenset({"study_complete"}),
            produces=frozenset({"strategy_complete"}),
            capabilities=["planning", "hypothesis-generation"],
        ),
        graph=Workflow(name="strategy", nodes={"strategist": node}, edges=[], start_node="strategist"),
        entry_node="strategist",
        exit_node="strategist",
    )


def build_pkg() -> Package:
    node = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )
    return Package(
        name="build",
        version="1.0.0",
        description="Code generation and implementation",
        inputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        outputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        contract=StateContract(
            requires=frozenset({"strategy_complete"}),
            produces=frozenset({"build_complete"}),
            capabilities=["code-generation"],
        ),
        graph=Workflow(name="build", nodes={"builder": node}, edges=[], start_node="builder"),
        entry_node="builder",
        exit_node="builder",
    )


def qa_pkg() -> Package:
    nodes = {
        "health_check": AgentNode(
            id="health_check", role=AgentRole.HEALTH_CHECKER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/health-check.md"},
        ),
        "code_review": AgentNode(
            id="code_review", role=AgentRole.CODE_REVIEWER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/code-review.md"},
        ),
        "fork_qa": ForkNode(id="fork_qa", targets=["health_check", "code_review"]),
        "join_qa": JoinNode(
            id="join_qa", sources=["health_check", "code_review"],
            reads={".factory/reviews/health-check.md", ".factory/reviews/code-review.md"},
        ),
    }
    edges = [Edge(source="fork_qa", target="join_qa")]
    return Package(
        name="qa",
        version="1.0.0",
        description="Health check + code review",
        inputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        outputs=[
            Port(name="health_check", artifact_path=".factory/reviews/health-check.md"),
            Port(name="code_review", artifact_path=".factory/reviews/code-review.md"),
        ],
        contract=StateContract(
            requires=frozenset({"build_complete"}),
            produces=frozenset({"qa_complete"}),
            capabilities=["health-check", "code-review"],
        ),
        graph=Workflow(name="qa", nodes=nodes, edges=edges, start_node="fork_qa"),
        entry_node="fork_qa",
        exit_node="join_qa",
    )


# ── the demo ──────────────────────────────────────────────────────


def main():
    # ── Part 1: Independent packages ──────────────────────────────

    header(1, "Two teams build packages independently")

    print("  The security team publishes a security audit package.")
    print("  The platform team publishes a performance optimizer.")
    print("  Neither knows about the other.\n")

    sec = security_audit_pkg()
    perf = perf_optimizer_pkg()

    show_package_card(sec)
    show_package_card(perf)

    # ── Part 2: Composition ───────────────────────────────────────

    header(2, "A third team composes them into something new")

    print("  Neither team built a 'hardened improvement pipeline.'")
    print("  But their packages compose into one:\n")

    study = study_pkg()
    strat = strategy_pkg()
    build = build_pkg()
    qa = qa_pkg()

    print(f"    {DIM}# Approach A: security before build (shift-left){RESET}")
    print(f"    {WHITE}pipeline_a = Sequential({RESET}")
    print(f"    {WHITE}    study, security_audit, perf_optimizer, strategy, build, qa{RESET}")
    print(f"    {WHITE}){RESET}\n")

    pipeline_a = Sequential(study, sec, perf, strat, build, qa, name="hardened-v1")

    print(f"    {DIM}# Approach B: security and perf in parallel after build{RESET}")
    print(f"    {WHITE}pipeline_b = Sequential({RESET}")
    print(f"    {WHITE}    study, strategy, build, Parallel(security_audit, perf_optimizer), qa{RESET}")
    print(f"    {WHITE}){RESET}\n")

    review_phase = Parallel(sec, perf, name="security-and-perf")
    pipeline_b = Sequential(study, strat, build, review_phase, qa, name="hardened-v2")

    print(f"    {DIM}# Approach C: iterative -- build + QA in a loop, then harden{RESET}")
    print(f"    {WHITE}pipeline_c = Sequential({RESET}")
    print(f"    {WHITE}    study, strategy,{RESET}")
    print(f"    {WHITE}    Loop(Sequential(build, qa), gate=precheck),{RESET}")
    print(f"    {WHITE}    Parallel(security_audit, perf_optimizer),{RESET}")
    print(f"    {WHITE}){RESET}\n")

    gate = GateNode(id="gate_precheck", evaluator_type="fn",
                    evaluator_command="factory precheck {project_path}")
    build_loop = Loop(Sequential(build, qa, name="build-verify"), gate, name="iterative-build")
    harden_phase = Parallel(sec, perf, name="harden")
    pipeline_c = Sequential(study, strat, build_loop, harden_phase, name="hardened-v3")

    # ── Part 3: Compile and compare ───────────────────────────────

    header(3, "Each composition compiles to a flat executable graph")

    for label, pipeline in [("A (shift-left)", pipeline_a),
                            ("B (parallel review)", pipeline_b),
                            ("C (loop + harden)", pipeline_c)]:
        wf = pipeline.compile()
        issues = wf.validate_graph()
        status = f"{GREEN}valid{RESET}" if not issues else f"{RED}invalid{RESET}"
        knob_count = len(pipeline.knobs)
        caps = len(pipeline.contract.capabilities)
        print(f"  {BOLD}{label}{RESET}: {len(wf.nodes)} nodes, {len(wf.edges)} edges, "
              f"{knob_count} knobs, {caps} capabilities  [{status}]")

    # ── Part 4: Optimization ──────────────────────────────────────

    header(4, "The optimizer searches over compositions and knobs")

    print("  Each composition exposes knobs from all constituent packages.")
    print("  The optimizer can tune them without touching source code.\n")

    candidates = [
        ("A", pipeline_a),
        ("B", pipeline_b),
        ("C", pipeline_c),
        ("A (opus security)", pipeline_a.configure(reviewer_model="opus")),
        ("B (haiku analyst)", pipeline_b.configure(analyst_model="haiku")),
        ("C (5 optimizations)", pipeline_c.configure(optimization_count=5)),
    ]

    print(f"  {'Candidate':<25} {'Nodes':>5} {'Knobs':>5}  {'Simulated Score':>15}")
    print(f"  {'─' * 55}")

    best_score = 0.0
    best_name = ""
    best_pipeline = candidates[0][1]

    for name, pipeline in candidates:
        wf = pipeline.compile()
        data = json.dumps(wf.to_dict(), sort_keys=True)
        knob_str = "|".join(f"{k.name}={k.default}" for k in pipeline.knobs)
        score = score_hash(data + knob_str, base=0.72, variance=0.25)
        marker = ""
        if score > best_score:
            best_score = score
            best_name = name
            best_pipeline = pipeline
            marker = f"  {YELLOW}<-- best so far{RESET}"
        print(f"  {name:<25} {len(wf.nodes):>5} {len(pipeline.knobs):>5}  {score:>15.3f}{marker}")

    print(f"\n  {GREEN}Winner: {BOLD}{best_name}{RESET}{GREEN} (score: {best_score:.3f}){RESET}")

    # ── Part 5: Save and distribute ───────────────────────────────

    header(5, "The winner becomes a distributable package")

    print("  The optimized composition is saved as a new package.")
    print("  Anyone can install it and compose it further.\n")

    wf = best_pipeline.compile()
    data = wf.to_dict()
    restored = Workflow.from_dict(data)

    print(f"  {WHITE}best.compile().to_dict()  ->  package.json{RESET}")
    print(f"  {WHITE}Workflow.from_dict(data)  ->  restored workflow{RESET}\n")

    print(f"  Round-trip: {GREEN}{'PASS' if len(restored.nodes) == len(wf.nodes) else 'FAIL'}{RESET}")
    print(f"  Serialized size: {len(json.dumps(data)):,} bytes")
    print(f"  Nodes preserved: {len(restored.nodes)}")
    print(f"  Edges preserved: {len(restored.edges)}")

    # Show what the saved package looks like
    print(f"\n  {DIM}The saved package can be loaded and extended:{RESET}\n")
    print(f"    {WHITE}hardened = Package.load('hardened-pipeline/'){RESET}")
    print(f"    {WHITE}with_monitoring = Sequential(hardened, monitoring_pkg){RESET}")
    print(f"    {WHITE}with_monitoring.compile()  # ready to run{RESET}")

    # ── Part 6: What this means ───────────────────────────────────

    header(6, "What composability gives you")

    print(f"""  {BOLD}Without packages:{RESET}
    The security team's work lives in a 200-line function.
    The platform team's work lives in another 200-line function.
    Combining them means copy-pasting nodes and hand-wiring edges.
    The optimizer can't search over alternative compositions.
    Sharing requires sharing source code.

  {BOLD}With packages:{RESET}
    {GREEN}Two teams publish independently.{RESET}
    {GREEN}A third team composes them in one line.{RESET}
    {GREEN}Three candidate topologies are tested in seconds.{RESET}
    {GREEN}The optimizer tunes knobs across package boundaries.{RESET}
    {GREEN}The winner is saved and shared as a single artifact.{RESET}

  The unit of reuse is no longer a code snippet.
  It's a typed, optimizable, composable workflow subgraph.
""")


if __name__ == "__main__":
    main()
