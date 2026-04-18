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

import os
import re
import subprocess
from pathlib import Path

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


# ── Tool detection ─────────────────────────────────────────────────


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


def _detect_python_project(project_path: Path) -> bool:
    return (project_path / "pyproject.toml").exists() or (project_path / "setup.py").exists()


def _detect_node_project(project_path: Path) -> bool:
    return (project_path / "package.json").exists()


def _detect_rust_project(project_path: Path) -> bool:
    return (project_path / "Cargo.toml").exists()


def _detect_go_project(project_path: Path) -> bool:
    return (project_path / "go.mod").exists()


def _run_cmd(
    cmd: list[str],
    cwd: Path,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return 1, "", str(exc)


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
    total_passed = 0
    total_failed = 0
    ran_any = False
    details_parts: list[str] = []

    for sp in sub_projects:
        if _detect_python_project(sp):
            # Try pytest
            rc, stdout, stderr = _run_cmd(["python", "-m", "pytest", "-v", "--tb=no", "-q"], sp)
            output = stdout + stderr
            p_match = re.search(r"(\d+)\s+passed", output)
            f_match = re.search(r"(\d+)\s+failed", output)
            p = int(p_match.group(1)) if p_match else 0
            f = int(f_match.group(1)) if f_match else 0
            if p + f > 0:
                ran_any = True
                total_passed += p
                total_failed += f
                details_parts.append(f"{sp.name}: {p} passed, {f} failed")

        if _detect_node_project(sp):
            # Try npm test
            rc, stdout, stderr = _run_cmd(["npm", "test", "--", "--passWithNoTests"], sp, timeout=180)
            output = stdout + stderr
            # Jest: "Tests: X passed, Y failed"
            p_match = re.search(r"(\d+)\s+passed", output)
            f_match = re.search(r"(\d+)\s+failed", output)
            p = int(p_match.group(1)) if p_match else 0
            f = int(f_match.group(1)) if f_match else 0
            if p + f > 0:
                ran_any = True
                total_passed += p
                total_failed += f
                details_parts.append(f"{sp.name}(js): {p} passed, {f} failed")

        if _detect_rust_project(sp):
            rc, stdout, stderr = _run_cmd(["cargo", "test"], sp)
            output = stdout + stderr
            p_match = re.search(r"(\d+)\s+passed", output)
            f_match = re.search(r"(\d+)\s+failed", output)
            p = int(p_match.group(1)) if p_match else 0
            f = int(f_match.group(1)) if f_match else 0
            if p + f > 0:
                ran_any = True
                total_passed += p
                total_failed += f
                details_parts.append(f"{sp.name}(rs): {p} passed, {f} failed")

        if _detect_go_project(sp):
            rc, stdout, stderr = _run_cmd(["go", "test", "./..."], sp)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                # go test: count "ok" lines
                ok_count = len(re.findall(r"^ok\s+", output, re.MULTILINE))
                total_passed += max(ok_count, 1)
                details_parts.append(f"{sp.name}(go): passed")
            elif "FAIL" in output:
                ran_any = True
                total_failed += 1
                details_parts.append(f"{sp.name}(go): failed")

    if not ran_any:
        return _neutral("tests", "no test suite detected")

    total = total_passed + total_failed
    score = total_passed / total if total > 0 else 0.0
    return {
        "name": "tests",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["tests"],
        "passed": total_failed == 0,
        "details": "; ".join(details_parts) or f"{total_passed} passed, {total_failed} failed",
    }


# ── Dimension 2: lint (weight 0.15) ───────────────────────────────


def eval_lint(project_path: Path) -> dict:
    """Run linters across detected sub-projects. Partial credit per error."""
    sub_projects = _find_sub_projects(project_path)
    total_errors = 0
    ran_any = False
    details_parts: list[str] = []

    for sp in sub_projects:
        if _detect_python_project(sp):
            rc, stdout, stderr = _run_cmd(["python", "-m", "ruff", "check", "."], sp)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}: clean")
            else:
                ran_any = True
                err_match = re.search(r"Found\s+(\d+)\s+error", output)
                count = int(err_match.group(1)) if err_match else 1
                total_errors += count
                details_parts.append(f"{sp.name}: {count} errors")

        if _detect_node_project(sp):
            rc, stdout, stderr = _run_cmd(["npx", "eslint", ".", "--format=compact"], sp, timeout=180)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}(js): clean")
            else:
                ran_any = True
                count = len(re.findall(r"Error -", output))
                total_errors += max(count, 1)
                details_parts.append(f"{sp.name}(js): {max(count, 1)} errors")

        if _detect_rust_project(sp):
            rc, stdout, stderr = _run_cmd(["cargo", "clippy", "--", "-D", "warnings"], sp)
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}(rs): clean")
            else:
                ran_any = True
                count = len(re.findall(r"^error", stderr, re.MULTILINE))
                total_errors += max(count, 1)
                details_parts.append(f"{sp.name}(rs): {max(count, 1)} errors")

    if not ran_any:
        return _neutral("lint", "no linter detected")

    score = max(0.0, 1.0 - total_errors * 0.1)
    return {
        "name": "lint",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["lint"],
        "passed": total_errors == 0,
        "details": "; ".join(details_parts),
    }


