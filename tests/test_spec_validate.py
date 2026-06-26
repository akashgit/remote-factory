"""Tests for factory.spec.validate — path checks, import cross-ref, orphan/hub, coupling."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.spec.validate import (
    ValidationResult,
    validate_spec,
)


def _write_spec(project: Path, spec_content: str) -> Path:
    """Write a GRAPH-SPEC.md into the project's .factory/ directory."""
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    spec_path = factory_dir / "GRAPH-SPEC.md"
    spec_path.write_text(spec_content)
    return spec_path


def _make_python_project(tmp_path: Path) -> Path:
    """Create a fixture Python project with known dependency structure."""
    project = tmp_path / "myproject"
    project.mkdir()

    # models module
    models = project / "myapp" / "models.py"
    models.parent.mkdir(parents=True)
    (project / "myapp" / "__init__.py").write_text("")
    models.write_text("class User:\n    pass\n\nclass Config:\n    pass\n")

    # store module
    store = project / "myapp" / "store.py"
    store.write_text("from myapp import models\n\nclass Store:\n    pass\n")

    # api module
    api = project / "myapp" / "api.py"
    api.write_text("from myapp import models\nfrom myapp import store\n")

    # utils module (orphan — nobody imports it)
    utils = project / "myapp" / "utils.py"
    utils.write_text("def slugify(s: str) -> str:\n    return s.lower()\n")

    return project


BASIC_SPEC = """\
# Repo Spec

## Modules

### models
- **Path:** myapp/models.py
- **Role:** Data models
- **Exports:** User, Config
- **Depends on:** none

### store
- **Path:** myapp/store.py
- **Role:** Data persistence
- **Exports:** Store
- **Depends on:** models

### api
- **Path:** myapp/api.py
- **Role:** API layer
- **Exports:** app
- **Depends on:** models, store

### utils
- **Path:** myapp/utils.py
- **Role:** Utility functions
- **Exports:** slugify
- **Depends on:** none

## Dependency Edges

| Source | Target | Import Type | Coupling |
|--------|--------|-------------|----------|
| store | models | direct | strong |
| api | models | direct | strong |
| api | store | direct | weak |
"""


class TestPathChecks:
    async def test_valid_paths_no_errors(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        path_errors = [e for e in result.errors if "does not exist" in e]
        assert path_errors == []

    async def test_missing_path_produces_error(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        spec_with_bad_path = BASIC_SPEC.replace("myapp/utils.py", "myapp/nonexistent.py")
        _write_spec(project, spec_with_bad_path)
        result = await validate_spec(project)
        path_errors = [e for e in result.errors if "does not exist" in e]
        assert len(path_errors) == 1
        assert "utils" in path_errors[0]

    async def test_missing_path_fails_validation(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        spec_with_bad_path = BASIC_SPEC.replace("myapp/utils.py", "myapp/nonexistent.py")
        _write_spec(project, spec_with_bad_path)
        result = await validate_spec(project)
        assert not result.passed


class TestImportCrossReference:
    async def test_valid_imports_no_warnings(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        import_warns = [w for w in result.warnings if "no import of" in w]
        assert import_warns == []

    async def test_phantom_dependency_produces_warning(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        spec_with_phantom = BASIC_SPEC.replace(
            "### utils\n- **Path:** myapp/utils.py\n- **Role:** Utility functions\n"
            "- **Exports:** slugify\n- **Depends on:** none",
            "### utils\n- **Path:** myapp/utils.py\n- **Role:** Utility functions\n"
            "- **Exports:** slugify\n- **Depends on:** models",
        )
        _write_spec(project, spec_with_phantom)
        result = await validate_spec(project)
        import_warns = [w for w in result.warnings if "declares dependency" in w]
        assert len(import_warns) >= 1
        assert "utils" in import_warns[0]
        assert "models" in import_warns[0]


class TestOrphanDetection:
    async def test_orphan_module_flagged(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        orphan_warns = [w for w in result.warnings if "Orphan module" in w]
        orphan_names = [w for w in orphan_warns if "utils" in w]
        assert len(orphan_names) >= 1

    async def test_consumed_module_not_orphan(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        orphan_warns = [w for w in result.warnings if "Orphan module" in w]
        orphan_names = [w for w in orphan_warns if "models" in w]
        assert orphan_names == []


class TestHubDetection:
    async def test_hub_module_flagged(self, tmp_path: Path) -> None:
        """A module with >=5 dependents gets flagged as a hub."""
        project = tmp_path / "hubproject"
        project.mkdir()

        core = project / "core"
        core.mkdir()
        (core / "__init__.py").write_text("")
        (core / "base.py").write_text("class Base: pass")

        modules_spec = (
            "### core\n- **Path:** core/\n- **Role:** Core module\n- **Depends on:** none\n\n"
        )
        for i in range(6):
            mod_dir = project / f"mod{i}"
            mod_dir.mkdir()
            (mod_dir / "__init__.py").write_text("")
            (mod_dir / "main.py").write_text("from core import base")
            modules_spec += (
                f"### mod{i}\n- **Path:** mod{i}/\n- **Role:** Module {i}\n"
                f"- **Depends on:** core\n\n"
            )

        spec = f"# Repo Spec\n\n## Modules\n\n{modules_spec}"
        _write_spec(project, spec)
        result = await validate_spec(project)
        hub_warns = [w for w in result.warnings if "Hub module" in w]
        assert len(hub_warns) >= 1
        assert "core" in hub_warns[0]

    async def test_low_dependent_count_not_hub(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        hub_warns = [w for w in result.warnings if "Hub module" in w]
        assert hub_warns == []


class TestCouplingMetrics:
    async def test_afferent_coupling(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        assert "models" in result.metrics
        assert result.metrics["models"].afferent == 2

    async def test_efferent_coupling(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        assert "api" in result.metrics
        assert result.metrics["api"].efferent == 2

    async def test_instability_stable_module(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        models_m = result.metrics["models"]
        assert models_m.instability == 0.0

    async def test_instability_unstable_module(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        api_m = result.metrics["api"]
        assert api_m.instability > 0.5

    async def test_instability_range(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        for name, m in result.metrics.items():
            assert 0.0 <= m.instability <= 1.0, f"{name} instability out of range"

    async def test_zero_coupling_module(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        utils_m = result.metrics["utils"]
        assert utils_m.afferent == 0
        assert utils_m.efferent == 0
        assert utils_m.instability == 0.0


class TestValidationReport:
    async def test_report_written(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        await validate_spec(project)
        report_path = project / ".factory" / "spec_validation.md"
        assert report_path.is_file()

    async def test_report_contains_summary(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        await validate_spec(project)
        report = (project / ".factory" / "spec_validation.md").read_text()
        assert "## Summary" in report
        assert "PASS" in report

    async def test_report_contains_coupling_table(self, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        await validate_spec(project)
        report = (project / ".factory" / "spec_validation.md").read_text()
        assert "## Coupling Metrics" in report
        assert "Ca (afferent)" in report


class TestValidationResult:
    def test_passed_with_no_errors(self) -> None:
        result = ValidationResult(warnings=["some warning"])
        assert result.passed

    def test_failed_with_errors(self) -> None:
        result = ValidationResult(errors=["path missing"])
        assert not result.passed


class TestMissingSpec:
    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await validate_spec(tmp_path)
