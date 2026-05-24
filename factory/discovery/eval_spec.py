"""Auto-generate starter eval_spec items based on project profile."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

import structlog

from factory.models import ProjectEvalDimension, ProjectProfile

log = structlog.get_logger()

_SPEC_BY_TYPE: dict[str, list[str]] = {
    "web_app": [
        "Start the dev server and confirm the landing page loads without errors",
        "Verify the main navigation links resolve to valid pages",
    ],
    "service": [
        "Start the service and confirm the health endpoint returns 200",
        "Send a sample request to the primary API endpoint and verify the response schema",
    ],
    "cli_tool": [
        "Run the CLI with --help and verify it prints usage information",
        "Run the CLI with a sample input and verify it produces expected output",
    ],
    "library": [
        "Import the package in a Python shell and verify no import errors",
        "Run the primary example from the README or docs and verify it completes",
    ],
    "bot": [
        "Start the bot process and verify it initializes without errors",
        "Verify the bot responds to a basic health-check or /start command",
    ],
}

_FRAMEWORK_SPECS: dict[str, list[str]] = {
    "fastapi": [
        "Verify /docs (Swagger UI) loads and lists all endpoints",
    ],
    "next.js": [
        "Run the Next.js dev server and verify the home page renders",
    ],
    "django": [
        "Run python manage.py check and verify no issues reported",
    ],
}


def generate_eval_spec(profile: ProjectProfile, project_path: Path) -> list[str]:
    """Produce starter eval_spec items based on project type and framework."""
    items: list[str] = []

    type_specs = _SPEC_BY_TYPE.get(profile.project_type, [])
    items.extend(type_specs)

    if profile.framework:
        fw_specs = _FRAMEWORK_SPECS.get(profile.framework, [])
        items.extend(fw_specs)

    compose_files = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
    has_docker = (project_path / "Dockerfile").exists() or any(
        (project_path / f).exists() for f in compose_files
    )
    if has_docker:
        items.append("Build and start Docker containers and verify services are healthy")

    if not items:
        items.append("Build and run the project's primary entry point without errors")

    log.debug(
        "generate_eval_spec",
        project_type=profile.project_type,
        framework=profile.framework,
        item_count=len(items),
    )
    return items


# ── Eval spec classification & promotion ─────────────────────────

_EXECUTABLE_VERBS = frozenset({
    "run", "start", "build", "execute", "deploy", "import",
    "install", "launch", "open", "curl", "send", "hit",
})

_COMMAND_PATTERNS = re.compile(
    r"(?:`[^`]+`|--\w+|\b(?:python|node|npm|cargo|go|docker|curl|make|pytest)\b)"
)


def classify_eval_spec_item(item: str) -> Literal["executable", "judgmental"]:
    """Classify an eval_spec item as executable (automatable) or judgmental (manual)."""
    lower = item.lower().strip()
    if not lower:
        return "judgmental"

    first_word = lower.split()[0]
    if first_word in _EXECUTABLE_VERBS:
        return "executable"

    if _COMMAND_PATTERNS.search(item):
        return "executable"

    return "judgmental"


def _find_entry_point(project_path: Path) -> str | None:
    """Find the main CLI entry point from pyproject.toml [project.scripts]."""
    pyproject = project_path / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
        scripts = data.get("project", {}).get("scripts", {})
        if scripts:
            return next(iter(scripts))
    except Exception:
        pass
    return None


def _find_package_name(project_path: Path) -> str | None:
    """Find the main Python package name by looking for dirs with __init__.py."""
    skip = {"tests", "test", "docs", "scripts", "examples", "eval"}
    for child in sorted(project_path.iterdir()):
        if child.is_dir() and (child / "__init__.py").exists() and child.name not in skip:
            return child.name
    return None


def _slugify(item: str) -> str:
    """Convert an eval_spec item to a short slug for dimension naming."""
    words = re.sub(r"[^a-z0-9\s]", "", item.lower()).split()[:4]
    return "_".join(words) if words else "spec_check"


def _generate_command(item: str, project_path: Path) -> str | None:
    """Attempt to generate a shell command from an eval_spec item."""
    lower = item.lower()

    if "--help" in lower or "--version" in lower:
        entry = _find_entry_point(project_path)
        if entry:
            flag = "--help" if "--help" in lower else "--version"
            return f"{entry} {flag}"
        return None

    if "import" in lower and ("package" in lower or "module" in lower or "python" in lower):
        pkg = _find_package_name(project_path)
        if pkg:
            return f'python -c "import {pkg}"'
        return None

    if "docker" in lower:
        compose_files = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
        if any((project_path / f).exists() for f in compose_files):
            return "docker compose build"
        if (project_path / "Dockerfile").exists():
            return "docker build -t spec-check ."
        return None

    if "manage.py" in lower:
        if (project_path / "manage.py").exists():
            return "python manage.py check"
        return None

    # Backticked command extraction
    backtick = re.search(r"`([^`]+)`", item)
    if backtick:
        cmd = backtick.group(1).strip()
        if cmd and not cmd.startswith("#"):
            return cmd

    return None


def generate_project_eval_from_spec(
    eval_spec: list[str],
    project_path: Path,
) -> list[ProjectEvalDimension]:
    """Generate ProjectEvalDimension entries for executable eval_spec items."""
    dims: list[ProjectEvalDimension] = []

    used_slugs: dict[str, int] = {}

    for item in eval_spec:
        if classify_eval_spec_item(item) != "executable":
            continue

        command = _generate_command(item, project_path)
        if not command:
            continue

        base = f"spec_{_slugify(item)}"
        if base in used_slugs:
            used_slugs[base] += 1
            name = f"{base}_{used_slugs[base]}"
        else:
            used_slugs[base] = 1
            name = base
        dims.append(ProjectEvalDimension(
            name=name,
            command=command,
            parse="exit_code",
            weight=1.0,
            description=item,
        ))

    log.debug(
        "generate_project_eval_from_spec",
        total_items=len(eval_spec),
        promoted=len(dims),
    )
    return dims
