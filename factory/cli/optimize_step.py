"""CLI handler for factory optimize-step — thin wrappers for workflow graph nodes."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


def _opt_dir(project: Path) -> Path:
    return project / ".factory" / "optimization"


def _read_state(project: Path) -> dict:
    state_path = _opt_dir(project) / "state.json"
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"step": 0, "current_score": 0.0, "best_score": 0.0, "best_step": 0, "history": []}


def _write_state(project: Path, state: dict) -> None:
    state_path = _opt_dir(project) / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n")


def _get_benchmark_and_evaluator(
    project: Path,
) -> tuple[Any, Any, Any]:
    """Build executor + evaluator from env vars, reusing existing protocol implementations."""
    benchmark = os.environ.get("FACTORY_OPT_BENCHMARK", "searchqa")
    concurrency = int(os.environ.get("FACTORY_OPT_CONCURRENCY", "5"))
    git_ref = os.environ.get("FACTORY_GIT_REF", "main")
    docker_host = os.environ.get("DOCKER_HOST", "")
    model = os.environ.get("FACTORY_OPT_MODEL", "sonnet")

    from factory.optimization.benchmarks.harbor import HarborBenchmark
    from factory.optimization.surface import Surface
    from factory.optimization.types import BenchmarkSplits

    splits: BenchmarkSplits | None = None
    splits_dir = project / ".factory" / "eval" / "benchmark" / "splits"
    if splits_dir.is_dir():
        splits = BenchmarkSplits.from_jsonl_dir(splits_dir)

    executor: Any
    evaluator: Any

    match benchmark:
        case "searchqa":
            from factory.optimization.benchmarks.searchqa import SearchQAEvaluator

            executor = HarborBenchmark(
                git_ref=git_ref, concurrency=concurrency,
                docker_host=docker_host, model=model, splits=splits,
            )
            evaluator = SearchQAEvaluator()

        case "featurebench":
            from factory.optimization.benchmarks.featurebench import FeatureBenchEvaluator

            executor = HarborBenchmark(
                git_ref=git_ref, concurrency=concurrency,
                docker_host=docker_host, model=model, splits=splits,
                dataset="featurebench",
                agent_class="factory_harbor_agent:FeatureBenchFactoryCeo",
            )
            evaluator = FeatureBenchEvaluator()

        case _:
            from factory.optimization.benchmarks.loader import load_benchmark

            benchmark_dir = project / ".factory" / "eval" / "benchmark"
            defn = load_benchmark(benchmark_dir)
            executor_params = defn.config.get("executor_params", {})
            evaluator_params = defn.config.get("evaluator_params", {})
            executor = defn.executor_cls(**executor_params)
            evaluator = defn.evaluator_cls(**evaluator_params)

    skill_path = _opt_dir(project) / "current_skill.md"
    skill_text = skill_path.read_text() if skill_path.exists() else ""
    surface = Surface(prompt_slots={"skill": skill_text})

    return executor, evaluator, surface


def cmd_optimize_step_run_dev(args: argparse.Namespace) -> int:
    """Run the dev split of the benchmark and record results."""
    project = Path(args.project).resolve()
    executor, evaluator, surface = _get_benchmark_and_evaluator(project)

    execution_result = executor.execute(project, surface, split="dev")

    score = 0.0
    artifacts = [Path(a) for a in execution_result.artifacts]
    if artifacts:
        eval_result = evaluator.parse_many(artifacts)
        if eval_result.valid:
            score = eval_result.score

    state = _read_state(project)
    step = state["step"] + 1
    state["step"] = step

    step_record = {
        "step": step,
        "score_start": state["current_score"],
        "score_end": score,
        "score_delta": score - state["current_score"],
        "verdict": "pending",
    }
    state["history"].append(step_record)
    state["current_score"] = score

    if score > state["best_score"]:
        state["best_score"] = score
        state["best_step"] = step

    _write_state(project, state)

    # Write per-step artifacts
    step_dir = _opt_dir(project) / "steps" / str(step)
    step_dir.mkdir(parents=True, exist_ok=True)

    results_data = []
    if hasattr(execution_result, "task_results") and execution_result.task_results:
        results_data = [
            {"task_id": t.task_id, "reward": t.reward, "predicted": t.predicted, "gold": t.gold}
            for t in execution_result.task_results
        ]
    (step_dir / "results.json").write_text(json.dumps(results_data, indent=2) + "\n")

    skill_path = _opt_dir(project) / "current_skill.md"
    if not skill_path.exists():
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(
            "# Question Answering Skill\n\n(No learned rules yet.)\n\n"
            "## Instructions\n\n"
            "Read the question and search results from /tmp/task-instruction.md.\n"
            "Answer the question and write ONLY your final answer to /workspace/answer.txt.\n"
            "Also include your answer in <answer> tags in your response.\n"
        )
    # Touch to mark as fresh for workflow artifact detection
    skill_path.touch()
    import shutil
    shutil.copy2(skill_path, step_dir / "skill.md")

    # On first call (step=1), also write baseline.json
    if step == 1:
        baseline = {"score": score, "step": 1, "task_results": results_data}
        (_opt_dir(project) / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n")

    log.info("optimize_step.run_dev", step=step, score=round(score, 4))
    print(json.dumps({"step": step, "score": score}))
    return 0


def cmd_optimize_step_run_test(args: argparse.Namespace) -> int:
    """Run the test split for a final unbiased score."""
    project = Path(args.project).resolve()
    executor, evaluator, surface = _get_benchmark_and_evaluator(project)

    execution_result = executor.execute(project, surface, split="test")

    score = 0.0
    artifacts = [Path(a) for a in execution_result.artifacts]
    if artifacts:
        eval_result = evaluator.parse_many(artifacts)
        if eval_result.valid:
            score = eval_result.score

    results_data = []
    if hasattr(execution_result, "task_results") and execution_result.task_results:
        results_data = [
            {"task_id": t.task_id, "reward": t.reward, "predicted": t.predicted, "gold": t.gold}
            for t in execution_result.task_results
        ]

    test_result = {"score": score, "task_results": results_data}
    result_path = _opt_dir(project) / "test_result.json"
    result_path.write_text(json.dumps(test_result, indent=2) + "\n")

    log.info("optimize_step.run_test", score=round(score, 4))
    print(json.dumps({"score": score}))
    return 0


def cmd_optimize_step_apply_patch(args: argparse.Namespace) -> int:
    """Read mutation.json, append rules to current_skill.md."""
    project = Path(args.project).resolve()
    mutation_path = _opt_dir(project) / "mutation.json"

    if not mutation_path.exists():
        print("Error: mutation.json not found", file=sys.stderr)
        return 1

    raw = mutation_path.read_text().strip()

    # Parse JSON — with regex fallback for markdown-wrapped JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\"rules\"[^{}]*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                print("Error: could not parse mutation.json", file=sys.stderr)
                return 1
        else:
            print("Error: could not parse mutation.json", file=sys.stderr)
            return 1

    rules = data.get("rules", [])
    if not rules:
        log.info("optimize_step.apply_patch.no_rules")
        return 0

    skill_path = _opt_dir(project) / "current_skill.md"
    skill_text = skill_path.read_text() if skill_path.exists() else ""

    new_rules = "\n## Learned Rules\n\n"
    for rule in rules:
        new_rules += f"- {rule}\n"

    skill_text += new_rules
    skill_path.write_text(skill_text)

    log.info("optimize_step.apply_patch", n_rules=len(rules))
    print(json.dumps({"rules_applied": len(rules)}))
    return 0


def cmd_optimize_step_check_gate(args: argparse.Namespace) -> int:
    """Check gate: PROCEED/RELOOP (exit 0, parsed from stdout), HALT (exit 1)."""
    project = Path(args.project).resolve()
    is_baseline = getattr(args, "baseline", False)

    if is_baseline:
        baseline_path = _opt_dir(project) / "baseline.json"
        if not baseline_path.exists():
            state = _read_state(project)
            score = state.get("current_score", 0.0)
        else:
            data = json.loads(baseline_path.read_text())
            score = data.get("score", 0.0)
        if score > 0:
            print("PROCEED")
            return 0
        else:
            print("HALT: baseline score is 0")
            return 1

    state = _read_state(project)
    history = state.get("history", [])
    max_iterations = int(os.environ.get("FACTORY_OPT_MAX_ITERATIONS", "5"))

    from factory.optimization.gate import evaluate_gate

    if not history:
        print("HALT: no history")
        return 1

    latest = history[-1]
    gate = evaluate_gate(
        candidate_score=latest["score_end"],
        current_score=latest["score_start"],
        best_score=state["best_score"],
        best_step=state["best_step"],
        global_step=state["step"],
    )

    mutation_count = len([h for h in history if h["step"] > 1])
    if mutation_count >= max_iterations:
        print(f"PROCEED: max iterations ({max_iterations}) reached")
        return 0

    if gate.accepted:
        print(f"PROCEED: {gate.reason}")
        return 0
    else:
        print(f"RELOOP: {gate.reason}")
        return 0
