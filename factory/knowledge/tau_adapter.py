"""Tau-bench simulation adapter — parse structured simulation JSON into triplets."""

from __future__ import annotations

import json
import re
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
    return re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")


def _entity(entity_type: EntityType, name: str, **attrs: str | float | bool) -> Entity:
    return Entity(
        id=f"{entity_type.value}:{_slugify(name)}",
        type=entity_type,
        name=name,
        attributes=dict(attrs) if attrs else {},
    )


def parse_simulation(path: Path, task_context: str) -> list[Triplet]:
    """Parse a tau-bench simulation JSON file into knowledge graph triplets.

    Reads the top-level ``simulations`` array and extracts triplets covering
    tool usage, failures, evaluation breakdowns, and causal chains.
    """
    data = json.loads(path.read_text())
    simulations = data.get("simulations", [])
    if not simulations:
        log.warning("tau_adapter.no_simulations", path=str(path))
        return []

    triplets: list[Triplet] = []
    for sim in simulations:
        triplets.extend(_extract_from_simulation(sim, task_context))

    log.info(
        "tau_adapter.parsed",
        path=str(path),
        simulations=len(simulations),
        triplets=len(triplets),
    )
    return triplets


def _extract_from_simulation(sim: dict, task_context: str) -> list[Triplet]:
    task_id = str(sim.get("task_id", "unknown"))
    now = datetime.now()
    source = f"tau_bench:task_{task_id}"
    agent = _entity(EntityType.AGENT, "airline_agent")
    task = _entity(EntityType.TASK, f"task_{task_id}", description=task_context)
    triplets: list[Triplet] = []

    reward_info = sim.get("reward_info", {})
    reward = reward_info.get("reward", 0.0)

    # task outcome
    triplets.append(
        Triplet(
            subject=agent,
            predicate=PredicateType.SUCCEEDS_AT if reward >= 1.0 else PredicateType.FAILS_AT,
            object=task,
            confidence=1.0,
            source=source,
            timestamp=now,
            evidence=f"reward={reward}",
        )
    )

    # tool calls from messages
    triplets.extend(_extract_tool_calls(sim.get("messages", []), agent, task_id, source, now))

    # evaluation breakdown
    triplets.extend(_extract_action_checks(reward_info, agent, task_id, source, now))
    triplets.extend(_extract_nl_assertions(reward_info, agent, task_id, source, now))
    triplets.extend(_extract_db_check(reward_info, agent, task_id, source, now))
    triplets.extend(_extract_reward_breakdown(reward_info, task_id, source, now))

    # termination reason
    term = sim.get("termination_reason", "")
    if term in ("too_many_errors", "max_steps"):
        triplets.append(
            Triplet(
                subject=agent,
                predicate=PredicateType.FAILS_WITH,
                object=_entity(EntityType.ERROR, term),
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=f"termination_reason={term}",
            )
        )

    return triplets


def _extract_tool_calls(
    messages: list[dict],
    agent: Entity,
    task_id: str,
    source: str,
    now: datetime,
) -> list[Triplet]:
    triplets: list[Triplet] = []
    prev_action: Entity | None = None
    call_index = 0

    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            continue

        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tool_name = tc.get("name", f"unknown_tool_{call_index}")
            tool = _entity(EntityType.TOOL, tool_name)
            action = _entity(
                EntityType.ACTION,
                f"{tool_name}_call_{call_index}",
                task_id=task_id,
                index=float(call_index),
            )

            triplets.append(
                Triplet(
                    subject=agent,
                    predicate=PredicateType.CALLS,
                    object=tool,
                    confidence=1.0,
                    source=source,
                    timestamp=now,
                    evidence=f"task {task_id}: {tool_name}({json.dumps(tc.get('arguments', {}))[:120]})",
                )
            )

            if prev_action is not None:
                triplets.append(
                    Triplet(
                        subject=prev_action,
                        predicate=PredicateType.PRECEDES,
                        object=action,
                        source=source,
                        timestamp=now,
                    )
                )

            # look ahead for the tool response
            tool_response = _find_tool_response(messages, i + 1)
            if tool_response is not None:
                if tool_response.get("error"):
                    error_content = str(tool_response.get("content", ""))[:80]
                    triplets.append(
                        Triplet(
                            subject=action,
                            predicate=PredicateType.FAILS_WITH,
                            object=_entity(
                                EntityType.ERROR, error_content or "tool_error", tool=tool_name
                            ),
                            confidence=1.0,
                            source=source,
                            timestamp=now,
                            evidence=str(tool_response.get("content", ""))[:200],
                        )
                    )
                else:
                    triplets.append(
                        Triplet(
                            subject=action,
                            predicate=PredicateType.PRODUCES,
                            object=_entity(
                                EntityType.OUTCOME, f"{tool_name}_result_{call_index}", success=True
                            ),
                            source=source,
                            timestamp=now,
                            evidence=str(tool_response.get("content", ""))[:200],
                        )
                    )

            prev_action = action
            call_index += 1

    return triplets


