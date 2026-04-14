"""Vault digest — scan the Obsidian vault and summarize factory activity."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from factory.obsidian.notes import _get_vault_path, _PROJECTS_DIR


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML frontmatter key-value pairs from a markdown note."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    result: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("- "):
            continue  # skip list items (e.g. tags)
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _parse_dashboard(text: str) -> dict[str, str]:
    """Extract key fields from a project dashboard note."""
    info: dict[str, str] = {}

    m = re.search(r"\*\*State\*\*:\s*(.+)", text)
    if m:
        info["state"] = m.group(1).strip()

    m = re.search(r"\*\*Current Score\*\*:\s*(\S+)", text)
    if m:
        info["score"] = m.group(1)

    m = re.search(r"\*\*Experiments Run\*\*:\s*(\d+)", text)
    if m:
        info["experiments"] = m.group(1)

    m = re.search(r"\*\*Kept\*\*:\s*(\d+)", text)
    if m:
        info["kept"] = m.group(1)

    m = re.search(r"## Description\n(.+?)(?:\n##|\Z)", text, re.DOTALL)
    if m:
        info["description"] = m.group(1).strip().split("\n")[0]

    return info


def _parse_experiment_note(text: str) -> dict[str, str]:
    """Extract key fields from an experiment note."""
    fm = _parse_frontmatter(text)
    info: dict[str, str] = {}
    info["date"] = fm.get("date", "")
    info["verdict"] = fm.get("verdict", "")
    info["experiment_id"] = fm.get("experiment_id", "")

    # Get hypothesis from heading
    m = re.search(r"# Experiment #\d+:\s*(.+)", text)
    if m:
        info["hypothesis"] = m.group(1).strip()

    # Get result line — handles both "—" and "--" separators
    m = re.search(r"\*\*(\w+)\*\*\s*(?:—|--)\s*score changed from (\S+) to (\S+) \(([^)]+)\)", text)
    if m:
        info["score_before"] = m.group(2)
        info["score_after"] = m.group(3)
        info["delta"] = m.group(4)

    # Get change summary
    m = re.search(r"## What Changed\n(.+?)(?:\n##|\Z)", text, re.DOTALL)
    if m:
        info["summary"] = m.group(1).strip()

    return info


def scan_vault(
    target_date: date | None = None,
    days: int = 7,
    vault_path: Path | None = None,
) -> dict[str, dict]:
    """Scan the Obsidian vault and collect project summaries.

    Args:
        target_date: If set, only include experiments from this specific date.
        days: If target_date is None, include experiments from the last N days.
        vault_path: Override vault path (for testing). Uses default if None.

    Returns:
        Dict mapping project name to project info with filtered experiments.
    """
    vault = vault_path if vault_path is not None else _get_vault_path()
    projects_dir = vault / _PROJECTS_DIR
    if not projects_dir.exists():
        return {}

    if target_date is not None:
        start_date = target_date
        end_date = target_date
    else:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

    projects: dict[str, dict] = {}

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        project_name = project_dir.name

        # Read dashboard
        dashboard_path = project_dir / f"{project_name}.md"
        dashboard_info: dict[str, str] = {}
        if dashboard_path.exists():
            dashboard_info = _parse_dashboard(dashboard_path.read_text(errors="replace"))

        # Read experiment notes
        experiments_dir = project_dir / "Experiments"
        experiments: list[dict[str, str]] = []
        if experiments_dir.exists():
            for note_path in sorted(experiments_dir.iterdir()):
                if not note_path.suffix == ".md":
                    continue
                text = note_path.read_text(errors="replace")
                exp = _parse_experiment_note(text)
                if not exp.get("date"):
                    continue

                try:
                    exp_date = datetime.strptime(exp["date"], "%Y-%m-%d").date()
                except ValueError:
                    continue

                if start_date <= exp_date <= end_date:
                    experiments.append(exp)

        if experiments or target_date is None:
            projects[project_name] = {
                "dashboard": dashboard_info,
                "experiments": experiments,
            }

    return projects


def format_digest(
    projects: dict[str, dict],
    target_date: date | None = None,
    days: int = 7,
) -> str:
    """Format the scanned vault data into a readable digest."""
    if target_date is not None:
        title = f"Factory Digest — {target_date.isoformat()}"
    else:
        end = date.today()
        start = end - timedelta(days=days)
        title = f"Factory Digest — {start.isoformat()} to {end.isoformat()}"

    lines = [f"# {title}", ""]

    if not projects:
        lines.append("No projects found in the vault.")
        return "\n".join(lines)

    active_count = sum(1 for p in projects.values() if p["experiments"])
    total_exps = sum(len(p["experiments"]) for p in projects.values())

    lines.append(f"**{len(projects)} projects** in vault, "
                 f"**{active_count}** with activity, "
                 f"**{total_exps}** experiments in period")
    lines.append("")

    for name, info in projects.items():
        dash = info["dashboard"]
        exps = info["experiments"]

        lines.append(f"## {name}")

        if dash.get("description"):
            lines.append(f"_{dash['description']}_")
            lines.append("")

        status_parts = []
        if dash.get("score"):
            status_parts.append(f"Score: {dash['score']}")
        if dash.get("experiments"):
            status_parts.append(f"{dash['experiments']} total experiments")
        if dash.get("kept"):
            status_parts.append(f"{dash['kept']} kept")
        if status_parts:
            lines.append(f"**Status**: {', '.join(status_parts)}")
            lines.append("")

        if not exps:
            lines.append("_No experiments in this period._")
        else:
            lines.append(f"### Built ({len(exps)} experiments)")
            lines.append("")
            for exp in exps:
                verdict = exp.get("verdict", "?").upper()
                hyp = exp.get("hypothesis", "?")
                exp_id = exp.get("experiment_id", "?")
                delta = exp.get("delta", "")
                summary = exp.get("summary", "")

                lines.append(f"- **#{exp_id}** [{verdict}] {hyp}")
                if delta:
                    lines.append(f"  - Delta: {delta}")
                if summary:
                    # Collapse multi-line markdown into a single line
                    flat = re.sub(r"\n[-*]\s*", "; ", summary)
                    flat = re.sub(r"\n+", " ", flat).strip()
                    flat = flat.lstrip("-* ")
                    short = flat[:120] + "..." if len(flat) > 120 else flat
                    lines.append(f"  - {short}")

        lines.append("")

    return "\n".join(lines)
