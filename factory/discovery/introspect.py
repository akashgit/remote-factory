"""Project introspection — detect project type, language, and existing tooling."""

from __future__ import annotations

import json
from pathlib import Path

from factory.models import ProjectProfile


def _read_json(path: Path) -> dict:
    """Read a JSON file, return empty dict on failure."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _read_toml_rough(path: Path) -> dict[str, str]:
    """Rough TOML key=value parser for pyproject.toml. Not a full parser."""
    result: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("[") and not stripped.startswith("#"):
                key, _, val = stripped.partition("=")
                result[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return result


def _detect_language(project_path: Path) -> str:
    """Detect primary language from project files."""
    if (project_path / "pyproject.toml").exists() or (project_path / "setup.py").exists():
        return "python"
    if (project_path / "package.json").exists():
        return "typescript"
    if (project_path / "Cargo.toml").exists():
        return "rust"
    if (project_path / "go.mod").exists():
        return "go"
    if (project_path / "Package.swift").exists():
        return "swift"
    return "unknown"


def _detect_project_type(project_path: Path, language: str) -> str:
    """Infer project type from README, directory structure, and config files."""
    readme_text = ""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme_path = project_path / name
        if readme_path.exists():
            readme_text = readme_path.read_text().lower()
            break

    # Check for bot indicators
    if any(kw in readme_text for kw in ("telegram", "discord", "slack bot", "chatbot")):
        return "bot"

    # Check for web app indicators
    if (project_path / "next.config.js").exists() or (project_path / "next.config.ts").exists():
        return "web_app"
    if any(kw in readme_text for kw in ("fastapi", "django", "flask", "web app", "webapp")):
        return "web_app"

    # Check for CLI indicators
    if language == "python":
        toml_data = _read_toml_rough(project_path / "pyproject.toml")
        if "scripts" in str(toml_data):
            return "cli_tool"
    if any(kw in readme_text for kw in ("cli", "command-line", "command line")):
        return "cli_tool"

    # Check for library indicators
    if any(kw in readme_text for kw in ("library", "sdk", "package", "pip install", "npm install")):
        return "library"

    # Check for service indicators
    if any(kw in readme_text for kw in ("service", "api", "server", "daemon")):
        return "service"

    return "unknown"


def _detect_framework(project_path: Path, language: str) -> str | None:
    """Detect framework from dependencies."""
    if language == "python":
        toml_text = ""
        if (project_path / "pyproject.toml").exists():
            toml_text = (project_path / "pyproject.toml").read_text().lower()
        if "fastapi" in toml_text:
            return "fastapi"
        if "django" in toml_text:
            return "django"
        if "flask" in toml_text:
            return "flask"
        if "python-telegram-bot" in toml_text:
            return "python-telegram-bot"
    elif language == "typescript":
        pkg = _read_json(project_path / "package.json")
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "next" in deps:
            return "next.js"
        if "express" in deps:
            return "express"
    return None


def _detect_test_command(project_path: Path, language: str) -> str | None:
    """Find the test command for the project."""
    if language == "python":
        if (project_path / "pyproject.toml").exists():
            toml_text = (project_path / "pyproject.toml").read_text()
            if "pytest" in toml_text:
                pm = "uv run" if (project_path / "uv.lock").exists() else "python -m"
                return f"{pm} pytest -v"
        # Check for tests directory
        if (project_path / "tests").exists():
            pm = "uv run" if (project_path / "uv.lock").exists() else "python -m"
            return f"{pm} pytest -v"
    elif language == "typescript":
        pkg = _read_json(project_path / "package.json")
        if "test" in pkg.get("scripts", {}):
            return "npm test"
    elif language == "rust":
        return "cargo test"
    elif language == "go":
        return "go test ./..."
    return None


def _detect_lint_command(project_path: Path, language: str) -> str | None:
    """Find the lint command for the project."""
    if language == "python":
        pm = "uv run" if (project_path / "uv.lock").exists() else "python -m"
        toml_text = ""
        if (project_path / "pyproject.toml").exists():
            toml_text = (project_path / "pyproject.toml").read_text().lower()
        if "ruff" in toml_text:
            return f"{pm} ruff check ."
        # Check if ruff is available even if not in pyproject
        return f"{pm} ruff check ."
    elif language == "typescript":
        pkg = _read_json(project_path / "package.json")
        if "lint" in pkg.get("scripts", {}):
            return "npm run lint"
    elif language == "rust":
        return "cargo clippy"
    elif language == "go":
        return "golangci-lint run"
    return None


def _detect_type_check_command(project_path: Path, language: str) -> str | None:
    """Find the type check command if applicable."""
    if language == "python":
        pm = "uv run" if (project_path / "uv.lock").exists() else "python -m"
        # Find the main package directory
        src_dirs = [
            d.name for d in project_path.iterdir()
            if d.is_dir()
            and (d / "__init__.py").exists()
            and d.name not in ("tests", "test", ".venv", "venv")
        ]
        target = src_dirs[0] if src_dirs else "."
        return f"{pm} mypy {target}/"
    elif language == "typescript":
        pkg = _read_json(project_path / "package.json")
        if "typescript" in pkg.get("devDependencies", {}):
            return "npx tsc --noEmit"
    return None


def _has_ci(project_path: Path) -> bool:
    """Check if CI configuration exists."""
    ci_paths = [
        project_path / ".github" / "workflows",
        project_path / ".gitlab-ci.yml",
        project_path / ".circleci" / "config.yml",
        project_path / "Jenkinsfile",
    ]
    return any(p.exists() for p in ci_paths)


def introspect_project(project_path: Path) -> ProjectProfile:
    """Analyze a project directory and return its profile."""
    language = _detect_language(project_path)
    project_type = _detect_project_type(project_path, language)
    framework = _detect_framework(project_path, language)
    test_cmd = _detect_test_command(project_path, language)
    lint_cmd = _detect_lint_command(project_path, language)
    type_check_cmd = _detect_type_check_command(project_path, language)

    # Detect package manager
    package_manager: str | None = None
    if language == "python":
        if (project_path / "uv.lock").exists():
            package_manager = "uv"
        elif (project_path / "poetry.lock").exists():
            package_manager = "poetry"
        elif (project_path / "Pipfile.lock").exists():
            package_manager = "pipenv"
        else:
            package_manager = "pip"
    elif language == "typescript":
        if (project_path / "pnpm-lock.yaml").exists():
            package_manager = "pnpm"
        elif (project_path / "yarn.lock").exists():
            package_manager = "yarn"
        elif (project_path / "bun.lockb").exists():
            package_manager = "bun"
        else:
            package_manager = "npm"

    return ProjectProfile(
        name=project_path.name,
        language=language,
        framework=framework,
        project_type=project_type,
        has_tests=test_cmd is not None,
        has_linter=lint_cmd is not None,
        has_type_checker=type_check_cmd is not None,
        has_ci=_has_ci(project_path),
        test_command=test_cmd,
        lint_command=lint_cmd,
        type_check_command=type_check_cmd,
        package_manager=package_manager,
    )