def _find_tool_response(messages: list[dict], start_index: int) -> dict | None:
    for j in range(start_index, min(start_index + 3, len(messages))):
        if messages[j].get("role") == "tool":
            return messages[j]
    return None


def _extract_action_checks(
    reward_info: dict,
    agent: Entity,
    task_id: str,
    source: str,
    now: datetime,
) -> list[Triplet]:
    checks = reward_info.get("action_checks")
    if not checks:
        return []

    triplets: list[Triplet] = []
    for check in checks:
        action_def = check.get("action", {})
        action_name = action_def.get("name", "unknown_action")
        matched = check.get("action_match", False)
        expected_action = _entity(
            EntityType.CONCEPT,
            f"expected_{action_name}",
            action_id=str(action_def.get("action_id", "")),
        )

        triplets.append(
            Triplet(
                subject=agent,
                predicate=PredicateType.SUCCEEDS_AT if matched else PredicateType.FAILS_AT,
                object=expected_action,
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=f"task {task_id}: expected {action_name}({json.dumps(action_def.get('arguments', {}))[:100]}), matched={matched}",
            )
        )

        # causal link: missing action → task failure
        if not matched:
            task_entity = _entity(EntityType.TASK, f"task_{task_id}")
            triplets.append(
                Triplet(
                    subject=expected_action,
                    predicate=PredicateType.CAUSES,
                    object=_entity(EntityType.OUTCOME, f"task_{task_id}_failure"),
                    confidence=0.9,
                    source=source,
                    timestamp=now,
                    evidence=f"missing expected action {action_name} caused task {task_id} failure",
                )
            )

    return triplets


def _extract_nl_assertions(
    reward_info: dict,
    agent: Entity,
    task_id: str,
    source: str,
    now: datetime,
) -> list[Triplet]:
    assertions = reward_info.get("nl_assertions")
    if not assertions:
        return []

    triplets: list[Triplet] = []
    for assertion in assertions:
        text = assertion.get("nl_assertion", "")
        met = assertion.get("met", False)
        justification = assertion.get("justification", "")

        assertion_entity = _entity(EntityType.CONCEPT, text[:60])

        triplets.append(
            Triplet(
                subject=agent,
                predicate=PredicateType.SUCCEEDS_AT if met else PredicateType.FAILS_AT,
                object=assertion_entity,
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=justification[:300],
            )
        )

        if not met:
            triplets.append(
                Triplet(
                    subject=assertion_entity,
                    predicate=PredicateType.CAUSES,
                    object=_entity(EntityType.OUTCOME, f"task_{task_id}_failure"),
                    confidence=0.9,
                    source=source,
                    timestamp=now,
                    evidence=f"failed assertion: {text}",
                )
            )

    return triplets


def _extract_db_check(
    reward_info: dict,
    agent: Entity,
    task_id: str,
    source: str,
    now: datetime,
) -> list[Triplet]:
    db_check = reward_info.get("db_check")
    if not db_check:
        return []

    db_match = db_check.get("db_match", True)
    consistency = _entity(EntityType.CONCEPT, "db_consistency")

    triplets = [
        Triplet(
            subject=agent,
            predicate=PredicateType.SUCCEEDS_AT if db_match else PredicateType.FAILS_AT,
            object=consistency,
            confidence=1.0,
            source=source,
            timestamp=now,
            evidence=f"task {task_id}: db_match={db_match}, db_reward={db_check.get('db_reward', 0)}",
        )
    ]

    if not db_match:
        triplets.append(
            Triplet(
                subject=consistency,
                predicate=PredicateType.CAUSES,
                object=_entity(EntityType.OUTCOME, f"task_{task_id}_failure"),
                confidence=0.9,
                source=source,
                timestamp=now,
                evidence="database state mismatch after agent actions",
            )
        )

    return triplets


def _extract_reward_breakdown(
    reward_info: dict,
    task_id: str,
    source: str,
    now: datetime,
) -> list[Triplet]:
    breakdown = reward_info.get("reward_breakdown")
    if not breakdown:
        return []

    total = _entity(EntityType.OUTCOME, f"task_{task_id}_reward")
    triplets: list[Triplet] = []
    for component, score in breakdown.items():
        triplets.append(
            Triplet(
                subject=_entity(
                    EntityType.CONCEPT, f"{component.lower()}_score", score=float(score)
                ),
                predicate=PredicateType.PART_OF,
                object=total,
                confidence=1.0,
                source=source,
                timestamp=now,
                evidence=f"{component}={score}",
            )
        )

    return triplets


