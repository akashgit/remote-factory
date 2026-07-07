"""Tests for factory.spec.ops — validate, scope, update, impact operations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.spec.ops import _parse_verdict, validate_spec
from factory.workflow.definitions import (
    improve_workflow,
    spec_update_workflow,
)
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode


# ── Fixtures ────────────────────────────────────────────────────

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
"""

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

PASS_REPORT = """\
# Spec Validation Report

## Errors
None

## Warnings
- Orphan module: 'utils' has zero consumers

Verdict: PASS
"""

FAIL_REPORT = """\
# Spec Validation Report

## Errors
- Module 'cli': path 'factory/cli.py' does not exist

## Warnings
- Orphan module: 'utils' has zero consumers

Verdict: FAIL
"""

SCOPE_REPORT = """\
## Affected Modules
- CLI
- Spec

## New Files
- factory/spec/update.py

## Deleted Files
- factory/old_module.py
"""


def _write_spec(project: Path, spec_content: str) -> Path:
    spec_path = project / "GRAPH-SPEC.md"
    spec_path.write_text(spec_content)
    return spec_path


def _setup_fixture_project(tmp_path: Path) -> Path:
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "GRAPH-SPEC.md").write_text(FIXTURE_SPEC)
    factory_dir = project / ".factory"
    factory_dir.mkdir()
    exp_dir = factory_dir / "experiments" / "1"
    exp_dir.mkdir(parents=True)
    (exp_dir / "changes.diff").write_text(FIXTURE_DIFF)
    return project


# ── _parse_verdict ──────────────────────────────────────────────


class TestParseVerdict:
    def test_pass(self) -> None:
        assert _parse_verdict("some text\nVerdict: PASS\n") is True

    def test_fail(self) -> None:
        assert _parse_verdict("some text\nVerdict: FAIL\n") is False

    def test_missing_defaults_true(self) -> None:
        assert _parse_verdict("no verdict here") is True

    def test_verdict_mid_text(self) -> None:
        assert _parse_verdict("intro\nVerdict: FAIL\nmore text") is False


# ── validate_spec integration ───────────────────────────────────


class TestValidateSpec:
    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(PASS_REPORT, 0)),
    )
    async def test_pass_writes_report(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        _report, is_valid = await validate_spec(tmp_path)
        assert is_valid
        report_path = tmp_path / ".factory" / "spec_validation.md"
        assert report_path.is_file()
        assert "Verdict: PASS" in report_path.read_text()

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(FAIL_REPORT, 0)),
    )
    async def test_fail_verdict(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        _report, is_valid = await validate_spec(tmp_path)
        assert not is_valid

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("error occurred", 1)),
    )
    async def test_agent_failure_returns_valid(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        _report, is_valid = await validate_spec(tmp_path)
        assert is_valid

    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await validate_spec(tmp_path)


# ── _get_diff_text ──────────────────────────────────────────────


class TestGetDiffText:
    def test_reads_experiment_diff_file(self, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        exp_dir = tmp_path / ".factory" / "experiments" / "1"
        exp_dir.mkdir(parents=True)
        (exp_dir / "changes.diff").write_text("diff content")

        result = _get_diff_text(tmp_path, experiment_id=1, spec_rel="GRAPH-SPEC.md")
        assert result == "diff content"

    def test_missing_experiment_diff_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        with pytest.raises(FileNotFoundError, match="No diff found"):
            _get_diff_text(tmp_path, experiment_id=99, spec_rel="GRAPH-SPEC.md")

    @patch("factory.spec.ops.subprocess.run")
    def test_git_diff_from_spec_commit(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="diff --git a/x.py b/x.py\n"),
        ]

        result = _get_diff_text(tmp_path, experiment_id=None, spec_rel="GRAPH-SPEC.md")
        assert "diff --git" in result

    @patch("factory.spec.ops.subprocess.run")
    def test_git_diff_fallback_to_head_minus_1(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="fallback diff\n"),
        ]

        result = _get_diff_text(tmp_path, experiment_id=None, spec_rel="GRAPH-SPEC.md")
        assert result == "fallback diff\n"

    @patch("factory.spec.ops.subprocess.run")
    def test_initial_commit_uses_root_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=128, stderr="fatal: bad revision"),
            MagicMock(returncode=0, stdout="root diff\n"),
        ]

        result = _get_diff_text(tmp_path, experiment_id=None, spec_rel="GRAPH-SPEC.md")
        assert result == "root diff\n"
        root_call = mock_run.call_args_list[2]
        assert "--root" in root_call[0][0]

    @patch("factory.spec.ops.subprocess.run")
    def test_root_diff_failure_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=128, stderr="fatal: bad revision"),
            MagicMock(returncode=1, stderr="fatal: unable to read tree"),
        ]

        with pytest.raises(RuntimeError, match="git diff failed"):
            _get_diff_text(tmp_path, experiment_id=None, spec_rel="GRAPH-SPEC.md")

    @patch("factory.spec.ops.subprocess.run")
    def test_git_diff_failure_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=128, stderr="fatal: bad revision"),
        ]

        with pytest.raises(RuntimeError, match="git diff failed"):
            _get_diff_text(tmp_path, experiment_id=None, spec_rel="GRAPH-SPEC.md")


