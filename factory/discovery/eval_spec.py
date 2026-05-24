"""Auto-generate starter eval_spec items based on project profile."""

from __future__ import annotations

from pathlib import Path

import structlog

from factory.models import ProjectProfile

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
