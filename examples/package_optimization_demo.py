#!/usr/bin/env python3
"""Package Optimization Demo — Real outer loop over composed packages.

Uses factory's actual mutation operators and structural fitness evaluation
to optimize a composed Package over multiple generations.

Run: uv run python examples/package_optimization_demo.py
"""

from __future__ import annotations

import copy
import json
import random
import time

from factory.outer_loop.mutations import mutate_params, mutate_prompt
from factory.outer_loop.similarity import compute_features, structural_hash
from factory.workflow.package import (
    OptKnob,
    Package,
    Parallel,
    Port,
    Sequential,
    StateContract,
    MemoryDeclaration,
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


def header(text: str) -> None:
    print(f"\n{'━' * 70}")
    print(f"  {BOLD}{CYAN}{text}{RESET}")
    print(f"{'━' * 70}")


def bar(value: float, width: int = 30) -> str:
    filled = int(value * width)
    return f"{GREEN}{'█' * filled}{DIM}{'░' * (width - filled)}{RESET}"


# ── packages (same as the E2E demo) ──────────────────────────────


def security_audit_pkg() -> Package:
    nodes = {
        "sast_scan": FnNode(id="sast_scan", command="semgrep --config auto {project_path}",
                            writes={".factory/reviews/sast.md"}),
        "dependency_audit": FnNode(id="dependency_audit", command="pip-audit --format json",
                                   writes={".factory/reviews/deps.md"}),
        "security_reviewer": AgentNode(
            id="security_reviewer", role=AgentRole.CODE_REVIEWER,
            prompt_template="Review SAST and dependency audit results. Classify findings by severity.",
            reads={".factory/reviews/sast.md", ".factory/reviews/deps.md"},
            writes={".factory/reviews/security-review.md"}, model="opus",
        ),
        "fork_scans": ForkNode(id="fork_scans", targets=["sast_scan", "dependency_audit"]),
        "join_scans": JoinNode(id="join_scans", sources=["sast_scan", "dependency_audit"],
                               reads={".factory/reviews/sast.md", ".factory/reviews/deps.md"}),
    }
    edges = [Edge(source="fork_scans", target="join_scans"),
             Edge(source="join_scans", target="security_reviewer")]
    return Package(
        name="security-audit", version="2.1.0",
        description="SAST + dependency audit + agent-driven review",
        inputs=[Port(name="codebase", artifact_path=".factory/strategy/observations.md")],
        outputs=[Port(name="security_review", artifact_path=".factory/reviews/security-review.md")],
        contract=StateContract(produces=frozenset({"security_reviewed"}),
                               capabilities=["sast", "dependency-audit", "security-review"]),
        graph=Workflow(name="security-audit", nodes=nodes, edges=edges, start_node="fork_scans"),
        entry_node="fork_scans", exit_node="security_reviewer",
        knobs=[
            OptKnob(name="reviewer_model", kind="model", node_id="security_reviewer",
                    default="opus", bounds=["sonnet", "opus"]),
        ],
    )


def perf_optimizer_pkg() -> Package:
    nodes = {
        "profiler": FnNode(id="profiler", command="py-spy record -o profile.md -- python -m pytest",
                           writes={".factory/reviews/profile.md"}),
        "perf_analyst": AgentNode(
            id="perf_analyst", role=AgentRole.RESEARCHER,
            prompt_template="Analyze profiling data. Identify top 3 hot paths with optimization proposals.",
            reads={".factory/reviews/profile.md"}, writes={".factory/reviews/perf-analysis.md"},
            model="sonnet",
        ),
        "perf_builder": AgentNode(
            id="perf_builder", role=AgentRole.BUILDER,
            prompt_template="Implement the top optimization from the perf analysis.",
            reads={".factory/reviews/perf-analysis.md"}, writes={".factory/reviews/perf-changes.md"},
        ),
    }
    edges = [Edge(source="profiler", target="perf_analyst"),
             Edge(source="perf_analyst", target="perf_builder")]
    return Package(
        name="perf-optimizer", version="1.3.0",
        description="Profile, analyze hot paths, implement optimizations",
        inputs=[Port(name="codebase", artifact_path=".factory/strategy/observations.md")],
        outputs=[Port(name="perf_changes", artifact_path=".factory/reviews/perf-changes.md")],
        contract=StateContract(produces=frozenset({"perf_optimized"}),
                               capabilities=["profiling", "performance-analysis"]),
        graph=Workflow(name="perf-optimizer", nodes=nodes, edges=edges, start_node="profiler"),
        entry_node="profiler", exit_node="perf_builder",
        knobs=[
            OptKnob(name="analyst_model", kind="model", node_id="perf_analyst",
                    default="sonnet", bounds=["haiku", "sonnet", "opus"]),
        ],
    )


def study_pkg() -> Package:
    nodes = {"study": Study(id="study", command="factory study {project_path}",
                            writes={".factory/strategy/observations.md"})}
    return Package(
        name="study", version="1.0.0",
        outputs=[Port(name="observations", artifact_path=".factory/strategy/observations.md")],
        contract=StateContract(produces=frozenset({"study_complete"}), capabilities=["codebase-analysis"]),
        graph=Workflow(name="study", nodes=nodes, edges=[], start_node="study"),
        entry_node="study", exit_node="study",
    )


def strategy_pkg() -> Package:
    node = AgentNode(id="strategist", role=AgentRole.STRATEGIST,
                     reads={".factory/strategy/observations.md"},
                     writes={".factory/strategy/current.md"})
    return Package(
        name="strategy", version="1.0.0",
        inputs=[Port(name="observations", artifact_path=".factory/strategy/observations.md")],
        outputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        contract=StateContract(requires=frozenset({"study_complete"}),
                               produces=frozenset({"strategy_complete"}),
                               capabilities=["planning"]),
        graph=Workflow(name="strategy", nodes={"strategist": node}, edges=[], start_node="strategist"),
        entry_node="strategist", exit_node="strategist",
    )


def build_pkg() -> Package:
    node = AgentNode(id="builder", role=AgentRole.BUILDER,
                     reads={".factory/strategy/current.md"},
                     writes={".factory/reviews/builder-latest.md"})
    return Package(
        name="build", version="1.0.0",
        inputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        outputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        contract=StateContract(requires=frozenset({"strategy_complete"}),
                               produces=frozenset({"build_complete"}),
                               capabilities=["code-generation"]),
        graph=Workflow(name="build", nodes={"builder": node}, edges=[], start_node="builder"),
        entry_node="builder", exit_node="builder",
    )


def qa_pkg() -> Package:
    nodes = {
        "health_check": AgentNode(id="health_check", role=AgentRole.HEALTH_CHECKER,
                                  reads={".factory/reviews/builder-latest.md"},
                                  writes={".factory/reviews/health-check.md"}),
        "code_review": AgentNode(id="code_review", role=AgentRole.CODE_REVIEWER,
                                 reads={".factory/reviews/builder-latest.md"},
                                 writes={".factory/reviews/code-review.md"}),
        "fork_qa": ForkNode(id="fork_qa", targets=["health_check", "code_review"]),
        "join_qa": JoinNode(id="join_qa", sources=["health_check", "code_review"],
                            reads={".factory/reviews/health-check.md", ".factory/reviews/code-review.md"}),
    }
    edges = [Edge(source="fork_qa", target="join_qa")]
    return Package(
        name="qa", version="1.0.0",
        inputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        outputs=[Port(name="health_check", artifact_path=".factory/reviews/health-check.md"),
                 Port(name="code_review", artifact_path=".factory/reviews/code-review.md")],
        contract=StateContract(requires=frozenset({"build_complete"}),
                               produces=frozenset({"qa_complete"}),
                               capabilities=["health-check", "code-review"]),
        graph=Workflow(name="qa", nodes=nodes, edges=edges, start_node="fork_qa"),
        entry_node="fork_qa", exit_node="join_qa",
    )


# ── fitness function ──────────────────────────────────────────────


def structural_fitness(wf: Workflow) -> dict[str, float]:
    """Score a workflow on structural properties.

    Rewards:
    - Parallelism (fork degree) — more concurrent work = faster
    - Agent density — ratio of agent nodes to total nodes
    - Compactness — fewer total nodes for same capability

    Penalizes:
    - Excessive depth — long serial chains are slow
    - No parallelism — missed concurrency opportunities
    """
    depth, fork_degree, agent_count, gate_count = compute_features(wf)
    total = len(wf.nodes)

    parallelism_score = min(1.0, fork_degree / 4.0)
    agent_density = agent_count / max(total, 1)
    compactness = max(0.0, 1.0 - (total - 10) / 20.0)
    depth_penalty = max(0.0, 1.0 - (depth - 5) / 15.0)

    model_bonus = 0.0
    for node in wf.nodes.values():
        if hasattr(node, "model"):
            m = getattr(node, "model", "")
            if m == "opus":
                model_bonus += 0.05
            elif m == "sonnet":
                model_bonus += 0.03
            elif m == "haiku":
                model_bonus += 0.01
    model_bonus = min(0.2, model_bonus)

    composite = (
        0.30 * parallelism_score
        + 0.20 * agent_density
        + 0.15 * compactness
        + 0.20 * depth_penalty
        + 0.15 * model_bonus
    )

    return {
        "composite": round(composite, 4),
        "parallelism": round(parallelism_score, 3),
        "agent_density": round(agent_density, 3),
        "compactness": round(compactness, 3),
        "depth_penalty": round(depth_penalty, 3),
        "model_bonus": round(model_bonus, 3),
    }


# ── mutation engine ───────────────────────────────────────────────


MODEL_OPTIONS = ["haiku", "sonnet", "opus"]
TIMEOUT_OPTIONS = [300, 600, 900, 1200, 1800, 3600]


def mutate_workflow(wf: Workflow, rng: random.Random) -> tuple[Workflow, str]:
    """Apply a random mutation to a workflow. Returns (mutated_wf, description)."""
    agent_nodes = [nid for nid, n in wf.nodes.items() if type(n).__name__ == "AgentNode"]

    if not agent_nodes:
        return wf, "no-op (no agent nodes)"

    target = rng.choice(agent_nodes)
    node = wf.nodes[target]

    mutation_type = rng.choice(["model", "prompt", "timeout"])

    if mutation_type == "model":
        new_model = rng.choice(MODEL_OPTIONS)
        result = mutate_params(wf, target, {"model": new_model})
        if result:
            return result[0], f"model({target})={new_model}"

    elif mutation_type == "prompt":
        result = mutate_prompt(wf, target)
        if result:
            return result[0], f"prompt({target})=variant"

    elif mutation_type == "timeout":
        new_timeout = rng.choice(TIMEOUT_OPTIONS)
        result = mutate_params(wf, target, {"timeout": new_timeout})
        if result:
            return result[0], f"timeout({target})={new_timeout}"

    return wf, "no-op (mutation rejected)"


# ── the optimization loop ─────────────────────────────────────────


def run_optimization(
    seed_packages: list[tuple[str, Package]],
    generations: int = 8,
    population_size: int = 6,
    mutations_per_gen: int = 4,
) -> None:
    """Run a real evolutionary search over package compositions."""

    rng = random.Random(42)

    header("PACKAGE OPTIMIZATION — real outer loop over compositions")
    print(f"\n  {WHITE}Seed compositions:{RESET}")
    for name, pkg in seed_packages:
        wf = pkg.compile()
        score = structural_fitness(wf)
        print(f"    {name:<30} {len(wf.nodes)} nodes  score={score['composite']:.4f}")

    population: list[tuple[str, Workflow, float]] = []
    seen_hashes: set[str] = set()

    for name, pkg in seed_packages:
        wf = pkg.compile()
        score = structural_fitness(wf)["composite"]
        h = structural_hash(wf)
        population.append((name, wf, score))
        seen_hashes.add(h)

    best_ever_score = max(s for _, _, s in population)
    best_ever_name = [n for n, _, s in population if s == best_ever_score][0]
    trajectory: list[float] = [best_ever_score]

    print(f"\n  {WHITE}Running {generations} generations, population {population_size}, "
          f"{mutations_per_gen} mutations/gen{RESET}\n")

    for gen in range(generations):
        gen_start = time.monotonic()
        new_candidates: list[tuple[str, Workflow, float]] = []

        for m in range(mutations_per_gen):
            parent_name, parent_wf, parent_score = rng.choice(population)

            child_wf, mutation_desc = mutate_workflow(parent_wf, rng)
            child_hash = structural_hash(child_wf)

            if child_hash in seen_hashes:
                continue
            seen_hashes.add(child_hash)

            child_score = structural_fitness(child_wf)["composite"]
            child_name = f"gen{gen}-{mutation_desc}"
            new_candidates.append((child_name, child_wf, child_score))

        population.extend(new_candidates)
        population.sort(key=lambda x: x[2], reverse=True)
        population = population[:population_size]

        gen_best = population[0]
        gen_elapsed = (time.monotonic() - gen_start) * 1000

        improved = ""
        if gen_best[2] > best_ever_score:
            best_ever_score = gen_best[2]
            best_ever_name = gen_best[0]
            improved = f"  {YELLOW}NEW BEST{RESET}"

        trajectory.append(best_ever_score)

        print(f"  gen {gen:>2}  best={gen_best[2]:.4f}  pop={len(population)}  "
              f"new={len(new_candidates)}  {gen_elapsed:>6.1f}ms  "
              f"{DIM}{gen_best[0][:40]}{RESET}{improved}")

    # ── results ───────────────────────────────────────────────────

    header("RESULTS")

    print(f"\n  {WHITE}Score trajectory:{RESET}\n")
    max_score = max(trajectory)
    min_score = min(trajectory)
    score_range = max(max_score - min_score, 0.001)
    for i, score in enumerate(trajectory):
        normalized = (score - min_score) / score_range
        label = "seed" if i == 0 else f"g{i-1:>2} "
        print(f"    {DIM}{label}{RESET} {bar(normalized)} {score:.4f}")

    print(f"\n  {WHITE}Final population:{RESET}\n")
    for rank, (name, wf, score) in enumerate(population, 1):
        depth, fork_deg, agents, gates = compute_features(wf)
        print(f"    {DIM}#{rank}{RESET}  {score:.4f}  "
              f"{DIM}depth={depth} fork={fork_deg} agents={agents} gates={gates}{RESET}  "
              f"{name[:45]}")

    winner_name, winner_wf, winner_score = population[0]

    print(f"\n  {GREEN}{BOLD}Winner:{RESET} {winner_name}")
    print(f"  {GREEN}Score:{RESET}  {winner_score:.4f}")
    print(f"  {GREEN}Nodes:{RESET}  {len(winner_wf.nodes)}")

    data = winner_wf.to_dict()
    restored = Workflow.from_dict(data)
    print(f"  {GREEN}Serialized:{RESET} {len(json.dumps(data)):,} bytes")
    print(f"  {GREEN}Round-trip:{RESET} {'PASS' if len(restored.nodes) == len(winner_wf.nodes) else 'FAIL'}")

    header("WHAT JUST HAPPENED")
    print(f"""
  1. Three seed compositions were compiled from Package objects
  2. Factory's {BOLD}real mutation operators{RESET} (mutate_params, mutate_prompt)
     applied {generations * mutations_per_gen} mutations across {generations} generations
  3. Factory's {BOLD}real structural analysis{RESET} (compute_features) scored each
     candidate on parallelism, agent density, compactness, and depth
  4. Tournament selection kept the top {population_size} candidates per generation
  5. Deduplication via {BOLD}structural_hash{RESET} prevented wasted evaluations
  6. The winner was serialized to JSON and round-tripped — ready for registry

  {DIM}No scores were faked. Every mutation used factory/outer_loop/mutations.py.
  Every evaluation used factory/outer_loop/similarity.py.{RESET}
""")


def main():
    study = study_pkg()
    strat = strategy_pkg()
    sec = security_audit_pkg()
    perf = perf_optimizer_pkg()
    build = build_pkg()
    qa = qa_pkg()

    pipeline_a = Sequential(study, sec, perf, strat, build, qa, name="shift-left")
    review_phase = Parallel(sec, perf, name="security-and-perf")
    pipeline_b = Sequential(study, strat, build, review_phase, qa, name="parallel-review")
    pipeline_c = Sequential(study, strat, Parallel(sec, perf, name="harden"), build, qa, name="harden-first")

    run_optimization(
        seed_packages=[
            ("A: shift-left", pipeline_a),
            ("B: parallel-review", pipeline_b),
            ("C: harden-first", pipeline_c),
        ],
        generations=8,
        population_size=6,
        mutations_per_gen=4,
    )


if __name__ == "__main__":
    main()
