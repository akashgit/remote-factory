"""Study prior interaction logs to inform factory hypotheses."""

from __future__ import annotations

import json
import logging
import re
import subprocess
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


def _extract_keywords(project_path: Path) -> list[str]:
    """Extract search keywords from a project's README or pyproject.toml."""
    # Try README first
    readme = project_path / "README.md"
    if readme.exists():
        text = readme.read_text(errors="replace")[:2000]
        # Use the first heading and first paragraph as keyword source
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        # Strip markdown heading markers
        lines = [re.sub(r"^#+\s*", "", ln) for ln in lines[:5]]
        text = " ".join(lines)
    else:
        # Fall back to pyproject.toml name + description
        pyproject = project_path / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(errors="replace")
            name_match = re.search(r'name\s*=\s*"([^"]+)"', content)
            desc_match = re.search(r'description\s*=\s*"([^"]+)"', content)
            parts = []
            if name_match:
                parts.append(name_match.group(1).replace("-", " "))
            if desc_match:
                parts.append(desc_match.group(1))
            text = " ".join(parts) if parts else ""
        else:
            text = project_path.name.replace("-", " ").replace("_", " ")

    if not text:
        return []

    # Remove common stop words and short tokens, keep meaningful words
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "and",
        "but", "or", "nor", "not", "so", "yet", "both", "either", "neither",
        "this", "that", "these", "those", "it", "its", "my", "your", "his",
        "her", "our", "their", "what", "which", "who", "whom", "how",
    }
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    keywords = [w for w in words if w not in stop_words]
    # Deduplicate while preserving order, return up to 5
    seen: set[str] = set()
    unique: list[str] = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique.append(w)
        if len(unique) >= 5:
            break
    return unique


def _search_similar_projects(project_path: Path) -> list[dict]:
    """Search GitHub for similar projects using `gh search repos`.

    Returns top 5 results as dicts with keys: name, url, description, stars.
    Gracefully returns empty list if gh is not available or search fails.
    """
    keywords = _extract_keywords(project_path)
    if not keywords:
        return []

    query = " ".join(keywords)
    try:
        result = subprocess.run(
            [
                "gh", "search", "repos", query,
                "--limit", "5",
                "--json", "fullName,url,description,stargazersCount",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("gh CLI not available or search timed out")
        return []

    if result.returncode != 0:
        logger.debug("gh search repos failed: %s", result.stderr)
        return []

    try:
        repos = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    return [
        {
            "name": r.get("fullName", ""),
            "url": r.get("url", ""),
            "description": (r.get("description") or "")[:200],
            "stars": r.get("stargazersCount", 0),
        }
        for r in repos[:5]
    ]


def _read_obsidian_notes(project_name: str) -> list[str]:
    """Read Obsidian vault notes for this project.

    Returns a list of note summaries (first 200 chars of each note).
    Gracefully returns empty list if vault doesn't exist.
    """
    from factory.obsidian.notes import _get_vault_path, _FACTORY_DIR

    vault = _get_vault_path()
    if not vault.exists():
        return []

    # Search across Experiments, Projects, and Strategies
    summaries: list[str] = []
    for subdir in ["Experiments", "Projects", "Strategies"]:
        notes_dir = vault / _FACTORY_DIR / subdir
        if not notes_dir.exists():
            continue
        for note_path in sorted(notes_dir.glob(f"{project_name}*.md")):
            try:
                content = note_path.read_text(errors="replace")
                # Skip frontmatter
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end != -1:
                        content = content[end + 3:].strip()
                summary = content[:200].strip()
                if summary:
                    summaries.append(summary)
            except OSError:
                continue

    return summaries


def study_project(project_path: Path) -> str:
    """Read interaction logs and produce an observations summary."""
    log_files = _find_log_files(project_path)

    all_messages: list[dict] = []
    for lf in log_files:
        all_messages.extend(_extract_messages(lf))

    # Categorize
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    errors = [m for m in all_messages if m["role"] == "error"]

    lines = [
        f"# Interaction Study — {project_path.name}",
        "",
    ]

    if log_files:
        lines.append(
            f"Analyzed {len(log_files)} conversation log(s), "
            f"{len(all_messages)} relevant messages."
        )
        lines.append("")
        lines.append(f"## User Messages ({len(user_msgs)})")
        for m in user_msgs:
            lines.append(f"- {m['text'][:200]}")

        lines.extend([
            "",
            f"## Errors and Issues ({len(errors)})",
        ])
        for m in errors:
            lines.append(f"- {m['text'][:200]}")
    else:
        lines.append("No interaction logs found.")

    # Similar projects from GitHub
    similar = _search_similar_projects(project_path)
    lines.extend(["", "## Similar Projects"])
    if similar:
        for proj in similar:
            stars = proj.get("stars", 0)
            desc = proj.get("description", "")
            desc_part = f" — {desc}" if desc else ""
            lines.append(f"- [{proj['name']}]({proj['url']}) ({stars} stars){desc_part}")
    else:
        lines.append("No similar projects found.")

    # Prior knowledge from Obsidian vault
    project_name = project_path.name
    notes = _read_obsidian_notes(project_name)
    lines.extend(["", "## Prior Knowledge (Obsidian)"])
    if notes:
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("No prior notes found.")

    return "\n".join(lines)
