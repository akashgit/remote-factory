"""Tests for factory.spec.impact — module impact subgraph extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.spec.impact import get_impact

SAMPLE_SPEC = """\
# Repo Spec

## Modules

### cli
- **Path:** factory/cli.py
- **Role:** CLI entry point for the factory
- **Layer:** cli
- **Classification:** hub
- **Exports:** main, build_parser
- **Depends on:** store, eval, spec
- **Contracts owned:** none

### store
- **Path:** factory/store.py
- **Role:** Experiment store managing .factory/ directory
- **Layer:** data
- **Classification:** hub
- **Exports:** ExperimentStore
- **Depends on:** models
- **Contracts owned:** ExperimentRecord

### models
- **Path:** factory/models.py
- **Role:** All Pydantic v2 strict models
- **Layer:** models
- **Classification:** hub
- **Exports:** ProjectState, FactoryConfig, EvalProfile
- **Depends on:** none
- **Contracts owned:** ProjectState, FactoryConfig, EvalProfile

### spec
- **Path:** factory/spec/
- **Role:** Repo spec generation and validation
- **Layer:** core
- **Classification:** leaf
- **Exports:** generate_spec, validate_spec
- **Depends on:** models, store
- **Contracts owned:** RepoSpec

### utils
- **Path:** factory/utils.py
- **Role:** Shared utility functions
- **Layer:** utils
- **Classification:** leaf
- **Exports:** slugify
- **Depends on:** none
- **Contracts owned:** none

## Dependency Edges

| Source | Target | Import Type | Coupling |
|--------|--------|-------------|----------|
| cli | store | direct | strong |
| cli | eval | direct | strong |
| cli | spec | direct | weak |
| store | models | direct | strong |
| spec | models | direct | strong |
| spec | store | direct | weak |

## Shared Contracts

| Contract | Defined In | Used By | Change Risk |
|----------|-----------|---------|-------------|
| ProjectState | models | cli, store, eval | high — 3 consumers |
| FactoryConfig | models | cli, store | medium — 2 consumers |
| ExperimentRecord | store | cli | low — 1 consumer |
| RepoSpec | spec | cli | low — 1 consumer |

## Entry Points

| Entry Point | Module | Type |
|-------------|--------|------|
| factory cli | cli | CLI |
| factory spec generate | spec | CLI |

## Change Impact

| Module | Classification | Dependents | Impact if Changed |
|--------|---------------|------------|-------------------|
| models | hub | cli, store, spec | HIGH — update all consumers |
| store | hub | cli, spec | MEDIUM — 2 direct consumers |
| cli | hub | — | LOW — top-level entry point |
| spec | leaf | — | LOW — no dependents |
| utils | leaf | — | LOW — no dependents |
"""


@pytest.fixture
def project_with_spec(tmp_path: Path) -> Path:
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "repo_spec.md").write_text(SAMPLE_SPEC)
    return tmp_path


class TestGetImpact:
    def test_hub_module_with_many_dependents(self, project_with_spec: Path) -> None:
        result = get_impact("models", project_with_spec)
        assert "## Impact: models" in result
        assert "**Classification:** hub" in result
        assert "### Dependencies (imports)" in result
        assert "- None" in result.split("### Dependencies (imports)")[1].split("###")[0]
        assert "### Dependents (imported by)" in result
        assert "cli" in result.split("### Dependents (imported by)")[1]
        assert "store" in result.split("### Dependents (imported by)")[1]
        assert "spec" in result.split("### Dependents (imported by)")[1]

    def test_leaf_module_no_dependents(self, project_with_spec: Path) -> None:
        result = get_impact("utils", project_with_spec)
        assert "## Impact: utils" in result
        assert "**Classification:** leaf" in result
        dependents_section = result.split("### Dependents (imported by)")[1]
        assert "- None" in dependents_section.split("###")[0]

    def test_module_with_dependencies(self, project_with_spec: Path) -> None:
        result = get_impact("cli", project_with_spec)
        deps_section = result.split("### Dependencies (imports)")[1].split("###")[0]
        assert "store" in deps_section
        assert "eval" in deps_section
        assert "spec" in deps_section

    def test_contracts_owned(self, project_with_spec: Path) -> None:
        result = get_impact("models", project_with_spec)
        assert "### Contracts Owned" in result
        assert "ProjectState" in result
        assert "FactoryConfig" in result

    def test_no_contracts_owned(self, project_with_spec: Path) -> None:
        result = get_impact("utils", project_with_spec)
        assert "### Contracts Owned" not in result

    def test_change_impact_section(self, project_with_spec: Path) -> None:
        result = get_impact("models", project_with_spec)
        assert "### Change Impact" in result
        assert "hub" in result.split("### Change Impact")[1]
        assert "HIGH" in result.split("### Change Impact")[1]

    def test_module_not_found(self, project_with_spec: Path) -> None:
        with pytest.raises(ValueError, match="Module 'nonexistent' not found"):
            get_impact("nonexistent", project_with_spec)

    def test_case_insensitive_lookup(self, project_with_spec: Path) -> None:
        result = get_impact("CLI", project_with_spec)
        assert "## Impact: cli" in result

    def test_spec_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            get_impact("models", tmp_path)

    def test_module_role_included(self, project_with_spec: Path) -> None:
        result = get_impact("store", project_with_spec)
        assert "**Role:** Experiment store managing .factory/ directory" in result

    def test_module_path_included(self, project_with_spec: Path) -> None:
        result = get_impact("store", project_with_spec)
        assert "**Path:** `factory/store.py`" in result

    def test_compact_output(self, project_with_spec: Path) -> None:
        result = get_impact("models", project_with_spec)
        lines = result.strip().splitlines()
        assert len(lines) < 30


class TestAgentPromptSections:
    """Verify that agent prompt files contain the new Repo Spec sections."""

    PROMPTS_DIR = Path(__file__).resolve().parent.parent / "factory" / "agents" / "prompts"

    def test_strategist_has_repo_spec_section(self) -> None:
        content = (self.PROMPTS_DIR / "strategist.md").read_text()
        assert "## Repo Spec (if available)" in content
        assert "blast radius" in content
        assert "hub modules" in content

    def test_builder_has_repo_spec_section(self) -> None:
        content = (self.PROMPTS_DIR / "builder.md").read_text()
        assert "## Repo Spec (if available)" in content
        assert "shared contract" in content
        assert "entry points" in content

    def test_qa_has_repo_spec_section(self) -> None:
        content = (self.PROMPTS_DIR / "qa.md").read_text()
        assert "## Repo Spec (if available)" in content
        assert "change impact predictions" in content
        assert "adversarial QA" in content

    def test_ceo_has_repo_spec_section(self) -> None:
        content = (self.PROMPTS_DIR / "ceo.md").read_text()
        assert "## Repo Spec (if available)" in content
        assert "factory spec impact" in content
        assert "coupling data" in content
