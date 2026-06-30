"""Tests for factory.spec.update — diff scoping, W₁₀ workflow, improve integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.spec.update import DiffScope, _format_scope, _map_file_to_module, _parse_diff_files
from factory.workflow.definitions import (
    improve_workflow,
    register_all,
    spec_update_workflow,
)
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode


# ── Diff parsing ────────────────────────────────────────────────


class TestParseDiffFiles:
    def test_modified_file(self) -> None:
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "index abc..def 100644\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,4 @@\n"
            " import os\n"
            "+import sys\n"
        )
        modified, added, deleted = _parse_diff_files(diff)
        assert modified == ["src/main.py"]
        assert added == []
        assert deleted == []

    def test_new_file(self) -> None:
        diff = (
            "diff --git a/src/new.py b/src/new.py\n"
            "new file mode 100644\n"
            "index 0000000..abc1234\n"
            "--- /dev/null\n"
            "+++ b/src/new.py\n"
            "@@ -0,0 +1 @@\n"
            "+x = 1\n"
        )
        modified, added, deleted = _parse_diff_files(diff)
        assert modified == []
        assert added == ["src/new.py"]
        assert deleted == []

    def test_deleted_file(self) -> None:
        diff = (
            "diff --git a/src/old.py b/src/old.py\n"
            "deleted file mode 100644\n"
            "index abc1234..0000000\n"
            "--- a/src/old.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-x = 1\n"
        )
        modified, added, deleted = _parse_diff_files(diff)
        assert modified == []
        assert added == []
        assert deleted == ["src/old.py"]

    def test_mixed_changes(self) -> None:
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "index abc..def 100644\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1,2 @@\n"
            " x\n"
            "+y\n"
            "diff --git a/src/new.py b/src/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/src/new.py\n"
            "@@ -0,0 +1 @@\n"
            "+z\n"
            "diff --git a/src/gone.py b/src/gone.py\n"
            "deleted file mode 100644\n"
            "--- a/src/gone.py\n"
            "+++ /dev/null\n"
        )
        modified, added, deleted = _parse_diff_files(diff)
        assert modified == ["src/main.py"]
        assert added == ["src/new.py"]
        assert deleted == ["src/gone.py"]

    def test_empty_diff(self) -> None:
        modified, added, deleted = _parse_diff_files("")
        assert modified == []
        assert added == []
        assert deleted == []


# ── File-to-module mapping ──────────────────────────────────────


class TestMapFileToModule:
    def test_exact_match(self) -> None:
        modules = [{"name": "cli", "path": "factory/cli.py"}]
        assert _map_file_to_module("factory/cli.py", modules) == "cli"

    def test_directory_match(self) -> None:
        modules = [{"name": "spec", "path": "factory/spec/"}]
        assert _map_file_to_module("factory/spec/update.py", modules) == "spec"

    def test_longest_match_wins(self) -> None:
        modules = [
            {"name": "factory", "path": "factory/"},
            {"name": "spec", "path": "factory/spec/"},
        ]
        assert _map_file_to_module("factory/spec/validate.py", modules) == "spec"

    def test_no_match(self) -> None:
        modules = [{"name": "cli", "path": "factory/cli.py"}]
        assert _map_file_to_module("tests/test_cli.py", modules) is None

    def test_empty_path_skipped(self) -> None:
        modules = [{"name": "unnamed", "path": ""}]
        assert _map_file_to_module("anything.py", modules) is None


# ── Scope formatting ───────────────────────────────────────────


class TestFormatScope:
    def test_formats_with_data(self) -> None:
        scope = DiffScope(
            affected_modules=["cli", "spec"],
            new_files=["factory/new.py"],
            deleted_files=["factory/old.py"],
        )
        output = _format_scope(scope)
        assert "# Spec Update Scope" in output
        assert "- cli" in output
        assert "- spec" in output
        assert "- factory/new.py" in output
        assert "- factory/old.py" in output

    def test_formats_empty(self) -> None:
        scope = DiffScope()
        output = _format_scope(scope)
        assert "None" in output


# ── Diff scoping with fixture ──────────────────────────────────


FIXTURE_SPEC = """\
# Repo Spec

## Modules

### CLI
**Path:** `factory/cli.py`
**Role:** CLI entry point
**Exports:** `main`
**Depends on:** spec, models
**Contracts owned:** None

### Spec
**Path:** `factory/spec/`
**Role:** Spec generation and validation
**Exports:** `generate_spec, validate_spec`
**Depends on:** models
**Contracts owned:** `RepoSpec`

### Models
**Path:** `factory/models.py`
**Role:** Domain models
**Exports:** `FactoryConfig, EvalProfile`
**Depends on:** None
**Contracts owned:** `FactoryConfig, EvalProfile`

## Dependency Edges

| Source | Target | Type | Coupling |
|--------|--------|------|----------|
| CLI | Spec | direct | strong |
| CLI | Models | direct | strong |
| Spec | Models | direct | strong |

## Shared Contracts

| Name | Defined In | Used By | Change Risk |
|------|-----------|---------|-------------|
| FactoryConfig | Models | CLI, Spec | high |

## Entry Points

| Name | Module | Type |
|------|--------|------|
| factory | CLI | cli |

## Change Impact

