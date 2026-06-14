"""Universal hygiene eval dimensions applied to every factory-managed project.

These 6 dimensions are mandatory and cannot be removed. They are computed by
the factory itself (not by per-project eval/score.py) and auto-detect the
project's tooling. Projects can ADD dimensions via eval/score.py but cannot
remove any of these.

Together with the 5 growth dimensions in growth.py, these form the 11
mandatory eval dimensions that define the factory's quality baseline.

All functions take a project_path and return an EvalResult-compatible dict.
If a tool is not detected for a dimension, score is 0.5 (neutral), not 0.
"""

from pathlib import Path

import structlog

from factory.eval.languages import _aggregate, detect_languages
from factory.eval.languages.base import EvalFragment

log = structlog.get_logger()

# Relative weights within the hygiene category (sum to 1.0).
# The runner normalizes these so that hygiene gets 50% of the composite.
HYGIENE_WEIGHTS = {
    "tests": 0.30,
    "lint": 0.15,
    "type_check": 0.10,
    "coverage": 0.25,
    "guard_patterns": 0.10,
    "config_parser": 0.10,
}


# ── Helpers ────────────────────────────────────────────────────────


def _find_sub_projects(project_path: Path) -> list[Path]:
    """Find project roots (dirs with pyproject.toml, package.json, Cargo.toml, go.mod).

    Checks the project root and immediate subdirectories. Returns the project
    root itself if it has project markers, plus any sub-project dirs.
    """
    markers = ["pyproject.toml", "package.json", "Cargo.toml", "go.mod"]
    skip = {".git", ".factory", "node_modules", ".venv", "venv", "__pycache__"}
    roots: list[Path] = []

    # Check top level
    if any((project_path / m).exists() for m in markers):
        roots.append(project_path)

    # Check immediate subdirs
    for child in sorted(project_path.iterdir()):
        if not child.is_dir() or child.name in skip or child.name.startswith("."):
            continue
        # Also follow symlinks
        resolved = child.resolve()
        if any((resolved / m).exists() for m in markers):
            roots.append(child)

    return roots or [project_path]


def _neutral(name: str, reason: str) -> dict:
    """Return a neutral score (0.5) when a tool isn't detected."""
    return {
        "name": name,
        "score": 0.5,
        "weight": HYGIENE_WEIGHTS[name],
        "passed": True,
        "details": f"Not detected: {reason}",
    }


# ── Dimension 1: tests (weight 0.30) ──────────────────────────────


def eval_tests(project_path: Path) -> dict:
    """Run test suites across all detected sub-projects. Parse pass/fail ratio."""
    sub_projects = _find_sub_projects(project_path)
    fragments = []
    for sp in sub_projects:
        for evaluator in detect_languages(sp):
            result = evaluator.run_tests(sp)
            if result is not None:
                fragments.append(result)
    if not fragments:
        return _neutral("tests", "no test suite detected")
    return _aggregate(fragments, "tests")


# ── Dimension 2: lint (weight 0.15) ───────────────────────────────


def eval_lint(project_path: Path) -> dict:
    """Run linters across detected sub-projects. Partial credit per error."""
    sub_projects = _find_sub_projects(project_path)
    fragments = []
    for sp in sub_projects:
        for evaluator in detect_languages(sp):
            result = evaluator.run_lint(sp)
            if result is not None:
                fragments.append(result)
    if not fragments:
        return _neutral("lint", "no linter detected")
    return _aggregate(fragments, "lint")


# ── Dimension 3: type_check (weight 0.10) ─────────────────────────


def eval_type_check(project_path: Path) -> dict:
    """Run type checkers across detected sub-projects. Partial credit per error."""
    sub_projects = _find_sub_projects(project_path)
    fragments = []
    for sp in sub_projects:
        for evaluator in detect_languages(sp):
            result = evaluator.run_type_check(sp)
            if result is not None:
                fragments.append(result)
    if not fragments:
        return _neutral("type_check", "no type checker detected")
    return _aggregate(fragments, "type_check")


# ── Dimension 4: coverage (weight 0.25) ───────────────────────────


def eval_coverage(project_path: Path) -> dict:
    """Run test coverage across detected sub-projects."""
    sub_projects = _find_sub_projects(project_path)
    fragments = []
    for sp in sub_projects:
        for evaluator in detect_languages(sp):
            result = evaluator.run_coverage(sp)
            if result is not None:
                fragments.append(result)
    if not fragments:
        return _neutral("coverage", "no coverage tool detected")
    return _aggregate(fragments, "coverage")


# ── Dimension 5: guard_patterns (weight 0.10) ─────────────────────


