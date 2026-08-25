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
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
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


# ── main ──────────────────────────────────────────────────────────


async def main():
    header("PACKAGE E2E OPTIMIZATION")
    print(f"\n  {WHITE}Project:{RESET} {PROJECT} (task queue with known bugs + gaps)")
    print(f"  {WHITE}Goal:{RESET}    Compose packages, run factory's outer loop, tune topology + knobs\n")

    baseline = eval_project(PROJECT)
    print(f"  {WHITE}Baseline:{RESET} {baseline['tests_passed']}/{baseline['tests_total']} tests, "
          f"{baseline['total_lines']} lines, score {baseline['composite']:.3f}")

    # ── Phase 1: Seed compositions (3 different topologies) ───────

    header("PHASE 1 — Three seed topologies")
    print(f"\n  Running 3 structurally different Package compositions through factory.\n")

    # Topology A: linear pipeline with single general researcher
    topo_a = Sequential(
        study_pkg(), research_pkg("general"), strategy_pkg(), build_pkg(), qa_pkg(),
        name="linear-general",
    )

    # Topology B: parallel specialized researchers (bugs + coverage) → merge → strategy → build
    topo_b = Sequential(
        study_pkg(),
        Parallel(research_pkg("bugs"), research_pkg("coverage"), name="parallel-research"),
        merge_research_pkg(),
        strategy_pkg(),
        build_pkg(),
        qa_pkg(),
        name="parallel-specialized",
    )

    # Topology C: linear with opus researcher (knob variant of A)
    topo_c = Sequential(
        study_pkg(), research_pkg("general", model="opus"), strategy_pkg(), build_pkg(), qa_pkg(),
        name="linear-opus",
    )

    results: list[tuple[str, Workflow, dict, float]] = []

    for label, pkg in [
        ("A: linear-general", topo_a),
        ("B: parallel-specialized (bugs + coverage)", topo_b),
        ("C: linear-opus (knob: model=opus)", topo_c),
    ]:
        wf = pkg.compile()
        scores, elapsed = await run_and_eval(wf, PROJECT, label)
        results.append((label, wf, scores, elapsed))

    # ── Phase 2: Mutate the best candidate ────────────────────────

    header("PHASE 2 — Mutate the best candidate")

    results.sort(key=lambda r: r[2]["composite"], reverse=True)
    best_label, best_wf, best_scores, best_elapsed = results[0]
    print(f"\n  {GREEN}Best seed: {best_label} (score={best_scores['composite']:.3f}){RESET}")
    print(f"  Applying factory's real mutation operators to generate 2 variants...\n")

    import random
    rng = random.Random(42)

    # Freeze non-agent nodes to protect package internal structure.
    # Mutations can tune agent knobs (model, prompt, timeout) but
    # must not remove ForkNodes, JoinNodes, or FnNodes that wire
    # the composition together.
    frozen_nodes = {
        nid for nid, n in best_wf.nodes.items()
        if type(n).__name__ not in ("AgentNode",)
    }
    print(f"  {DIM}Frozen nodes (package internals): {sorted(frozen_nodes)}{RESET}\n")

    mutations_applied = []
    for i in range(2):
        candidate_wf = copy.deepcopy(best_wf)
        mutation_desc_parts = []

        # Apply a param mutation (model change)
        agent_nodes = [nid for nid, n in candidate_wf.nodes.items()
                       if type(n).__name__ == "AgentNode"]
        if agent_nodes:
            target = rng.choice(agent_nodes)
            new_model = rng.choice(["haiku", "sonnet", "opus"])
            param_result = mutate_params(
                candidate_wf, target, {"model": new_model},
                frozen_nodes=frozen_nodes,
            )
            if param_result:
                candidate_wf = param_result[0]
                mutation_desc_parts.append(f"model({target})={new_model}")

        # Apply a prompt mutation
        agent_nodes = [nid for nid, n in candidate_wf.nodes.items()
                       if type(n).__name__ == "AgentNode"]
        if agent_nodes:
            target = rng.choice(agent_nodes)
            prompt_result = mutate_prompt(
                candidate_wf, target,
                frozen_nodes=frozen_nodes,
            )
            if prompt_result:
                candidate_wf = prompt_result[0]
                mutation_desc_parts.append(f"prompt({target})")

        desc = " + ".join(mutation_desc_parts) or "no-op"
        mutations_applied.append((f"Mutant {i+1}: {desc}", candidate_wf))
        print(f"  {DIM}Generated: {desc}{RESET}")

    for label, wf in mutations_applied:
        scores, elapsed = await run_and_eval(wf, PROJECT, label)
        results.append((label, wf, scores, elapsed))

    # ── Phase 3: Results ──────────────────────────────────────────

    header("RESULTS — all candidates ranked")

    results.sort(key=lambda r: r[2]["composite"], reverse=True)

    print(f"\n  {'#':<3} {'Score':>6} {'Tests':>8} {'Lines':>6} {'Time':>6}  Candidate")
    print(f"  {'─' * 68}")

    for rank, (label, wf, scores, elapsed) in enumerate(results, 1):
        depth, fork_deg, agents, _ = compute_features(wf)
        marker = f" {YELLOW}★{RESET}" if rank == 1 else ""
        print(f"  {rank:<3} {scores['composite']:>6.3f} "
              f"{scores['tests_passed']:>3}/{scores['tests_total']:<3} "
              f"{scores['total_lines']:>6} {elapsed:>5.0f}s  {label}{marker}")

    winner_label, winner_wf, winner_scores, _ = results[0]

    # Show trajectory
    print(f"\n  {WHITE}Score trajectory:{RESET}\n")
    all_scores = [r[2]["composite"] for r in results]
    max_s, min_s = max(all_scores), min(all_scores)
    score_range = max(max_s - min_s, 0.001)
    best_so_far = 0.0
    for i, (label, _, scores, _) in enumerate(results):
        s = scores["composite"]
        best_so_far = max(best_so_far, s)
        normalized = (best_so_far - min_s) / score_range
        phase = "seed" if i < 3 else "mut "
        print(f"    {DIM}{phase}{RESET} {bar(normalized)} {best_so_far:.3f}  {DIM}{label[:35]}{RESET}")

    # Serialize winner
    data = winner_wf.to_dict()
    restored = Workflow.from_dict(data)

    print(f"\n  {GREEN}{BOLD}Winner: {winner_label}{RESET}")
    print(f"  {GREEN}Score:{RESET}  {winner_scores['composite']:.3f} "
          f"(tests: {winner_scores['tests_passed']}/{winner_scores['tests_total']}, "
          f"lines: {winner_scores['total_lines']})")
    print(f"  {GREEN}Serialized:{RESET} {len(json.dumps(data)):,} bytes, "
          f"round-trip {'PASS' if len(restored.nodes) == len(winner_wf.nodes) else 'FAIL'}")

    delta = winner_scores["composite"] - baseline["composite"]
    print(f"  {GREEN}Improvement over baseline:{RESET} +{delta:.3f} "
          f"({baseline['composite']:.3f} → {winner_scores['composite']:.3f})")

    header("WHAT JUST HAPPENED")
    print(f"""
  1. Three {BOLD}structurally different{RESET} Package compositions compiled to Workflows:
     • Linear pipeline (single general researcher)
     • Parallel specialized researchers (bugs + coverage fork/join)
     • Knob variant (opus model on researcher)

  2. Each ran through factory's {BOLD}real WorkflowExecutor{RESET} with live Claude agents
     against a project with known bugs and test coverage gaps

  3. Factory's {BOLD}real mutation operators{RESET} (mutate_params, mutate_prompt)
     generated 2 variants from the best seed, tuning model choice and prompts

  4. All 5 candidates were scored on real pytest results: tests passed,
     code growth, and artifact completeness

  5. The winner was serialized to JSON — ready for the package registry
""")


if __name__ == "__main__":
    asyncio.run(main())
