"""Study prior interaction logs to inform factory hypotheses."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _path_to_slug(project_path: Path) -> str:
    """Convert a project path to Claude's directory slug format.

    Claude replaces all non-alphanumeric chars (except -) with -.
    e.g. /home/dev/projects/my-app
      -> -home-dev-projects-my-app
    """
    return "".join(c if c.isalnum() or c == "-" else "-" for c in str(project_path))


def _find_log_files(project_path: Path) -> list[Path]:
    """Find Claude conversation logs matching this project."""
    claude_projects = Path.home() / ".claude" / "projects"
    slug = _path_to_slug(project_path.resolve())

    project_dir = claude_projects / slug
    if not project_dir.exists():
        logger.warning("No Claude project directory found at %s", project_dir)
        return []

    return sorted(project_dir.glob("*.jsonl"))


def _extract_messages(log_file: Path) -> list[dict]:
    """Extract user messages and errors from a JSONL log file."""
    messages: list[dict] = []
    with open(log_file) as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            content = msg.get("message", {}).get("content", "")

            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block["text"]

            text = text.strip()
            if not text or len(text) > 2000:
                continue

            # Skip system prompts and skill loads
            if text.startswith("Base directory") or text.startswith("<task-notification"):
                continue

            if msg_type == "user":
                messages.append({"role": "user", "text": text[:500]})
            elif msg_type == "assistant":
                # Extract error mentions
                error_keywords = ["error", "failed", "bug", "fix", "broken"]
                if any(kw in text.lower() for kw in error_keywords):
                    error_lines = [
                        line.strip()
                        for line in text.split("\n")
                        if any(kw in line.lower() for kw in error_keywords)
                        and line.strip()
                        and len(line.strip()) < 300
                    ]
                    for el in error_lines[:3]:
                        messages.append({"role": "error", "text": el})

    return messages


def study_project(project_path: Path) -> str:
    """Read interaction logs and produce an observations summary."""
    log_files = _find_log_files(project_path)
    if not log_files:
        return "No interaction logs found."

    all_messages: list[dict] = []
    for lf in log_files:
        all_messages.extend(_extract_messages(lf))

    # Categorize
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    errors = [m for m in all_messages if m["role"] == "error"]

    lines = [
        f"# Interaction Study — {project_path.name}",
        "",
        f"Analyzed {len(log_files)} conversation log(s), {len(all_messages)} relevant messages.",
        "",
        f"## User Messages ({len(user_msgs)})",
    ]
    for m in user_msgs:
        lines.append(f"- {m['text'][:200]}")

    lines.extend([
        "",
        f"## Errors and Issues ({len(errors)})",
    ])
    for m in errors:
        lines.append(f"- {m['text'][:200]}")

    return "\n".join(lines)
