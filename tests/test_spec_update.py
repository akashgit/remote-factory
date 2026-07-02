"""Tests for factory.spec.update — agent-based diff scoping and spec update."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.spec.update import DiffScope, _format_scope
from factory.workflow.definitions import (
    improve_workflow,
    register_all,
    spec_update_workflow,
)
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode


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


# ── Agent-based scope_diff ────────────────────────────────────


FIXTURE_SPEC = """\
# Repo Spec

## Modules

### CLI
**Path:** `factory/cli.py`
**Role:** CLI entry point

### Spec
**Path:** `factory/spec/`
**Role:** Spec generation and validation

### Models
**Path:** `factory/models.py`
**Role:** Domain models
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


def _mock_scope_agent() -> AsyncMock:
    """Return a mock invoke_agent that returns a valid DiffScope JSON."""
    data = json.dumps(
        {
            "affected_modules": ["CLI", "Spec"],
            "new_files": ["factory/spec/update.py"],
            "deleted_files": ["factory/old_module.py"],
        }
    )
    return AsyncMock(return_value=(data, 0))


def _mock_scope_agent_failure() -> AsyncMock:
    return AsyncMock(return_value=("error", 1))


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
    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_scope_agent)
    async def test_scopes_experiment_diff(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = _setup_fixture_project(tmp_path)
        scope = await scope_diff(project, experiment_id=1)

        assert "CLI" in scope.affected_modules
        assert "Spec" in scope.affected_modules
        assert "factory/old_module.py" in scope.deleted_files

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_scope_agent)
    async def test_writes_scope_file(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = _setup_fixture_project(tmp_path)
        await scope_diff(project, experiment_id=1)

        scope_path = project / ".factory" / "spec_update_scope.md"
        assert scope_path.is_file()
        content = scope_path.read_text()
        assert "# Spec Update Scope" in content

    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = tmp_path / "empty_project"
        project.mkdir()

        with pytest.raises(FileNotFoundError):
            await scope_diff(project, experiment_id=1)

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_scope_agent)
    async def test_missing_diff_raises(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = tmp_path / "no_diff_project"
        project.mkdir()
        (project / "GRAPH-SPEC.md").write_text(FIXTURE_SPEC)
        (project / ".factory").mkdir()

        with pytest.raises(FileNotFoundError, match="No diff found"):
            await scope_diff(project, experiment_id=99)

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_scope_agent_failure)
    async def test_agent_failure_raises(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = _setup_fixture_project(tmp_path)

        with pytest.raises(RuntimeError, match="Scope diff agent failed"):
            await scope_diff(project, experiment_id=1)

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_scope_agent)
    async def test_haiku_model_used(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.update import scope_diff

        project = _setup_fixture_project(tmp_path)
        await scope_diff(project, experiment_id=1)
        assert mock_agent.call_args.kwargs.get("model") == "haiku"


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
        assert len(all_wf) == 13

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
