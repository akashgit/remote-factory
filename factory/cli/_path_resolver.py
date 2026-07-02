"""Path resolution and project materialization for CEO commands."""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import structlog

from factory.cli._helpers import _is_github_url, _safe_is_dir, _safe_is_file

log = structlog.get_logger()


_FILLER_WORDS = frozenset({
    "a", "an", "the", "that", "which", "with", "for", "and", "or", "to", "using",
    "comprehensive", "simple", "basic", "advanced", "new", "custom", "full",
    "complete", "modern", "robust", "scalable", "lightweight", "minimal",
    "fully", "featured", "production", "ready",
})


_VERB_RE = re.compile(
    r"^(build|create|make|implement|develop|design|write|add|set\s*up|construct|craft)\b\s*"
)


def _get_projects_dir() -> Path:
    from factory.user_config import resolve

    raw = resolve("projects_dir", env_var="FACTORY_PROJECTS_DIR", default=str(Path.home() / "factory-projects"))
    return Path(raw).expanduser() if raw else Path.home() / "factory-projects"


_ORIGINAL_GET_PROJECTS_DIR = _get_projects_dir


def _resolve_projects_dir() -> Path:
    """Resolve _get_projects_dir with support for test monkeypatching on factory.cli."""
    import factory.cli as _cli
    cli_fn = getattr(_cli, "_get_projects_dir", _ORIGINAL_GET_PROJECTS_DIR)
    if cli_fn is not _ORIGINAL_GET_PROJECTS_DIR:
        return cli_fn()
    return _get_projects_dir()


def _resolve_input(raw: str, dir_name: str | None = None) -> tuple[Path, str | None]:
    """Resolve any user input to (project_path, optional_context).

    Handles four input types in priority order:
    1. Existing directory -> use directly
    2. Existing file -> read as spec, create repo
    3. GitHub URL -> clone
    4. Raw prompt -> create repo, use prompt as spec
    """
    # 1. Existing directory
    expanded = Path(raw).expanduser()
    if _safe_is_dir(expanded):
        return expanded.resolve(), None

    # 2. Existing file (e.g. path to an idea/spec .md file)
    if _safe_is_file(expanded):
        idea_content = expanded.read_text()
        slug = _slugify(dir_name) if dir_name else _slugify(expanded.stem.split("—")[0].strip())
        project_path = _dedupe_project_path(_resolve_projects_dir() / slug, idea_content)
        print(f"Idea file: {expanded.name}")
        print(f"Project directory: {project_path}")
        return project_path, idea_content

    # 3. GitHub URL
    if _is_github_url(raw):
        tmp_dir = tempfile.mkdtemp(prefix="factory-")
        subprocess.run(["git", "clone", raw, tmp_dir], check=True)
        print(f"Cloned {raw} → {tmp_dir}")
        return Path(tmp_dir).resolve(), None

    # 4. Raw prompt
    slug = _slugify(dir_name) if dir_name else _extract_project_name(raw)
    project_path = _dedupe_project_path(_resolve_projects_dir() / slug, raw)
    print(f"New project from prompt: {project_path}")
    return project_path, raw


def _extract_project_name(description: str) -> str:
    """Extract a concise project name from a verbose description.

    Strips leading imperative verbs and filler words, then takes
    up to 4 whitespace-delimited tokens (hyphenated compounds like
    ``real-time`` count as one token).
    """
    text = description.lower().strip()
    text = _VERB_RE.sub("", text)
    words = [w for w in re.split(r"\s+", text) if w and w not in _FILLER_WORDS]
    name = "-".join(words[:4])
    return _slugify(name) if name else _slugify(description[:50])


def _extract_short_description(text: str, max_words: int = 6) -> str:
    """Extract a short lowercase phrase from idea text for session naming.

    Like ``_extract_project_name`` but keeps spaces and allows more words.
    """
    lowered = text.lower().strip()
    lowered = _VERB_RE.sub("", lowered)
    words = [w for w in re.split(r"\s+", lowered) if w and w not in _FILLER_WORDS]
    return " ".join(words[:max_words])


def _dedupe_project_path(project_path: Path, new_spec: str) -> Path:
    """Append a numeric suffix if the directory already holds a different project."""
    spec_path = project_path / ".factory" / "strategy" / "current.md"
    if not spec_path.exists():
        return project_path
    if new_spec.strip() in spec_path.read_text():
        return project_path
    base = project_path
    counter = 2
    while True:
        candidate = base.parent / f"{base.name}-{counter}"
        cand_spec = candidate / ".factory" / "strategy" / "current.md"
        if not cand_spec.exists():
            return candidate
        if new_spec.strip() in cand_spec.read_text():
            return candidate
        counter += 1


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:50].rstrip("-") or "factory-project"


