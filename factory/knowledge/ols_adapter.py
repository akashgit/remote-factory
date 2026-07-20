"""OLS troubleshooting eval adapter — parse CSV results into triplets."""

from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import structlog

from factory.knowledge.models import (
    Entity,
    EntityType,
    PredicateType,
    Triplet,
)

log = structlog.get_logger()


def _slugify(name: str) -> str:
    """Lowercase, replace non-alphanumeric with underscores, strip edges."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")


def _entity(entity_type: EntityType, name: str, **attrs: str | float | bool) -> Entity:
    """Create an entity with a slugified ID."""
    return Entity(
        id=f"{entity_type.value}:{_slugify(name)}",
        type=entity_type,
        name=name,
        attributes=dict(attrs) if attrs else {},
    )


def _state_path(config_path: Path) -> Path:
    return config_path.parent / "run_state.json"


def _load_state(config_path: Path) -> dict:
    sp = _state_path(config_path)
    if sp.exists():
        return json.loads(sp.read_text())
    return {
        "baseline_score": None,
        "current_score": None,
        "iteration_count": 0,
        "score_history": [],
    }


def _save_state(config_path: Path, state: dict) -> None:
    _state_path(config_path).write_text(json.dumps(state, indent=2))


def _append_improvement_record(
    config_path: Path,
    score_before: float | None,
    score_after: float,
) -> None:
    """Append one record to {task_id}_improvements.jsonl."""
    cfg = json.loads(config_path.read_text())
    task_id = cfg["task_id"]
    state = _load_state(config_path)
    history_path = config_path.parent / f"{task_id}_improvements.jsonl"

    improvement_md = config_path.parent / f"{task_id}_improvement.md"
    changes = improvement_md.read_text() if improvement_md.exists() else ""

    record = {
        "iteration": state.get("iteration_count", 0),
        "score_before": score_before,
        "score_after": score_after,
        "changes_summary": changes[:500],
        "timestamp": datetime.now().isoformat(),
    }
    with history_path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def parse_results(results_dir: Path, task_context: str) -> list[Triplet]:
    """Parse OLS eval CSV results into knowledge graph triplets.

    Scans ``results_dir`` for ``iter_NN/<scenario>/evaluation_*_detailed.csv``
    files and extracts triplets covering scenario outcomes, metric breakdowns,
    causal chains for failures, and execution time metadata.
    """
    csv_paths = sorted(results_dir.glob("iter_*/*/*.csv"))
    if not csv_paths:
        log.warning("ols_adapter.no_csv_files", path=str(results_dir))
        return []

    triplets: list[Triplet] = []
    agent = _entity(EntityType.AGENT, "ols_agent")
    now = datetime.now()

    for csv_path in csv_paths:
        scenario = csv_path.parent.name
        iter_dir = csv_path.parent.parent.name
        iter_match = re.search(r"iter_(\d+)", iter_dir)
        iter_num = iter_match.group(1) if iter_match else "00"
        source = f"ols_eval:iter_{iter_num}:{scenario}"

        try:
            with csv_path.open() as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_triplets = _extract_row_triplets(
                        row, agent, scenario, source, now,
                    )
                    triplets.extend(row_triplets)
        except FileNotFoundError:
            log.warning("ols_adapter.file_not_found", path=str(csv_path))
        except csv.Error as exc:
            log.warning("ols_adapter.csv_error", path=str(csv_path), error=str(exc))

    log.info(
        "ols_adapter.parsed",
        path=str(results_dir),
        csv_files=len(csv_paths),
        triplets=len(triplets),
    )
    return triplets


def _extract_row_triplets(
    row: dict[str, str],
    agent: Entity,
    scenario: str,
    source: str,
    now: datetime,
) -> list[Triplet]:
    """Extract triplets from a single CSV row."""
    triplets: list[Triplet] = []

    metric = row.get("metric_identifier", "")
    result = row.get("result", "")
    turn_id = row.get("turn_id", "")
    query = row.get("query", "")
    response = row.get("response", "")
    execution_time = row.get("execution_time", "")

    if not metric:
        return []

    try:
        score_val = float(row.get("score", "")) if row.get("score") else None
    except (ValueError, TypeError):
        log.warning("ols_adapter.invalid_score", scenario=scenario, metric=metric)
        return []

    is_conversation_level = not turn_id
    is_answer_correctness = "answer_correctness" in metric

    if result == "ERROR":
        result = "FAIL"
        score_val = 0.0

    task = _entity(EntityType.TASK, scenario, description=scenario)

    if is_answer_correctness and not is_conversation_level:
        passed = result == "PASS"
        triplets.append(
            Triplet(
                subject=agent,
                predicate=PredicateType.SUCCEEDS_AT if passed else PredicateType.FAILS_AT,
                object=task,
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=f"answer_correctness={score_val}, result={result}",
            )
        )

        if not passed:
            triplets.append(
                Triplet(
                    subject=_entity(
                        EntityType.CONCEPT,
                        f"{turn_id}_failure",
                    ),
                    predicate=PredicateType.CAUSES,
                    object=_entity(EntityType.OUTCOME, f"{scenario}_failure"),
                    confidence=0.9,
                    source=source,
                    timestamp=now,
                    evidence=f"query={query[:100]}, response={response[:100]}",
                )
            )

    if is_conversation_level:
        triplets.append(
            Triplet(
                subject=_entity(EntityType.CONCEPT, metric),
                predicate=PredicateType.PART_OF,
                object=_entity(EntityType.OUTCOME, f"{scenario}_conversation"),
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=f"score={score_val}, result={result}",
            )
        )
    else:
        triplets.append(
            Triplet(
                subject=_entity(EntityType.CONCEPT, metric),
                predicate=PredicateType.PART_OF,
                object=_entity(EntityType.OUTCOME, f"{scenario}_result"),
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=f"score={score_val}, result={result}",
            )
        )

    if execution_time and turn_id:
        triplets.append(
            Triplet(
                subject=_entity(EntityType.TASK, f"{scenario}.{turn_id}"),
                predicate=PredicateType.RELATED_TO,
                object=_entity(EntityType.CONCEPT, "execution_time"),
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=f"{execution_time}s",
            )
        )

    return triplets


def compute_aggregate_score(results_dir: Path) -> float:
    """Compute macro-averaged answer_correctness pass rate.

    Matches ``eval_metrics.sh`` logic: per-scenario pass rates averaged
    across all scenarios.
    """
    scenario_results: dict[str, list[bool]] = defaultdict(list)

    for csv_path in sorted(results_dir.glob("iter_*/*/*.csv")):
        scenario = csv_path.parent.name
        try:
            with csv_path.open() as f:
                for row in csv.DictReader(f):
                    metric = row.get("metric_identifier", "")
                    if "answer_correctness" not in metric:
                        continue
                    result = row.get("result", "")
                    if result == "ERROR":
                        scenario_results[scenario].append(False)
                        continue
                    try:
                        score = float(row.get("score", ""))
                    except (ValueError, TypeError):
                        continue
                    scenario_results[scenario].append(score >= 0.5)
        except (FileNotFoundError, csv.Error) as exc:
            log.warning("ols_adapter.csv_error", path=str(csv_path), error=str(exc))

    if not scenario_results:
        return 0.0

    per_scenario = [sum(v) / len(v) for v in scenario_results.values() if v]
    return sum(per_scenario) / len(per_scenario) if per_scenario else 0.0


def extract_scores(results_dir: Path) -> dict:
    """Extract detailed scoring breakdown for analysis and reporting."""
    scenario_results: dict[str, list[bool]] = defaultdict(list)
    per_metric: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0, "error": 0})
    iterations: set[str] = set()

    for csv_path in sorted(results_dir.glob("iter_*/*/*.csv")):
        scenario = csv_path.parent.name
        iter_dir = csv_path.parent.parent.name
        iterations.add(iter_dir)
        try:
            with csv_path.open() as f:
                for row in csv.DictReader(f):
                    metric = row.get("metric_identifier", "")
                    result = row.get("result", "")
                    turn_id = row.get("turn_id", "")

                    if not metric:
                        continue

                    if result == "ERROR":
                        per_metric[metric]["error"] += 1
                        if "answer_correctness" in metric and turn_id:
                            scenario_results[scenario].append(False)
                    elif result == "PASS":
                        per_metric[metric]["pass"] += 1
                        if "answer_correctness" in metric and turn_id:
                            scenario_results[scenario].append(True)
                    elif result == "FAIL":
                        per_metric[metric]["fail"] += 1
                        if "answer_correctness" in metric and turn_id:
                            scenario_results[scenario].append(False)
        except (FileNotFoundError, csv.Error) as exc:
            log.warning("ols_adapter.csv_error", path=str(csv_path), error=str(exc))

    per_scenario = {k: (sum(v) / len(v) if v else 0.0) for k, v in scenario_results.items()}
    rates = list(per_scenario.values())
    mean_pass_rate = sum(rates) / len(rates) if rates else 0.0

    return {
        "mean_pass_rate": mean_pass_rate,
        "scenario_count": len(per_scenario),
        "pass_count": sum(1 for r in rates if r >= 1.0),
        "fail_count": sum(1 for r in rates if r < 1.0),
        "per_scenario": per_scenario,
        "per_metric": dict(per_metric),
        "iteration_count": len(iterations),
    }


def run_ols_eval(config_path: Path, *, is_reeval: bool = False) -> None:
    """Run OLS eval command and update run_state.json."""
    cfg = json.loads(config_path.read_text())
    eval_cmd = cfg["eval_command"]
    results_dir = Path(cfg["results_dir"])

    label = "Re-running" if is_reeval else "Running"
    print(f"{label} OLS eval: {eval_cmd}")

    result = subprocess.run(eval_cmd, shell=True, capture_output=True, text=True)  # noqa: S602
    print(result.stdout[-2000:])
    if result.returncode != 0:
        print(result.stderr[-1000:])

    score = compute_aggregate_score(results_dir)
    state = _load_state(config_path)
    score_before = state.get("current_score")

    if not is_reeval and state.get("baseline_score") is None:
        state["baseline_score"] = score
    state["current_score"] = score
    state["iteration_count"] = state.get("iteration_count", 0) + (1 if is_reeval else 0)
    state["score_history"].append(
        {
            "iteration": state["iteration_count"],
            "score": score,
            "timestamp": datetime.now().isoformat(),
        }
    )
    _save_state(config_path, state)

    if is_reeval:
        _append_improvement_record(config_path, score_before, score)
        print(f"Re-eval score: {score:.4f} (baseline: {state.get('baseline_score', 'N/A')})")
    else:
        print(f"Score: {score:.4f}")


def evaluate_score_gate(config_path: Path) -> None:
    """Check OLS eval score vs threshold — print pass/reloop verdict."""
    cfg = json.loads(config_path.read_text())
    state = _load_state(config_path)
    score = state.get("current_score", 0.0)
    threshold = cfg.get("score_threshold", 0.5)

    if score is not None and score >= threshold:
        print(f"pass: score {score:.4f} meets threshold {threshold}")
    else:
        print(f"reloop: score {score} below threshold {threshold}")


def evaluate_compare_gate(config_path: Path) -> None:
    """Compare before/after OLS eval scores — print pass/reloop verdict."""
    cfg = json.loads(config_path.read_text())
    state = _load_state(config_path)
    baseline = state.get("baseline_score", 0.0)
    current = state.get("current_score", 0.0)
    threshold = cfg.get("score_threshold", 0.5)

    if current is not None and current >= threshold:
        print(
            f"pass: score {current:.4f} meets threshold {threshold} "
            f"(baseline was {baseline:.4f})"
        )
    elif current is not None and current > baseline:
        print(
            f"reloop: improved {baseline:.4f} -> {current:.4f} "
            f"but still below threshold {threshold}"
        )
    else:
        print(f"reloop: no improvement ({baseline} -> {current}), try a different approach")


def write_failing_scenarios(config_path: Path) -> None:
    """Write failing scenario details to {task_id}_failing_scenarios.md."""
    cfg = json.loads(config_path.read_text())
    task_id = cfg["task_id"]
    results_dir = Path(cfg["results_dir"])

    csv_paths = sorted(results_dir.glob("iter_*/*/*.csv"))
    if not csv_paths:
        return

    # Collect failures grouped by scenario
    scenario_failures: dict[str, list[dict]] = defaultdict(list)
    scenario_pass_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "total": 0})
    conversation_metrics: dict[str, list[dict]] = defaultdict(list)

    for csv_path in csv_paths:
        scenario = csv_path.parent.name
        iter_dir = csv_path.parent.parent.name
        try:
            with csv_path.open() as f:
                for row in csv.DictReader(f):
                    metric = row.get("metric_identifier", "")
                    result = row.get("result", "")
                    turn_id = row.get("turn_id", "")

                    if not turn_id and metric:
                        conversation_metrics[scenario].append({
                            "metric": metric,
                            "score": row.get("score", ""),
                            "result": result,
                        })
                        continue

                    if "answer_correctness" not in metric:
                        continue

                    is_fail = result in ("FAIL", "ERROR")
                    scenario_pass_counts[scenario]["total"] += 1
                    if not is_fail:
                        scenario_pass_counts[scenario]["pass"] += 1
                    else:
                        scenario_failures[scenario].append({
                            "iter": iter_dir,
                            "turn_id": turn_id,
                            "query": row.get("query", ""),
                            "response": row.get("response", ""),
                            "score": row.get("score", ""),
                            "reason": row.get("reason", ""),
                        })
        except (FileNotFoundError, csv.Error):
            continue

    if not scenario_failures:
        return

    lines = [f"# Failing Scenarios for {task_id}\n"]

    for scenario, failures in sorted(scenario_failures.items()):
        counts = scenario_pass_counts[scenario]
        total = counts["total"]
        passed = counts["pass"]
        rate = passed / total if total else 0.0
        lines.append(f"## {scenario} (pass_rate={rate:.3f}, {passed}/{total} iterations passed)\n")

        for fail in failures:
            turn_label = fail["turn_id"]
            header = f"### {fail['iter']}"
            if turn_label and turn_label != scenario:
                header += f", Turn: {turn_label}"
            header += f" — FAIL (score={fail['score']})"
            lines.append(header)
            lines.append(f"**Query:** {fail['query']}")
            lines.append(f"**Response:** {fail['response']}")
            lines.append(f"**Score:** {fail['score']}")
            lines.append(f"**Reason:** {fail['reason']}\n")

        conv = conversation_metrics.get(scenario, [])
        if conv:
            lines.append("### Conversation Metrics")
            lines.append("| Metric | Score | Result |")
            lines.append("|--------|-------|--------|")
            for cm in conv:
                lines.append(f"| {cm['metric']} | {cm['score']} | {cm['result']} |")
            lines.append("")

        lines.append("---\n")

    out_path = config_path.parent / f"{task_id}_failing_scenarios.md"
    out_path.write_text("\n".join(lines))
    fail_count = len(scenario_failures)
    print(f"Wrote {fail_count} failing scenarios to {out_path.name}")


# backwards compat — moved to factory.knowledge.gates
from factory.knowledge.gates import evaluate_insights_gate as evaluate_insights_gate  # noqa: E402, F401
from factory.knowledge.gates import generate_report as generate_report  # noqa: E402, F401
