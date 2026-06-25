"""Tests for factory.spec.parser — Markdown parsing of repo_spec.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.spec.parser import (
    RepoSpec,
    parse_spec,
)

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
def spec_file(tmp_path: Path) -> Path:
    p = tmp_path / "repo_spec.md"
    p.write_text(SAMPLE_SPEC)
    return p


class TestParseSpec:
    def test_parses_all_modules(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert len(spec.modules) == 5
        names = [m.name for m in spec.modules]
        assert names == ["cli", "store", "models", "spec", "utils"]

    def test_module_path(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        cli = spec.get_module("cli")
        assert cli is not None
        assert cli.path == "factory/cli.py"

    def test_module_role(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        store = spec.get_module("store")
        assert store is not None
        assert store.role == "Experiment store managing .factory/ directory"

    def test_module_layer(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        models = spec.get_module("models")
        assert models is not None
        assert models.layer == "models"

    def test_module_classification(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        cli = spec.get_module("cli")
        assert cli is not None
        assert cli.classification == "hub"

    def test_module_exports(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        models = spec.get_module("models")
        assert models is not None
        assert models.exports == ["ProjectState", "FactoryConfig", "EvalProfile"]

    def test_module_depends_on(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        cli = spec.get_module("cli")
        assert cli is not None
        assert cli.depends_on == ["store", "eval", "spec"]

    def test_module_contracts_owned(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        store = spec.get_module("store")
        assert store is not None
        assert store.contracts_owned == ["ExperimentRecord"]

    def test_contracts_owned_none(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        cli = spec.get_module("cli")
        assert cli is not None
        assert cli.contracts_owned == []

    def test_depends_on_none(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        models = spec.get_module("models")
        assert models is not None
        assert models.depends_on == []


class TestParseDependencyEdges:
    def test_parses_all_edges(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert len(spec.dependency_edges) == 6

    def test_edge_source_target(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        edge = spec.dependency_edges[0]
        assert edge.source == "cli"
        assert edge.target == "store"

    def test_edge_import_type(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        edge = spec.dependency_edges[0]
        assert edge.import_type == "direct"

    def test_edge_coupling(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        weak_edge = next(e for e in spec.dependency_edges if e.coupling == "weak")
        assert weak_edge.source == "cli"
        assert weak_edge.target == "spec"


class TestParseSharedContracts:
    def test_parses_all_contracts(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert len(spec.shared_contracts) == 3

    def test_contract_name(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert spec.shared_contracts[0].name == "ProjectState"

    def test_contract_defined_in(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert spec.shared_contracts[0].defined_in == "models"

    def test_contract_used_by(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert spec.shared_contracts[0].used_by == ["cli", "store", "eval"]

    def test_contract_change_risk(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert "high" in spec.shared_contracts[0].change_risk


class TestParseEntryPoints:
    def test_parses_all_entry_points(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert len(spec.entry_points) == 2

    def test_entry_point_fields(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        ep = spec.entry_points[0]
        assert ep.name == "factory cli"
        assert ep.module == "cli"
        assert ep.type == "CLI"


class TestParseChangeImpact:
    def test_parses_all_impacts(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert len(spec.change_impact) == 5

    def test_impact_fields(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        imp = spec.change_impact[0]
        assert imp.module == "models"
        assert imp.classification == "hub"
        assert "cli" in imp.dependents
        assert "HIGH" in imp.impact

    def test_impact_no_dependents(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        utils_impact = next(i for i in spec.change_impact if i.module == "utils")
        assert utils_impact.dependents == []


class TestGetModule:
    def test_case_insensitive(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert spec.get_module("CLI") is not None
        assert spec.get_module("cli") is not None

    def test_not_found(self, spec_file: Path) -> None:
        spec = parse_spec(spec_file)
        assert spec.get_module("nonexistent") is None


class TestParseSpecErrors:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_spec(tmp_path / "missing.md")

    def test_empty_spec(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.md"
        p.write_text("")
        spec = parse_spec(p)
        assert isinstance(spec, RepoSpec)
        assert spec.modules == []
        assert spec.dependency_edges == []

    def test_spec_with_no_tables(self, tmp_path: Path) -> None:
        p = tmp_path / "notables.md"
        p.write_text("# Repo Spec\n\n## Modules\n\n### alpha\n- **Path:** src/alpha\n")
        spec = parse_spec(p)
        assert len(spec.modules) == 1
        assert spec.modules[0].name == "alpha"
        assert spec.dependency_edges == []
