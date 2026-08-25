#!/usr/bin/env python3
"""Package E2E Optimization — factory's outer loop over composed packages.

Builds 3 structurally different Package compositions, compiles each to a
Workflow, runs them through factory's real WorkflowExecutor with live Claude
agents, then applies factory's actual mutation operators to the best candidate
and re-evaluates. Shows topology + knob optimization converging on a winner.

Run: uv run python examples/package_e2e_optimize.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
import subprocess
import time
from pathlib import Path

from factory.outer_loop.mutations import mutate_params, mutate_prompt
from factory.outer_loop.similarity import compute_features, structural_hash
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.package import (
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
    FnNode,
    Study,
    Workflow,
)

# ── config ────────────────────────────────────────────────────────

PROJECT = Path("/tmp/test-pkg-hard")

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


def bar(value: float, width: int = 25) -> str:
    filled = int(value * width)
    return f"{GREEN}{'█' * filled}{DIM}{'░' * (width - filled)}{RESET}"


# ── packages ──────────────────────────────────────────────────────


def study_pkg() -> Package:
    node = Study(id="study", command="factory study {project_path}",
                 writes={".factory/strategy/observations.md"})
    return Package(
        name="study", version="1.0.0",
        outputs=[Port(name="observations", artifact_path=".factory/strategy/observations.md")],
        contract=StateContract(produces=frozenset({"study_complete"})),
        graph=Workflow(name="study", nodes={"study": node}, edges=[], start_node="study"),
        entry_node="study", exit_node="study",
    )


def research_pkg(focus: str = "general", model: str = "") -> Package:
    prompts = {
        "general": (
            "Read .factory/strategy/observations.md. "
            "Research what improvements would most benefit this codebase: "
            "bugs, missing test coverage, error handling gaps, missing features. "
            "Write a prioritized list of findings to .factory/strategy/research.md"
        ),
        "bugs": (
            "Read .factory/strategy/observations.md. "
            "Focus exclusively on finding bugs: logic errors, edge cases not handled, "
            "off-by-one errors, race conditions, resource leaks. "
            "Write each bug with reproduction steps to .factory/strategy/research-bugs.md"
        ),
        "coverage": (
            "Read .factory/strategy/observations.md. "
            "Focus on test coverage gaps: untested functions, missing edge case tests, "
            "untested error paths. "
            "Write specific test cases that should exist to .factory/strategy/research-coverage.md"
        ),
    }
    node_id = f"researcher_{focus}"
    output_file = f".factory/strategy/research-{focus}.md" if focus != "general" else ".factory/strategy/research.md"
    node = AgentNode(
        id=node_id, role=AgentRole.RESEARCHER,
        prompt_template=prompts[focus],
        reads={".factory/strategy/observations.md"},
        writes={output_file},
        model=model,
    )
    return Package(
        name=f"research-{focus}", version="1.0.0",
        description=f"{focus} researcher (model={model or 'default'})",
        inputs=[Port(name="observations", artifact_path=".factory/strategy/observations.md")],
        outputs=[Port(name=f"research_{focus}", artifact_path=output_file)],
        contract=StateContract(requires=frozenset({"study_complete"}),
                               produces=frozenset({f"research_{focus}_complete"})),
        graph=Workflow(name=f"research-{focus}", nodes={node_id: node}, edges=[], start_node=node_id),
        entry_node=node_id, exit_node=node_id,
        knobs=[OptKnob(name=f"{focus}_model", kind="model", node_id=node_id,
                       default=model or "default", bounds=["haiku", "sonnet", "opus"])],
    )


def strategy_pkg(reads_from: list[str] | None = None) -> Package:
    reads = set(reads_from or [".factory/strategy/research.md"])
    node = AgentNode(
        id="strategist", role=AgentRole.STRATEGIST,
        prompt_template=(
            "Read the research findings. Pick the single highest-impact improvement. "
            "Write a concrete hypothesis and step-by-step implementation plan to "
            ".factory/strategy/current.md. Include what tests to add or modify."
        ),
        reads=reads,
        writes={".factory/strategy/current.md"},
    )
    return Package(
        name="strategy", version="1.0.0",
        inputs=[Port(name="research", artifact_path=list(reads)[0])],
        outputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        contract=StateContract(produces=frozenset({"strategy_complete"})),
        graph=Workflow(name="strategy", nodes={"strategist": node}, edges=[], start_node="strategist"),
        entry_node="strategist", exit_node="strategist",
    )


def build_pkg() -> Package:
    node = AgentNode(
        id="builder", role=AgentRole.BUILDER,
        prompt_template=(
            "Read .factory/strategy/current.md. Implement the planned change. "
            "Run python3 -m pytest test_taskqueue.py -v after each edit. "
            "All tests must pass. Write a summary to .factory/reviews/builder-latest.md"
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )
    return Package(
        name="build", version="1.0.0",
        inputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
        outputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        contract=StateContract(requires=frozenset({"strategy_complete"}),
                               produces=frozenset({"build_complete"})),
        graph=Workflow(name="build", nodes={"builder": node}, edges=[], start_node="builder"),
        entry_node="builder", exit_node="builder",
    )


def qa_pkg() -> Package:
    node = AgentNode(
        id="health_checker", role=AgentRole.HEALTH_CHECKER,
        prompt_template=(
            "Run python3 -m pytest test_taskqueue.py -v. "
            "Report: total tests, passed, failed, and any error output. "
            "Write full results to .factory/reviews/health-check.md"
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/reviews/health-check.md"},
    )
    return Package(
        name="qa", version="1.0.0",
        inputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
        outputs=[Port(name="health_check", artifact_path=".factory/reviews/health-check.md")],
        contract=StateContract(requires=frozenset({"build_complete"}),
                               produces=frozenset({"qa_complete"})),
        graph=Workflow(name="qa", nodes={"health_checker": node}, edges=[], start_node="health_checker"),
        entry_node="health_checker", exit_node="health_checker",
    )


def merge_research_pkg() -> Package:
    """FnNode that concatenates parallel research outputs."""
    node = FnNode(
        id="merge_research",
        command=(
            "cat {project_path}/.factory/strategy/research-bugs.md "
            "{project_path}/.factory/strategy/research-coverage.md "
            "> {project_path}/.factory/strategy/research.md 2>/dev/null || "
            "cat {project_path}/.factory/strategy/research-*.md "
            "> {project_path}/.factory/strategy/research.md"
        ),
        reads={".factory/strategy/research-bugs.md", ".factory/strategy/research-coverage.md"},
        writes={".factory/strategy/research.md"},
    )
    return Package(
        name="merge-research", version="1.0.0",
        inputs=[
            Port(name="bugs", artifact_path=".factory/strategy/research-bugs.md"),
            Port(name="coverage", artifact_path=".factory/strategy/research-coverage.md"),
        ],
        outputs=[Port(name="research", artifact_path=".factory/strategy/research.md")],
        contract=StateContract(produces=frozenset({"research_complete"})),
        graph=Workflow(name="merge-research", nodes={"merge_research": node}, edges=[], start_node="merge_research"),
        entry_node="merge_research", exit_node="merge_research",
    )


# ── evaluation ────────────────────────────────────────────────────


def eval_project(project_path: Path) -> dict:
    """Score the project by running tests and measuring improvements."""
    result = subprocess.run(
        ["python3", "-m", "pytest", "test_taskqueue.py", "-v", "--tb=short"],
        cwd=project_path, capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr
    passed = output.count(" PASSED")
    failed = output.count(" FAILED")
    errored = output.count(" ERROR")
    total = passed + failed + errored

    py_files = list(project_path.glob("*.py"))
    total_lines = sum(len(f.read_text().splitlines()) for f in py_files)

    test_file = project_path / "test_taskqueue.py"
    test_lines = len(test_file.read_text().splitlines()) if test_file.exists() else 0

    factory_dir = project_path / ".factory"
    artifacts = sum(1 for name in [
        "strategy/observations.md", "strategy/research.md",
        "strategy/current.md", "reviews/builder-latest.md",
        "reviews/health-check.md",
    ] if (factory_dir / name).exists() and (factory_dir / name).stat().st_size > 20)

    test_pass_rate = passed / max(total, 1)
    test_growth = max(0, total - 5) / 15  # baseline is 5, target ~20
    line_growth = min(1.0, max(0, total_lines - 100) / 200)
    artifact_score = artifacts / 5

    composite = (
        0.35 * test_pass_rate
        + 0.25 * min(1.0, test_growth)
        + 0.15 * line_growth
        + 0.25 * artifact_score
    )

    return {
        "composite": round(composite, 4),
        "tests_passed": passed,
        "tests_failed": failed,
        "tests_total": total,
        "test_pass_rate": round(test_pass_rate, 3),
        "total_lines": total_lines,
        "test_lines": test_lines,
        "artifacts": artifacts,
    }


def reset_project(project_path: Path) -> None:
    """Reset project to baseline commit."""
    subprocess.run(["git", "checkout", ".", "--quiet"], cwd=project_path, capture_output=True)
    subprocess.run(["git", "clean", "-fd", "--quiet"], cwd=project_path, capture_output=True)
    # Recreate .factory dirs
    for sub in ["strategy", "reviews", "experiments", "archive"]:
        (project_path / ".factory" / sub).mkdir(parents=True, exist_ok=True)


# ── run a workflow ────────────────────────────────────────────────


async def run_and_eval(
    wf: Workflow,
    project_path: Path,
    label: str,
) -> tuple[dict, float]:
    """Run a workflow, evaluate, return (scores, elapsed)."""
    reset_project(project_path)

    print(f"\n  {YELLOW}▶ {label}{RESET}")
    depth, fork_deg, agents, gates = compute_features(wf)
    print(f"    {DIM}{len(wf.nodes)} nodes | depth={depth} fork={fork_deg} "
          f"agents={agents} | hash={structural_hash(wf)[:8]}{RESET}")

    start = time.monotonic()
    executor = WorkflowExecutor(workflow=wf, project_path=project_path, auto_approve=True)
    result = await executor.execute()
    elapsed = time.monotonic() - start

    status = f"{GREEN}OK{RESET}" if result.success else f"{RED}HALT: {result.halt_reason}{RESET}"
    print(f"    {DIM}Executed {result.nodes_executed} nodes in {elapsed:.0f}s [{status}]{RESET}")

    scores = eval_project(project_path)
    print(f"    Tests: {scores['tests_passed']}/{scores['tests_total']} | "
          f"Lines: {scores['total_lines']} | "
          f"Score: {BOLD}{scores['composite']:.3f}{RESET}")

    return scores, elapsed


# ── mutation helpers ───────────────────────────────────────────────


MODEL_CHOICES = ["haiku", "sonnet", "opus"]
TIMEOUT_CHOICES = [300, 600, 900, 1800]


def apply_mutations(
    wf: Workflow,
    frozen_nodes: set[str],
    rng: random.Random,
    n_mutations: int = 2,
) -> tuple[Workflow, list[str]]:
    """Apply n_mutations to a workflow, returning (mutated_wf, descriptions)."""
    candidate = copy.deepcopy(wf)
    descs: list[str] = []

    for _ in range(n_mutations):
        agent_nodes = [nid for nid, n in candidate.nodes.items()
                       if type(n).__name__ == "AgentNode" and nid not in frozen_nodes]
        if not agent_nodes:
            break

        mutation_type = rng.choice(["model", "prompt", "timeout"])
        target = rng.choice(agent_nodes)

        if mutation_type == "model":
            new_model = rng.choice(MODEL_CHOICES)
            result = mutate_params(candidate, target, {"model": new_model},
                                   frozen_nodes=frozen_nodes)
            if result:
                candidate = result[0]
                descs.append(f"model({target})={new_model}")

        elif mutation_type == "prompt":
            result = mutate_prompt(candidate, target, frozen_nodes=frozen_nodes)
            if result:
                candidate = result[0]
                descs.append(f"prompt({target})")

        elif mutation_type == "timeout":
            new_timeout = rng.choice(TIMEOUT_CHOICES)
            result = mutate_params(candidate, target, {"timeout": new_timeout},
                                   frozen_nodes=frozen_nodes)
            if result:
                candidate = result[0]
                descs.append(f"timeout({target})={new_timeout}")

    return candidate, descs


# ── main ──────────────────────────────────────────────────────────


async def main():
    rng = random.Random(42)
    NUM_GENERATIONS = 4
    CANDIDATES_PER_GEN = 2

    header("PACKAGE E2E OPTIMIZATION — iterative outer loop")
    print(f"\n  {WHITE}Project:{RESET}      {PROJECT} (task queue with known bugs + gaps)")
    print(f"  {WHITE}Generations:{RESET}  {NUM_GENERATIONS}")
    print(f"  {WHITE}Candidates:{RESET}   {CANDIDATES_PER_GEN} per generation (mutated from best)")
    print(f"  {WHITE}Mutations:{RESET}    model, prompt, timeout (via factory's real operators)\n")

    baseline = eval_project(PROJECT)
    print(f"  {WHITE}Baseline:{RESET} {baseline['tests_passed']}/{baseline['tests_total']} tests, "
          f"{baseline['total_lines']} lines, score {baseline['composite']:.3f}")

    # ── Gen 0: Seed with the best topology ────────────────────────

    header("GEN 0 — Seed composition")

    seed = Sequential(
        study_pkg(),
        Parallel(research_pkg("bugs"), research_pkg("coverage"), name="parallel-research"),
        merge_research_pkg(),
        strategy_pkg(),
        build_pkg(),
        qa_pkg(),
        name="parallel-specialized",
    )

    print(f"\n  {WHITE}Topology:{RESET} Sequential(study, Parallel(research-bugs, research-coverage),")
    print("           merge, strategy, build, qa)")
    print(f"  {DIM}9 nodes, fork/join parallel researchers, full QA{RESET}")

    seed_wf = seed.compile()
    seed_scores, seed_elapsed = await run_and_eval(seed_wf, PROJECT, "Gen 0: seed")

    # Track the best across all generations
    best_wf = seed_wf
    best_scores = seed_scores
    best_label = "Gen 0: seed"
    best_elapsed = seed_elapsed

    # Freeze package internals
    frozen_nodes = {
        nid for nid, n in best_wf.nodes.items()
        if type(n).__name__ not in ("AgentNode",)
    }

    trajectory: list[tuple[str, float, str]] = [
        ("Gen 0", best_scores["composite"], best_label),
    ]

    all_results: list[tuple[str, dict, float]] = [
        (best_label, best_scores, best_elapsed),
    ]

    # ── Generations 1-N: mutate → evaluate → select ──────────────

    for gen in range(1, NUM_GENERATIONS + 1):
        header(f"GEN {gen} — mutate best, evaluate {CANDIDATES_PER_GEN} candidates")

        print(f"\n  {WHITE}Parent:{RESET} {best_label} (score={best_scores['composite']:.3f})")
        print(f"  {DIM}Frozen: {sorted(frozen_nodes)}{RESET}\n")

        gen_best_wf = best_wf
        gen_best_scores = best_scores
        gen_best_label = best_label

        for c in range(CANDIDATES_PER_GEN):
            candidate_wf, mutation_descs = apply_mutations(
                best_wf, frozen_nodes, rng, n_mutations=rng.randint(1, 3),
            )
            desc = " + ".join(mutation_descs) if mutation_descs else "no-op"
            label = f"Gen {gen}.{c+1}: {desc}"

            # Skip if identical to parent
            if structural_hash(candidate_wf) == structural_hash(best_wf):
                print(f"  {DIM}Skipped duplicate: {desc}{RESET}")
                continue

            scores, elapsed = await run_and_eval(candidate_wf, PROJECT, label)
            all_results.append((label, scores, elapsed))

            if scores["composite"] > gen_best_scores["composite"]:
                gen_best_wf = candidate_wf
                gen_best_scores = scores
                gen_best_label = label

        # Select: keep the best from this generation
        if gen_best_scores["composite"] > best_scores["composite"]:
            delta = gen_best_scores["composite"] - best_scores["composite"]
            print(f"\n  {GREEN}Improved: {best_scores['composite']:.3f} → "
                  f"{gen_best_scores['composite']:.3f} (+{delta:.3f}){RESET}")
            best_wf = gen_best_wf
            best_scores = gen_best_scores
            best_label = gen_best_label
            # Re-freeze based on new best
            frozen_nodes = {
                nid for nid, n in best_wf.nodes.items()
                if type(n).__name__ not in ("AgentNode",)
            }
        else:
            print(f"\n  {DIM}No improvement this generation (best remains {best_scores['composite']:.3f}){RESET}")

        trajectory.append((f"Gen {gen}", best_scores["composite"], best_label))

    # ── Results ───────────────────────────────────────────────────

    header("OPTIMIZATION COMPLETE")

    print(f"\n  {WHITE}Score trajectory across generations:{RESET}\n")
    min_s = min(s for _, s, _ in trajectory)
    max_s = max(s for _, s, _ in trajectory)
    score_range = max(max_s - min_s, 0.001)
    for gen_label, score, candidate_label in trajectory:
        normalized = (score - min_s) / score_range if score_range > 0.001 else 1.0
        marker = f" {YELLOW}★{RESET}" if score == max_s and gen_label != "Gen 0" else ""
        print(f"    {gen_label:<6} {bar(normalized)} {score:.3f}  "
              f"{DIM}{candidate_label[:40]}{RESET}{marker}")

    print(f"\n  {WHITE}All evaluated candidates:{RESET}\n")
    print(f"  {'#':<3} {'Score':>6} {'Tests':>8} {'Lines':>6} {'Time':>6}  Candidate")
    print(f"  {'─' * 70}")

    all_results.sort(key=lambda r: r[1]["composite"], reverse=True)
    for rank, (label, scores, elapsed) in enumerate(all_results, 1):
        marker = f" {YELLOW}★{RESET}" if rank == 1 else ""
        print(f"  {rank:<3} {scores['composite']:>6.3f} "
              f"{scores['tests_passed']:>3}/{scores['tests_total']:<3} "
              f"{scores['total_lines']:>6} {elapsed:>5.0f}s  {label}{marker}")

    # Serialize winner
    data = best_wf.to_dict()
    restored = Workflow.from_dict(data)

    print(f"\n  {GREEN}{BOLD}Winner: {best_label}{RESET}")
    print(f"  {GREEN}Score:{RESET}  {best_scores['composite']:.3f} "
          f"(tests: {best_scores['tests_passed']}/{best_scores['tests_total']}, "
          f"lines: {best_scores['total_lines']})")
    print(f"  {GREEN}Serialized:{RESET} {len(json.dumps(data)):,} bytes, "
          f"round-trip {'PASS' if len(restored.nodes) == len(best_wf.nodes) else 'FAIL'}")

    delta = best_scores["composite"] - baseline["composite"]
    print(f"  {GREEN}Improvement over baseline:{RESET} +{delta:.3f} "
          f"({baseline['composite']:.3f} → {best_scores['composite']:.3f})")

    total_evals = len(all_results)
    total_time = sum(e for _, _, e in all_results)
    print(f"  {GREEN}Total evaluations:{RESET} {total_evals} "
          f"({total_time:.0f}s / {total_time/60:.1f}min)")

    header("WHAT JUST HAPPENED")
    print(f"""
  The factory's outer loop ran {NUM_GENERATIONS} generations of iterative optimization:

  1. {BOLD}Gen 0:{RESET} Seeded with a parallel-specialized Package composition
     (fork/join researchers + merge + strategy + build + QA)

  2. {BOLD}Gens 1-{NUM_GENERATIONS}:{RESET} Each generation:
     a) Takes the best workflow from the previous generation
     b) Applies 1-3 of factory's {BOLD}real mutation operators{RESET}
        (mutate_params, mutate_prompt) to generate {CANDIDATES_PER_GEN} candidates
     c) Runs each through the {BOLD}real WorkflowExecutor{RESET} with live Claude agents
     d) Scores on real pytest results
     e) Selects the best as parent for the next generation

  3. {BOLD}Package boundaries enforced:{RESET} ForkNode, JoinNode, FnNode internals
     were frozen. Only agent knobs (model, prompt, timeout) were mutated.

  4. {BOLD}Winner serialized{RESET} to JSON, ready for the package registry.

  This is the compile() → mutate → evaluate → select loop from the design doc,
  running on real agents against a real codebase.
""")


if __name__ == "__main__":
    asyncio.run(main())
