"""Project introspection — detect project type, language, and existing tooling."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from factory.models import DiscoveredEval, ProjectProfile

log = structlog.get_logger()


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
    from factory.eval.languages import detect_primary_language

    lang = detect_primary_language(project_path)
    if lang == "unknown" and (project_path / "Package.swift").exists():
        lang = "swift"
    log.debug("detect_language", language=lang)
    return lang


def _detect_project_type(project_path: Path, language: str) -> str:
    """Infer project type from README, directory structure, and config files."""
    log.debug("detect_project_type_start", language=language)
    readme_text = ""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme_path = project_path / name
        if readme_path.exists():
            readme_text = readme_path.read_text().lower()
            break

    # Check for bot indicators
    if any(kw in readme_text for kw in ("telegram", "discord", "slack bot", "chatbot")):
        log.debug("detect_project_type_result", project_type="bot")
        return "bot"

    # Check for web app indicators
    if (project_path / "next.config.js").exists() or (project_path / "next.config.ts").exists():
        log.debug("detect_project_type_result", project_type="web_app")
        return "web_app"
    if any(kw in readme_text for kw in ("fastapi", "django", "flask", "web app", "webapp")):
        log.debug("detect_project_type_result", project_type="web_app")
        return "web_app"

    # Check for CLI indicators
    if language == "python":
        toml_data = _read_toml_rough(project_path / "pyproject.toml")
        if "scripts" in str(toml_data):
            log.debug("detect_project_type_result", project_type="cli_tool")
            return "cli_tool"
    if any(kw in readme_text for kw in ("cli", "command-line", "command line")):
        log.debug("detect_project_type_result", project_type="cli_tool")
        return "cli_tool"

    # Check for library indicators
    if any(kw in readme_text for kw in ("library", "sdk", "package", "pip install", "npm install")):
        log.debug("detect_project_type_result", project_type="library")
        return "library"

    # Check for service indicators
    if any(kw in readme_text for kw in ("service", "api", "server", "daemon")):
        log.debug("detect_project_type_result", project_type="service")
        return "service"

    log.debug("detect_project_type_result", project_type="unknown")
    return "unknown"


def _detect_framework(project_path: Path, language: str) -> str | None:
    """Detect framework from dependencies."""
    log.debug("detect_framework_start", language=language)
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


def _detect_project_evals(project_path: Path) -> list[dict[str, str]]:
    """Discover existing evaluation/benchmark scripts in the project."""
    evals: list[dict[str, str]] = []

    for dir_name in ("eval", "benchmark", "benchmarks", "evaluation"):
        eval_dir = project_path / dir_name
        if not eval_dir.is_dir():
            continue
        for script in eval_dir.glob("*.py"):
            if script.name in ("score.py", "__init__.py"):
                continue
            evals.append({
                "name": script.stem,
                "command": f"python {dir_name}/{script.name}",
                "source": "discovered",
            })

    for name in ("evaluate.py", "benchmark.py", "bench.py"):
        if (project_path / name).exists():
            evals.append({
                "name": Path(name).stem,
                "command": f"python {name}",
                "source": "discovered",
            })

    makefile = project_path / "Makefile"
    if makefile.exists():
        try:
            text = makefile.read_text()
            for target in ("eval", "benchmark", "bench", "evaluate"):
                if f"\n{target}:" in text or text.startswith(f"{target}:"):
                    evals.append({
                        "name": target,
                        "command": f"make {target}",
                        "source": "discovered",
                    })
        except OSError:
            pass

    log.debug("detect_project_evals", count=len(evals))
    return evals


def introspect_project(project_path: Path) -> ProjectProfile:
    """Analyze a project directory and return its profile."""
    log.info("introspect_project_start", project=str(project_path))
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

    raw_evals = _detect_project_evals(project_path)
    discovered_evals = [DiscoveredEval(**e) for e in raw_evals]

    profile = ProjectProfile(
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
        discovered_evals=discovered_evals,
    )
    log.info(
        "introspect_project_complete",
        name=profile.name,
        language=language,
        project_type=project_type,
        framework=framework,
        has_tests=profile.has_tests,
        has_linter=profile.has_linter,
        has_ci=profile.has_ci,
    )
    return profile