def _ensure_repo(project_path: Path) -> None:
    """Create directory + git init (with initial commit) if needed."""
    project_path.mkdir(parents=True, exist_ok=True)
    if not (project_path / ".git").is_dir():
        subprocess.run(["git", "init"], cwd=project_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Factory", "-c", "user.email=factory@localhost",
             "commit", "--allow-empty", "-m", "Initial commit"],
            cwd=project_path, capture_output=True, check=True,
        )


def _persist_spec(project_path: Path, spec: str) -> None:
    """Write the project spec to .factory/strategy/current.md so all agents can read it."""
    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    spec_path = strategy_dir / "current.md"
    if not spec_path.exists():
        spec_path.write_text(f"## Project Specification\n\n{spec}\n")


def _materialize_project(project_path: Path, spec: str | None = None) -> None:
    """Create git repo and optionally persist spec. Single choke point for deferred creation."""
    _ensure_repo(project_path)
    if spec:
        _persist_spec(project_path, spec)


def _is_scaffold_only(project_path: Path) -> bool:
    """Return True if project_path is empty scaffolding that can be safely removed.

    A project is considered scaffold-only when it has exactly 1 git commit
    (the initial empty commit from _ensure_repo) and the only non-.git content
    is .factory/strategy/current.md.
    """
    if not project_path.is_dir():
        return False
    git_dir = project_path / ".git"
    if not git_dir.is_dir():
        return False
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=project_path, capture_output=True, text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != "1":
        return False
    non_git = [
        p for p in project_path.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]
    allowed = {project_path / ".factory" / "strategy" / "current.md"}
    return all(p in allowed for p in non_git)


def _read_prompt_file(project_path: Path, prompt_file: str) -> str:
    """Read a prompt file (absolute or relative to project) and persist it as the build spec."""
    import sys

    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = project_path / prompt_path
    if not prompt_path.exists():
        print(f"Error: prompt file not found: {prompt_path}", file=sys.stderr)
        sys.exit(1)
    content = prompt_path.read_text()
    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    spec_path = strategy_dir / "current.md"
    spec_path.write_text(f"## Project Specification\n\n{content}\n")
    print(f"  Prompt: {prompt_path.name} → .factory/strategy/current.md", file=sys.stderr)
    return content


def _resolve_focus_issue(
    focus: str, project_path: Path,
) -> tuple[str, str, int, str] | None:
    """If *focus* looks like an issue ref, fetch it and return (title, context, number, url).

    Returns ``None`` when *focus* is a plain backlog-item name.
    Callers must check ``--no-github`` *before* calling this function.
    """
    from factory.issue import is_issue_ref

    if not is_issue_ref(focus):
        return None

    from factory.issue import fetch_issue, format_issue_as_spec

    issue_spec = fetch_issue(focus, project_path)
    context = format_issue_as_spec(issue_spec)

    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "current.md").write_text(
        f"## Project Specification\n\n{context}\n"
    )
    print(
        f"  Issue: #{issue_spec.number} → .factory/strategy/current.md",
        file=sys.stderr,
    )
    return issue_spec.title, context, issue_spec.number, issue_spec.url


def _derive_session_name(
    *,
    focus: str | None = None,
    design_idea: str | None = None,
    research_ideation: str | None = None,
    raw_path: str | None = None,
    project_path: Path,
    mode: str = "improve",
) -> str:
    """Derive a human-readable session name from the best available context."""
    prefix = "factory: "
    max_len = 60

    if focus:
        label = focus.lower()[:max_len - len(prefix)]
        return f"{prefix}{label}"

    idea = design_idea or research_ideation
    if idea:
        desc = _extract_short_description(idea)
        if desc:
            return f"{prefix}{desc}"[:max_len]

    if raw_path and not _safe_is_dir(Path(raw_path).expanduser()) \
            and not _safe_is_file(Path(raw_path).expanduser()) \
            and not _is_github_url(raw_path):
        desc = _extract_short_description(raw_path)
        if desc:
            return f"{prefix}{desc}"[:max_len]

    proj_name = project_path.resolve().name
    return f"{prefix}{mode} {proj_name}"[:max_len]


def _has_research_target(project_path: Path) -> bool:
    """Check if project already has research_target configured."""
    import json

    from factory.cli._helpers import _run

    try:
        from factory.store import ExperimentStore
        config = _run(ExperimentStore(project_path).read_config())
        return config.research_target is not None
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError):
        return False