# ── Dimension 3: type_check (weight 0.10) ─────────────────────────


def eval_type_check(project_path: Path) -> dict:
    """Run type checkers across detected sub-projects. Partial credit per error."""
    sub_projects = _find_sub_projects(project_path)
    total_errors = 0
    ran_any = False
    details_parts: list[str] = []

    for sp in sub_projects:
        if _detect_python_project(sp):
            # Find the main source dir (first dir with __init__.py)
            src_dirs = []
            for child in sorted(sp.iterdir()):
                if child.is_dir() and (child / "__init__.py").exists():
                    src_dirs.append(child.name)
            target = src_dirs[0] if src_dirs else "."
            rc, stdout, stderr = _run_cmd(["python", "-m", "mypy", target], sp)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}: clean")
            else:
                ran_any = True
                err_match = re.search(r"Found\s+(\d+)\s+error", output)
                count = int(err_match.group(1)) if err_match else 1
                total_errors += count
                details_parts.append(f"{sp.name}: {count} errors")

        if _detect_node_project(sp):
            rc, stdout, stderr = _run_cmd(["npx", "tsc", "--noEmit"], sp, timeout=180)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}(ts): clean")
            else:
                ran_any = True
                count = len(re.findall(r"error TS\d+", output))
                total_errors += max(count, 1)
                details_parts.append(f"{sp.name}(ts): {max(count, 1)} errors")

    if not ran_any:
        return _neutral("type_check", "no type checker detected")

    score = max(0.0, 1.0 - total_errors * 0.05)
    return {
        "name": "type_check",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["type_check"],
        "passed": total_errors == 0,
        "details": "; ".join(details_parts),
    }


# ── Dimension 4: coverage (weight 0.25) ───────────────────────────


def eval_coverage(project_path: Path) -> dict:
    """Run test coverage across detected sub-projects."""
    sub_projects = _find_sub_projects(project_path)
    coverages: list[tuple[str, int]] = []
    ran_any = False

    for sp in sub_projects:
        if _detect_python_project(sp):
            # Find source dir for --cov target
            src_dirs = [
                c.name for c in sorted(sp.iterdir())
                if c.is_dir() and (c / "__init__.py").exists()
            ]
            cov_target = src_dirs[0] if src_dirs else "."
            rc, stdout, stderr = _run_cmd(
                ["python", "-m", "pytest", f"--cov={cov_target}", "--cov-report=term", "-q"],
                sp,
            )
            output = stdout + stderr
            total_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
            if total_match:
                ran_any = True
                pct = int(total_match.group(1))
                coverages.append((sp.name, pct))

    if not ran_any:
        return _neutral("coverage", "no coverage tool detected")

    avg_pct = sum(p for _, p in coverages) / len(coverages)
    score = avg_pct / 100.0
    details = ", ".join(f"{name}: {pct}%" for name, pct in coverages)
    return {
        "name": "coverage",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["coverage"],
        "passed": avg_pct >= 80,
        "details": f"Coverage: {details} (threshold: 80%)",
    }


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
            ("eval_threshold is positive", float(eval_threshold) > 0),
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


def compute_hygiene_results(project_path: Path) -> list[dict]:
    """Compute all 6 mandatory hygiene dimensions for a project."""
    return [
        eval_tests(project_path),
        eval_lint(project_path),
        eval_type_check(project_path),
        eval_coverage(project_path),
        eval_guard_patterns(project_path),
        eval_config_parser(project_path),
    ]
