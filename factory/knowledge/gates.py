"""Generic gate evaluators and report generation for knowledge workflows."""

from __future__ import annotations

import json
from pathlib import Path


def evaluate_insights_gate(config_path: Path) -> None:
    """Check insight quality — print pass/reloop verdict."""
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


def generate_report(config_path: Path) -> None:
    """Generate the final insights report with score progression."""
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

    report += _score_progression_section(knowledge_dir)
    report += _changes_applied_section(knowledge_dir, task_id)

    (knowledge_dir / f"{task_id}_report.md").write_text(report)
    print(report)


def _score_progression_section(knowledge_dir: Path) -> str:
    state_path = knowledge_dir / "run_state.json"
    if not state_path.exists():
        return ""

    state = json.loads(state_path.read_text())
    history = state.get("score_history", [])
    if not history:
        return ""

    lines = [
        "\n\n## Score Progression\n",
        "| Iteration | Score | Delta |",
        "|-----------|-------|-------|",
    ]
    prev = None
    for entry in history:
        iteration = entry.get("iteration", "?")
        score = entry.get("score", 0.0)
        label = "Baseline" if iteration == 0 else str(iteration)
        delta = (
            f"+{score - prev:.4f}"
            if prev is not None and score > prev
            else (f"{score - prev:.4f}" if prev is not None else "-")
        )
        lines.append(f"| {label} | {score:.4f} | {delta} |")
        prev = score

    return "\n".join(lines) + "\n"


def _changes_applied_section(knowledge_dir: Path, task_id: str) -> str:
    history_path = knowledge_dir / f"{task_id}_improvements.jsonl"
    if not history_path.exists():
        return ""

    lines = ["\n## Changes Applied\n"]
    for line in history_path.read_text().strip().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        iteration = record.get("iteration", "?")
        before = record.get("score_before")
        after = record.get("score_after", 0)
        summary = record.get("changes_summary", "")
        lines.append(f"### Iteration {iteration}")
        if before is not None:
            lines.append(f"Score: {before:.4f} -> {after:.4f}\n")
        if summary:
            first_line = summary.split("\n")[0][:200]
            lines.append(f"{first_line}\n")

    return "\n".join(lines) + "\n"