def run_tau_eval(config_path: Path, *, is_reeval: bool = False) -> None:
    """Run tau-bench and record score in task_config.json.

    Called by the run_eval and re_eval FnNodes.
    """
    import subprocess

    cfg = json.loads(config_path.read_text())
    tau_cmd = cfg["tau_command"]
    sim_path = Path(cfg["simulation_path"])

    if sim_path.exists():
        sim_path.unlink()

    label = "Re-running" if is_reeval else "Running"
    print(f"{label} tau-bench: {tau_cmd}")

    result = subprocess.run(tau_cmd, shell=True, capture_output=True, text=True)  # noqa: S602
    print(result.stdout[-2000:])
    if result.returncode != 0:
        print(result.stderr[-1000:])

    if not sim_path.exists():
        print("Error: simulation output not found")
        raise SystemExit(1)

    score = compute_aggregate_score(sim_path)
    if not is_reeval and cfg.get("baseline_score") is None:
        cfg["baseline_score"] = score
    cfg["current_score"] = score
    config_path.write_text(json.dumps(cfg, indent=2))

    baseline = cfg.get("baseline_score", "N/A")
    if is_reeval:
        print(f"Re-eval score: {score:.4f} (baseline: {baseline})")
    else:
        print(f"Score: {score:.4f}")


def evaluate_insights_gate(config_path: Path) -> None:
    """Check insight quality — print pass/reloop verdict.

    Called by the gate_insights FnNode.
    """
    cfg = json.loads(config_path.read_text())
    task_id = cfg["task_id"]
    threshold = cfg.get("insight_threshold", 2)
    conf_threshold = cfg.get("confidence_threshold", 0.5)

    p = config_path.parent / f"{task_id}_insights.json"
    if not p.exists():
        print("reloop: no insights file found")
        return

    insights = json.loads(p.read_text())
    if len(insights) < threshold:
        print(f"reloop: only {len(insights)} insights, need at least {threshold}")
        return

    avg_conf = sum(i.get("confidence", 0) for i in insights) / len(insights) if insights else 0
    if avg_conf < conf_threshold:
        print(f"reloop: average confidence {avg_conf:.2f} below {conf_threshold}")
        return

    print(f"pass: {len(insights)} insights with avg confidence {avg_conf:.2f}")


def evaluate_score_gate(config_path: Path) -> None:
    """Check tau-bench score vs threshold — print pass/reloop verdict.

    Called by the gate_score FnNode.
    """
    cfg = json.loads(config_path.read_text())
    score = cfg.get("current_score", 0.0)
    threshold = cfg.get("score_threshold", 0.8)

    if score is not None and score >= threshold:
        print(f"pass: score {score:.4f} meets threshold {threshold}")
    else:
        print(f"reloop: score {score} below threshold {threshold}")


def evaluate_compare_gate(config_path: Path) -> None:
    """Compare before/after scores — print pass/reloop verdict.

    Called by the gate_compare FnNode.
    """
    cfg = json.loads(config_path.read_text())
    baseline = cfg.get("baseline_score", 0.0)
    current = cfg.get("current_score", 0.0)
    threshold = cfg.get("score_threshold", 0.8)

    if current is not None and current >= threshold:
        print(
            f"pass: score {current:.4f} meets threshold {threshold} (baseline was {baseline:.4f})"
        )
    elif current is not None and current > baseline:
        print(
            f"reloop: improved {baseline:.4f} -> {current:.4f} "
            f"but still below threshold {threshold}"
        )
    else:
        print(f"reloop: no improvement ({baseline} -> {current}), try a different approach")


def generate_report(config_path: Path) -> None:
    """Generate the final insights report.

    Called by the report FnNode.
    """
    from factory.knowledge.insight import Insight, format_insights
    from factory.knowledge.models import KnowledgeGraph

    cfg = json.loads(config_path.read_text())
    task_id = cfg["task_id"]
    knowledge_dir = config_path.parent

    graph_path = knowledge_dir / f"{task_id}.json"
    insights_path = knowledge_dir / f"{task_id}_insights.json"

    if not graph_path.exists() or not insights_path.exists():
        print("No graph or insights to report")
        return

    graph = KnowledgeGraph.model_validate(json.loads(graph_path.read_text()), strict=False)
    insights = [
        Insight.model_validate(i, strict=False) for i in json.loads(insights_path.read_text())
    ]
    report = format_insights(insights, graph)
    (knowledge_dir / f"{task_id}_report.md").write_text(report)
    print(report)


def compute_aggregate_score(path: Path) -> float:
    """Compute average reward across all simulations in a results file."""
    data = json.loads(path.read_text())
    sims = data.get("simulations", [])
    if not sims:
        return 0.0
    rewards = [s.get("reward_info", {}).get("reward", 0.0) for s in sims]
    return sum(rewards) / len(rewards)


def extract_scores(path: Path) -> dict:
    """Extract detailed scoring breakdown from a simulation results file."""
    data = json.loads(path.read_text())
    sims = data.get("simulations", [])
    if not sims:
        return {
            "mean_reward": 0.0,
            "task_count": 0,
            "pass_count": 0,
            "fail_count": 0,
            "per_task": {},
        }

    per_task: dict[str, float] = {}
    for sim in sims:
        tid = str(sim.get("task_id", "unknown"))
        reward = sim.get("reward_info", {}).get("reward", 0.0)
        per_task[tid] = reward

    rewards = list(per_task.values())
    return {
        "mean_reward": sum(rewards) / len(rewards),
        "task_count": len(rewards),
        "pass_count": sum(1 for r in rewards if r >= 1.0),
        "fail_count": sum(1 for r in rewards if r < 1.0),
        "per_task": per_task,
    }
