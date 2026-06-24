"""Tests for factory.discovery.spec — SPEC.md resolution and generation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from factory.discovery.spec import (
    _fetch_github_issues,
    _read_readme_summary,
    _read_top_level_deps,
    generate_spec,
    resolve_spec,
)
from factory.models import ProjectProfile


def _make_profile(**overrides) -> ProjectProfile:
    defaults = {
        "name": "test-project",
        "language": "python",
        "project_type": "cli_tool",
        "has_tests": True,
        "has_linter": True,
        "has_type_checker": True,
        "has_ci": False,
        "test_command": "pytest -v",
        "lint_command": "ruff check .",
        "type_check_command": "mypy .",
        "package_manager": "uv",
    }
    defaults.update(overrides)
    return ProjectProfile(**defaults)


def test_resolve_spec_committed(tmp_path: Path):
    (tmp_path / "SPEC.md").write_text("# Spec")
    path, source = resolve_spec(tmp_path)
    assert source == "committed"
    assert path == tmp_path / "SPEC.md"


def test_resolve_spec_generated(tmp_path: Path):
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "SPEC.md").write_text("# Generated Spec")
    path, source = resolve_spec(tmp_path)
    assert source == "generated"
    assert path == factory_dir / "SPEC.md"


def test_resolve_spec_committed_takes_priority(tmp_path: Path):
    (tmp_path / "SPEC.md").write_text("# Committed")
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "SPEC.md").write_text("# Generated")
    path, source = resolve_spec(tmp_path)
    assert source == "committed"
    assert path == tmp_path / "SPEC.md"


def test_resolve_spec_absent(tmp_path: Path):
    path, source = resolve_spec(tmp_path)
    assert source == "absent"
    assert path is None


def test_generate_spec_format(tmp_path: Path):
    profile = _make_profile(name="my-app")
    output = generate_spec(tmp_path, profile)
    assert output.startswith("# my-app Specification")
    assert "## 1. Project Identity" in output
    assert "## 2. Goals" in output
    assert "## 3. Technical Stack" in output
    assert "## 4. Architecture" in output
    assert "## 5. Eval Dimensions" in output
    assert "## 6. Known Issues" in output
    assert "## 7. Backlog" in output
    assert "RFC 2119" in output


def test_generate_spec_captures_profile_data(tmp_path: Path):
    profile = _make_profile(
        language="python",
        framework="fastapi",
        test_command="pytest -v",
    )
    output = generate_spec(tmp_path, profile)
    assert "python" in output
    assert "fastapi" in output
    assert "pytest -v" in output


def test_generate_spec_reads_readme(tmp_path: Path):
    (tmp_path / "README.md").write_text("# My Project\n\nThis is a great tool for testing.\n")
    profile = _make_profile()
    output = generate_spec(tmp_path, profile)
    assert "This is a great tool for testing." in output


def test_generate_spec_no_readme(tmp_path: Path):
    profile = _make_profile()
    output = generate_spec(tmp_path, profile)
    assert "Goals not yet documented." in output


def test_generate_spec_detects_source_dirs(tmp_path: Path):
    pkg = tmp_path / "mypackage"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    profile = _make_profile()
    output = generate_spec(tmp_path, profile)
    assert "mypackage" in output


# ── _read_top_level_deps ──────────────────────────────────────────


def test_read_top_level_deps_python_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        "dependencies = [\n"
        '  "requests>=2.28",\n'
        '  "click==8.1",\n'
        '  "pydantic[email]>=2.0",\n'
        "]\n"
    )
    deps = _read_top_level_deps(tmp_path, "python")
    assert "requests" in deps
    assert "click" in deps
    assert "pydantic" in deps


def test_read_top_level_deps_typescript_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "demo",
        "dependencies": {"express": "^4.18", "lodash": "^4.17"},
    }))
    deps = _read_top_level_deps(tmp_path, "typescript")
    assert "express" in deps
    assert "lodash" in deps


def test_read_top_level_deps_no_matching_files(tmp_path: Path):
    assert _read_top_level_deps(tmp_path, "python") == []
    assert _read_top_level_deps(tmp_path, "typescript") == []
    assert _read_top_level_deps(tmp_path, "go") == []


# ── generate_spec with eval_profile.json ──────────────────────────


def test_generate_spec_with_eval_profile(tmp_path: Path):
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "eval_profile.json").write_text(json.dumps({
        "dimensions": [
            {"name": "test_coverage", "weight": 0.4, "source": "pytest"},
            {"name": "lint_score", "weight": 0.3, "source": "ruff"},
        ],
    }))
    profile = _make_profile()
    output = generate_spec(tmp_path, profile)
    assert "test_coverage" in output
    assert "weight: 0.4" in output
    assert "lint_score" in output
    assert "source: ruff" in output


# ── generate_spec with backlog ────────────────────────────────────


def test_generate_spec_with_backlog_items(tmp_path: Path):
    strategy_dir = tmp_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "backlog.md").write_text("- Add auth flow\n- Fix logging\n")
    profile = _make_profile()
    output = generate_spec(tmp_path, profile)
    assert "Add auth flow" in output
    assert "Fix logging" in output


def test_generate_spec_with_empty_backlog(tmp_path: Path):
    strategy_dir = tmp_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "backlog.md").write_text("")
    profile = _make_profile()
    output = generate_spec(tmp_path, profile)
    assert "No backlog items." in output


# ── _fetch_github_issues graceful failure ─────────────────────────


def test_fetch_github_issues_graceful_when_gh_unavailable(tmp_path: Path):
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = _fetch_github_issues(tmp_path)
    assert result == []


# ── generate_spec for TypeScript project ──────────────────────────


def test_generate_spec_typescript_source_dirs(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.ts").write_text("export default {}")
    profile = _make_profile(language="typescript")
    output = generate_spec(tmp_path, profile)
    assert "`src/`" in output


# ── generate_spec for Go project ─────────────────────────────────


def test_generate_spec_go_source_dirs(tmp_path: Path):
    cmd = tmp_path / "cmd"
    cmd.mkdir()
    (cmd / "main.go").write_text("package main")
    profile = _make_profile(language="go")
    output = generate_spec(tmp_path, profile)
    assert "`cmd/`" in output


# ── _read_readme_summary edge cases ──────────────────────────────


def test_read_readme_summary_rst(tmp_path: Path):
    (tmp_path / "README.rst").write_text(
        "My Project\n==========\n\nA tool for data analysis.\n"
    )
    result = _read_readme_summary(tmp_path)
    assert result == "My Project"


def test_read_readme_summary_heading_only(tmp_path: Path):
    (tmp_path / "README.md").write_text("# My Project\n")
    result = _read_readme_summary(tmp_path)
    assert result == "Goals not yet documented."


# ── study.py SPEC.md section ─────────────────────────────────────


def test_study_project_local_with_spec(tmp_path: Path):
    (tmp_path / "SPEC.md").write_text("# Test Spec\n\nSome spec content.\n")
    from factory.study import study_project_local
    output = study_project_local(tmp_path)
    assert "## SPEC.md" in output
    assert "committed" in output


def test_study_project_local_without_spec(tmp_path: Path):
    from factory.study import study_project_local
    output = study_project_local(tmp_path)
    assert "## SPEC.md" in output
    assert "No SPEC.md found" in output