# ── scope_diff / update_spec error paths ────────────────────────


class TestScopeDiffErrors:
    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import scope_diff

        project = tmp_path / "empty_project"
        project.mkdir()

        with pytest.raises(FileNotFoundError):
            await scope_diff(project, experiment_id=1)

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("error", 1)),
    )
    async def test_agent_failure_raises(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.ops import scope_diff

        project = _setup_fixture_project(tmp_path)

        with pytest.raises(RuntimeError, match="Scope diff agent failed"):
            await scope_diff(project, experiment_id=1)


class TestUpdateSpecErrors:
    async def test_no_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import update_spec

        with pytest.raises(FileNotFoundError, match="No repo spec"):
            await update_spec(tmp_path)


# ── get_impact error paths ──────────────────────────────────────


class TestGetImpactErrors:
    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import get_impact

        with pytest.raises(FileNotFoundError):
            await get_impact("models", tmp_path)

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("error", 1)),
    )
    async def test_agent_failure_raises(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.ops import get_impact

        (tmp_path / "GRAPH-SPEC.md").write_text(BASIC_SPEC)

        with pytest.raises(RuntimeError, match="Impact analysis agent failed"):
            await get_impact("models", tmp_path)


# ── W₁₀ Spec Update workflow ───────────────────────────────────


class TestSpecUpdateWorkflow:
    def test_validates(self) -> None:
        wf = spec_update_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"spec-update workflow has issues: {issues}"

    def test_name(self) -> None:
        assert spec_update_workflow().name == "spec-update"

    def test_start_node(self) -> None:
        assert spec_update_workflow().start_node == "diff_scope"

    def test_has_required_nodes(self) -> None:
        wf = spec_update_workflow()
        expected = {"diff_scope", "patch", "gate_patch", "revalidate", "gate_revalidate"}
        assert expected == set(wf.nodes.keys())

    def test_diff_scope_is_fn(self) -> None:
        node = spec_update_workflow().nodes["diff_scope"]
        assert isinstance(node, FnNode)
        assert "factory spec scope" in node.command

    def test_patch_is_opus_agent(self) -> None:
        node = spec_update_workflow().nodes["patch"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert node.model == "opus"

    def test_gates_are_ceo(self) -> None:
        wf = spec_update_workflow()
        for gate_id in ("gate_patch", "gate_revalidate"):
            gate = wf.nodes[gate_id]
            assert isinstance(gate, GateNode)
            assert gate.evaluator_role == AgentRole.CEO

    def test_revalidate_is_fn(self) -> None:
        node = spec_update_workflow().nodes["revalidate"]
        assert isinstance(node, FnNode)
        assert "factory spec validate" in node.command

    def test_reloop_from_gate_revalidate_to_patch(self) -> None:
        wf = spec_update_workflow()
        reloop_edges = [
            e for e in wf.edges if e.source == "gate_revalidate" and e.target == "patch"
        ]
        assert len(reloop_edges) == 1


# ── Improve workflow integration ────────────────────────────────


class TestImproveWorkflowSpecUpdate:
    def test_has_spec_update_node(self) -> None:
        assert "spec_update" in improve_workflow().nodes

    def test_spec_update_is_non_blocking_fn(self) -> None:
        node = improve_workflow().nodes["spec_update"]
        assert isinstance(node, FnNode)
        assert node.blocking is False

    def test_archivist_to_spec_update_edge(self) -> None:
        wf = improve_workflow()
        edges = [e for e in wf.edges if e.source == "archivist" and e.target == "spec_update"]
        assert len(edges) == 1

    def test_improve_still_validates(self) -> None:
        issues = improve_workflow().validate_graph()
        assert issues == [], f"improve workflow has issues: {issues}"