| Module | Classification | Dependents | Impact |
|--------|---------------|------------|--------|
| Models | hub | CLI, Spec | high |
| Spec | leaf | CLI | medium |
| CLI | leaf | — | low |
"""

FIXTURE_DIFF = """\
diff --git a/factory/spec/update.py b/factory/spec/update.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/factory/spec/update.py
@@ -0,0 +1,5 @@
+def scope_diff():
+    pass
diff --git a/factory/cli.py b/factory/cli.py
index abc..def 100644
--- a/factory/cli.py
+++ b/factory/cli.py
@@ -1,3 +1,5 @@
 import os
+import sys
diff --git a/factory/old_module.py b/factory/old_module.py
deleted file mode 100644
--- a/factory/old_module.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""


def _setup_fixture_project(tmp_path: Path) -> Path:
    """Create a fixture project with a repo spec and experiment diff."""
    project = tmp_path / "myproject"
    project.mkdir()

    (project / "GRAPH-SPEC.md").write_text(FIXTURE_SPEC)
    factory_dir = project / ".factory"
    factory_dir.mkdir()

    exp_dir = factory_dir / "experiments" / "1"
    exp_dir.mkdir(parents=True)
    (exp_dir / "changes.diff").write_text(FIXTURE_DIFF)

    return project


class TestScopeDiff:
    def test_scopes_experiment_diff(self, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = _setup_fixture_project(tmp_path)
        scope = scope_diff(project, experiment_id=1)

        assert "CLI" in scope.affected_modules
        assert "Spec" in scope.affected_modules
        assert "factory/spec/update.py" in scope.new_files or "Spec" in scope.affected_modules
        assert "factory/old_module.py" in scope.deleted_files

    def test_writes_scope_file(self, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = _setup_fixture_project(tmp_path)
        scope_diff(project, experiment_id=1)

        scope_path = project / ".factory" / "spec_update_scope.md"
        assert scope_path.is_file()
        content = scope_path.read_text()
        assert "# Spec Update Scope" in content

    def test_missing_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = tmp_path / "empty_project"
        project.mkdir()

        with pytest.raises(FileNotFoundError, match="No repo spec found"):
            scope_diff(project, experiment_id=1)

    def test_missing_diff_raises(self, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = tmp_path / "no_diff_project"
        project.mkdir()
        (project / "GRAPH-SPEC.md").write_text(FIXTURE_SPEC)
        (project / ".factory").mkdir()

        with pytest.raises(FileNotFoundError, match="No diff found"):
            scope_diff(project, experiment_id=99)


# ── W₁₀ Spec Update workflow ──────────────────────────────────


class TestSpecUpdateWorkflow:
    def test_validates(self) -> None:
        wf = spec_update_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"spec-update workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = spec_update_workflow()
        assert wf.name == "spec-update"

    def test_start_node(self) -> None:
        wf = spec_update_workflow()
        assert wf.start_node == "diff_scope"

    def test_no_trigger(self) -> None:
        wf = spec_update_workflow()
        assert wf.trigger is None

    def test_has_required_nodes(self) -> None:
        wf = spec_update_workflow()
        expected = {"diff_scope", "patch", "gate_patch", "revalidate", "gate_revalidate"}
        assert expected == set(wf.nodes.keys())

    def test_diff_scope_is_fn(self) -> None:
        wf = spec_update_workflow()
        node = wf.nodes["diff_scope"]
        assert isinstance(node, FnNode)
        assert "factory spec scope" in node.command

    def test_patch_is_opus_agent(self) -> None:
        wf = spec_update_workflow()
        node = wf.nodes["patch"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert node.model == "opus"

    def test_gates_are_ceo(self) -> None:
        wf = spec_update_workflow()
        for gate_id in ("gate_patch", "gate_revalidate"):
            gate = wf.nodes[gate_id]
            assert isinstance(gate, GateNode)
            assert gate.evaluator_type == "agent"
            assert gate.evaluator_role == AgentRole.CEO

    def test_revalidate_is_fn(self) -> None:
        wf = spec_update_workflow()
        node = wf.nodes["revalidate"]
        assert isinstance(node, FnNode)
        assert "factory spec validate" in node.command

    def test_reloop_from_gate_revalidate_to_patch(self) -> None:
        wf = spec_update_workflow()
        reloop_edges = [
            e for e in wf.edges if e.source == "gate_revalidate" and e.target == "patch"
        ]
        assert len(reloop_edges) == 1


# ── Registry includes W₁₀ ─────────────────────────────────────


class TestRegistryIncludesSpecUpdate:
    def test_register_all_includes_spec_update(self) -> None:
        all_wf = register_all()
        assert "spec-update" in all_wf

    def test_register_all_count(self) -> None:
        all_wf = register_all()
        assert len(all_wf) == 10

    def test_all_workflows_validate(self) -> None:
        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"


# ── Improve workflow integration ───────────────────────────────


class TestImproveWorkflowSpecUpdate:
    def test_has_spec_update_node(self) -> None:
        wf = improve_workflow()
        assert "spec_update" in wf.nodes

    def test_spec_update_is_fn(self) -> None:
        wf = improve_workflow()
        node = wf.nodes["spec_update"]
        assert isinstance(node, FnNode)

    def test_spec_update_is_non_blocking(self) -> None:
        wf = improve_workflow()
        node = wf.nodes["spec_update"]
        assert node.blocking is False

    def test_archivist_to_spec_update_edge(self) -> None:
        wf = improve_workflow()
        edges = [e for e in wf.edges if e.source == "archivist" and e.target == "spec_update"]
        assert len(edges) == 1

    def test_improve_still_validates(self) -> None:
        wf = improve_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"improve workflow has issues: {issues}"
