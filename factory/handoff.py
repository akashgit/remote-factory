"""Generate a structured handoff brief for cross-session resume."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path


def generate_handoff(project_path: Path) -> str:
    """Read 9 data sources and synthesize a structured markdown handoff brief."""
    project_path = project_path.resolve()
    factory_dir = project_path / ".factory"
    sections: list[str] = []

    sections.append(_section_project(project_path))
    sections.append(_section_current_state(factory_dir))
    sections.append(_section_score_trajectory(factory_dir))
    sections.append(_section_in_progress(factory_dir))
    sections.append(_section_pending(factory_dir))
    sections.append(_section_recent_activity(factory_dir))
    sections.append(_section_next_steps(factory_dir))

    return "\n\n".join(sections) + "\n"


def _section_project(project_path: Path) -> str:
    lines = [f"# Handoff Brief: {project_path.name}", ""]

    # Git state
    branch = _git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
    lines.append(f"**Branch:** {branch or 'unknown'}")

    commits = _git(project_path, "log", "--oneline", "-5")
    if commits:
        lines.append("")
        lines.append("**Recent commits:**")
        for c in commits.strip().splitlines():
            lines.append(f"- {c}")

    # Config
    config_path = project_path / ".factory" / "config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text())
            if config.get("goal"):
                lines.append(f"\n**Goal:** {config['goal']}")
        except (json.JSONDecodeError, KeyError):
            pass

    return "\n".join(lines)


def _section_current_state(factory_dir: Path) -> str:
    lines = ["## Current State"]

    # Checkpoint
    checkpoint_path = factory_dir / "checkpoint.json"
    if checkpoint_path.is_file():
        try:
            ckpt = json.loads(checkpoint_path.read_text())
            lines.append(f"- **Mode:** {ckpt.get('mode', 'unknown')}")
            if ckpt.get("active_experiment_id"):
                lines.append(f"- **Active experiment:** {ckpt['active_experiment_id']}")
            if ckpt.get("current_hypothesis"):
                lines.append(f"- **Hypothesis:** {ckpt['current_hypothesis']}")
            if ckpt.get("completed_agents"):
                lines.append(f"- **Completed agents:** {', '.join(ckpt['completed_agents'])}")
            if ckpt.get("pending_agents"):
                lines.append(f"- **Pending agents:** {', '.join(ckpt['pending_agents'])}")
        except (json.JSONDecodeError, KeyError):
            lines.append("- Checkpoint file exists but could not be parsed")
    else:
        lines.append("- No checkpoint found")

    # Cycle state
    cycle_path = factory_dir / "state" / "cycle.json"
    if cycle_path.is_file():
        try:
            cycle = json.loads(cycle_path.read_text())
            lines.append(f"- **Cycle ID:** {cycle.get('cycle_id', 'unknown')}")
            if cycle.get("respawns"):
                lines.append(f"- **Respawns:** {cycle['respawns']}")
        except (json.JSONDecodeError, KeyError):
            pass

    if len(lines) == 1:
        lines.append("- No state data available")

    return "\n".join(lines)


def _section_score_trajectory(factory_dir: Path) -> str:
    lines = ["## Score Trajectory"]

    tsv_path = factory_dir / "results.tsv"
    if not tsv_path.is_file():
        lines.append("- No experiment history")
        return "\n".join(lines)

    try:
        rows = tsv_path.read_text().strip().splitlines()
        if len(rows) <= 1:
            lines.append("- No experiments recorded")
            return "\n".join(lines)

        header = rows[0].split("\t")
        data_rows = rows[1:]

        score_after_idx = None
        verdict_idx = None
        for i, col in enumerate(header):
            if col == "score_after":
                score_after_idx = i
            elif col == "verdict":
                verdict_idx = i

        kept = 0
        reverted = 0
        scores: list[float] = []

        for row in data_rows:
            cols = row.split("\t")
            if verdict_idx is not None and verdict_idx < len(cols):
                v = cols[verdict_idx]
                if v == "keep":
                    kept += 1
                elif v == "revert":
                    reverted += 1
            if score_after_idx is not None and score_after_idx < len(cols):
                try:
                    scores.append(float(cols[score_after_idx]))
                except (ValueError, IndexError):
                    pass

        lines.append(f"- **Total experiments:** {len(data_rows)}")
        lines.append(f"- **Kept:** {kept}, **Reverted:** {reverted}")
        if scores:
            lines.append(f"- **Latest score:** {scores[-1]:.3f}")
            if len(scores) >= 2:
                lines.append(f"- **Previous score:** {scores[-2]:.3f}")
    except Exception:
        lines.append("- Could not parse experiment history")

    return "\n".join(lines)


def _section_in_progress(factory_dir: Path) -> str:
    lines = ["## What's In Progress"]

    # Review verdicts show recent agent work
    reviews_dir = factory_dir / "reviews"
    if reviews_dir.is_dir():
        latest_files = sorted(reviews_dir.glob("*-latest.md"))
        if latest_files:
            for f in latest_files[-3:]:
                role = f.stem.replace("-latest", "")
                lines.append(f"- **{role}** agent has output (see .factory/reviews/{f.name})")
        else:
            lines.append("- No recent agent outputs")
    else:
        lines.append("- No review artifacts")

    # Strategy
    strategy_path = factory_dir / "strategy" / "current.md"
    if strategy_path.is_file():
        content = strategy_path.read_text().strip()
        if content:
            preview = content[:200].replace("\n", " ")
            lines.append(f"- **Active strategy:** {preview}...")

    return "\n".join(lines)


def _section_pending(factory_dir: Path) -> str:
    lines = ["## What's Pending"]

    # Backlog
    backlog_path = factory_dir / "strategy" / "backlog.md"
    if backlog_path.is_file():
        content = backlog_path.read_text().strip()
        if content:
            items = [
                line.lstrip("- ").strip()
                for line in content.splitlines()
                if line.strip().startswith("- ")
            ]
            if items:
                lines.append(f"- **Backlog items:** {len(items)}")
                for item in items[:5]:
                    lines.append(f"  - {item}")
                if len(items) > 5:
                    lines.append(f"  - ... and {len(items) - 5} more")
            else:
                lines.append("- Backlog is empty")
        else:
            lines.append("- Backlog is empty")
    else:
        lines.append("- No backlog file")

    return "\n".join(lines)


def _section_recent_activity(factory_dir: Path) -> str:
    lines = ["## Recent Activity"]

    events_path = factory_dir / "events.jsonl"
    if not events_path.is_file():
        lines.append("- No events log")
        return "\n".join(lines)

    try:
        all_lines = events_path.read_text().strip().splitlines()
        recent = all_lines[-10:] if len(all_lines) > 10 else all_lines
        if not recent:
            lines.append("- Events log is empty")
            return "\n".join(lines)

        for event_line in recent:
            try:
                event = json.loads(event_line)
                ts = event.get("timestamp", "")
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts)
                        ts = dt.strftime("%H:%M:%S")
                    except (ValueError, TypeError):
                        ts = str(ts)[:8]
                etype = event.get("type", "unknown")
                lines.append(f"- [{ts}] {etype}")
            except json.JSONDecodeError:
                continue
    except Exception:
        lines.append("- Could not parse events log")

    return "\n".join(lines)


def _section_next_steps(factory_dir: Path) -> str:
    lines = ["## Recommended Next Steps"]

    checkpoint_path = factory_dir / "checkpoint.json"
    has_checkpoint = checkpoint_path.is_file()

    if has_checkpoint:
        try:
            ckpt = json.loads(checkpoint_path.read_text())
            pending = ckpt.get("pending_agents", [])
            mode = ckpt.get("mode", "improve")
            if pending:
                lines.append(f"1. Resume {mode} mode, continue with: {', '.join(pending)}")
            else:
                lines.append(f"1. Resume {mode} mode from checkpoint")
            lines.append(f"2. Run: `factory ceo {factory_dir.parent} --mode {mode}`")
        except (json.JSONDecodeError, KeyError):
            lines.append("1. Checkpoint exists but is corrupt; consider clearing and starting fresh")
            lines.append(f"2. Run: `factory ceo {factory_dir.parent}`")
    else:
        lines.append("1. No checkpoint; start a new cycle")
        lines.append(f"2. Run: `factory ceo {factory_dir.parent}`")

    return "\n".join(lines)


def _git(project_path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