def eval_guard_patterns(project_path: Path) -> dict:
    """Test that the factory's guard glob matching works correctly on this project."""
    try:
        from factory.eval.guards import _glob_match
    except (ImportError, AttributeError) as exc:
        return {
            "name": "guard_patterns",
            "score": 0.0,
            "weight": HYGIENE_WEIGHTS["guard_patterns"],
            "passed": False,
            "details": f"Could not import _glob_match: {exc}",
        }

    # Read project scope from config if available
    scope_patterns: list[str] = []
    config_path = project_path / ".factory" / "config.json"
    if config_path.exists():
        import json
        try:
            data = json.loads(config_path.read_text())
            scope_patterns = data.get("scope", [])
        except (json.JSONDecodeError, KeyError):
            pass

    # Build test cases from the project's actual scope + universal cases
    test_cases: list[tuple[str, str, bool]] = [
        # Universal: .factory/ should never match user scope
        ("src/**/*.py", ".factory/config.json", False),
        ("src/**/*.py", "src/main.py", True),
        ("tests/**/*.py", "tests/test_main.py", True),
        ("tests/**/*.py", "src/main.py", False),
    ]

    # Add project-specific scope tests
    for pattern in scope_patterns[:4]:
        # The pattern itself should match something reasonable
        if "**" in pattern:
            parts = pattern.split("**")
            prefix = parts[0].rstrip("/")
            if prefix:
                test_cases.append((pattern, f"{prefix}/example.py", True))
                test_cases.append((pattern, "unrelated/file.txt", False))

    correct = 0
    details: list[str] = []
    for pattern, filepath, expected in test_cases:
        actual = _glob_match(filepath, pattern)
        if actual == expected:
            correct += 1
        else:
            details.append(f"FAIL: {pattern} vs {filepath} expected={expected} got={actual}")

    total = len(test_cases)
    score = correct / total if total > 0 else 1.0
    summary = f"{correct}/{total} pattern tests passed"
    if details:
        summary += "; " + "; ".join(details[:3])

    return {
        "name": "guard_patterns",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["guard_patterns"],
        "passed": correct == total,
        "details": summary,
    }


# ── Dimension 6: config_parser (weight 0.10) ──────────────────────


def _parse_factory_md(path: Path) -> dict[str, str | list[str] | float]:
    """Synchronously parse factory.md into a dict of config fields.

    Replicates the parsing logic from ExperimentStore.reparse_config()
    without requiring asyncio, so it can be called safely from sync code
    that may already be running inside an async event loop.
    """
    text = path.read_text()
    parsed: dict[str, str | list[str] | float] = {}
    current_section: str | None = None
    list_buffer: list[str] = []
    in_code_block = False

    section_map: dict[str, str] = {
        "command": "eval_command",
        "threshold": "eval_threshold",
        "modifiable": "scope",
        "read_only": "read_only",
    }

    def _flush_list() -> None:
        if current_section and list_buffer:
            parsed[current_section] = list(list_buffer)
            list_buffer.clear()

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            if stripped and current_section:
                parsed[current_section] = stripped
            continue

        if stripped.startswith("#"):
            _flush_list()
            heading = stripped.lstrip("#").strip().lower().replace(" ", "_")
            mapped = section_map.get(heading, heading)
            current_section = mapped
        elif stripped.startswith("- ") and current_section:
            list_buffer.append(stripped[2:].strip())
        elif stripped and current_section and not list_buffer:
            if current_section == "eval_threshold":
                parsed[current_section] = float(stripped)
            else:
                parsed[current_section] = stripped
    _flush_list()

    return parsed


def eval_config_parser(project_path: Path) -> dict:
    """Test that factory.md can be parsed and essential fields extracted."""
    factory_md = project_path / "factory.md"
    if not factory_md.exists():
        return _neutral("config_parser", "no factory.md found")

    try:
        parsed = _parse_factory_md(factory_md)

        goal = parsed.get("goal", "")
        scope = parsed.get("scope", [])
        eval_command = parsed.get("eval_command", "")
        eval_threshold = parsed.get("eval_threshold", 0.0)

        checks: list[tuple[str, bool]] = [
            ("goal is non-empty", bool(goal and len(str(goal)) > 0)),
            ("scope has entries", isinstance(scope, list) and len(scope) > 0),
            ("eval_command is non-empty", bool(eval_command)),
            ("eval_threshold is positive", float(str(eval_threshold)) > 0),
        ]

        correct = sum(1 for _, ok in checks if ok)
        total = len(checks)
        score = correct / total
        detail_parts = [f"{'OK' if ok else 'FAIL'}: {label}" for label, ok in checks]

        return {
            "name": "config_parser",
            "score": round(score, 4),
            "weight": HYGIENE_WEIGHTS["config_parser"],
            "passed": correct == total,
            "details": "; ".join(detail_parts),
        }
    except Exception as exc:
        return {
            "name": "config_parser",
            "score": 0.0,
            "weight": HYGIENE_WEIGHTS["config_parser"],
            "passed": False,
            "details": f"Error: {exc}",
        }


# ── Public API ─────────────────────────────────────────────────────


def _collect_test_and_coverage(project_path: Path) -> tuple[dict, dict]:
    """Run tests and coverage together via run_tests_with_coverage(), return both result dicts."""
    sub_projects = _find_sub_projects(project_path)
    test_fragments: list[EvalFragment] = []
    cov_fragments: list[EvalFragment] = []
    for sp in sub_projects:
        for evaluator in detect_languages(sp):
            test_frag, cov_frag = evaluator.run_tests_with_coverage(sp)
            if test_frag is not None:
                test_fragments.append(test_frag)
            if cov_frag is not None:
                cov_fragments.append(cov_frag)

    test_result = _aggregate(test_fragments, "tests") if test_fragments else _neutral("tests", "no test suite detected")
    cov_result = _aggregate(cov_fragments, "coverage") if cov_fragments else _neutral("coverage", "no coverage tool detected")
    return test_result, cov_result


def compute_hygiene_results(project_path: Path) -> list[dict]:
    """Compute all 6 mandatory hygiene dimensions for a project."""
    test_result, cov_result = _collect_test_and_coverage(project_path)
    return [
        test_result,
        eval_lint(project_path),
        eval_type_check(project_path),
        cov_result,
        eval_guard_patterns(project_path),
        eval_config_parser(project_path),
    ]
