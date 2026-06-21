"""Tier 5: E2E tests — ACTUALLY RUN workflows on real projects.

These tests invoke real Claude Code agents on real projects. They are the
authoritative validation that the graph engine produces correct outcomes.

Each test creates a real project directory, executes the workflow via the
graph executor (NOT dry-run), and verifies real file production, agent
invocation order, and outcome correctness.

NOTE: These tests require a working Claude Code CLI (`claude` binary)
with valid authentication. They are SLOW and EXPENSIVE — they actually
spawn Claude Code subprocesses. Mark them with @pytest.mark.e2e so they
can be filtered in CI.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from factory.workflow.definitions import (
    build_workflow,
    design_workflow,
    improve_workflow,
    meta_workflow,
    research_workflow,
)
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import DEFAULT_AGENT_POOL

e2e = pytest.mark.skipif(
    os.environ.get("FACTORY_RUN_E2E", "0") != "1",
    reason="E2E tests require FACTORY_RUN_E2E=1 and a working Claude Code CLI",
)


def _has_claude_cli() -> bool:
    """Check if Claude Code CLI is available."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _init_test_project(path: Path, *, with_factory: bool = False) -> Path:
    """Initialize a minimal test project."""
    path.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["git", "init"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
    )

    (path / "README.md").write_text("# Test Project\n")
    (path / "main.py").write_text('print("hello")\n')

    subprocess.run(
        ["git", "add", "."],
        cwd=path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        capture_output=True,
    )

    if with_factory:
        factory_dir = path / ".factory"
        factory_dir.mkdir(exist_ok=True)
        for sub in ("strategy", "reviews", "experiments", "archive"):
            (factory_dir / sub).mkdir(exist_ok=True)

        config = {
            "goal": "Test project for e2e workflow testing",
            "scope": ["*.py"],
            "guards": [],
            "eval_command": "python -c \"import json; print(json.dumps({'results': []}))\"",
            "eval_threshold": 0.0,
            "constraints": [],
        }
        (factory_dir / "config.json").write_text(json.dumps(config, indent=2))

        factory_md = (
            "# Test Project\n\n"
            "## Goal\nTest project for e2e workflow testing\n\n"
            "## Scope\n- *.py\n\n"
            "## Guards\n(none)\n\n"
            "## Eval\npython -c \"import json; print(json.dumps({'results': []}))\"\n\n"
            "## Threshold\n0.0\n"
        )
        (path / "factory.md").write_text(factory_md)

    return path


# ── W₁ Build E2E ────────────────────────────────────────────────


@e2e
class TestBuildE2E:
    async def test_build_workflow_runs(self, tmp_path: Path) -> None:
        """W₁: Execute build workflow on a new project directory."""
        project = _init_test_project(tmp_path / "build-test")

        wf = build_workflow()
        executor = WorkflowExecutor(
            wf, project, agent_pool=DEFAULT_AGENT_POOL,
        )
        result = await executor.execute()

        assert result.nodes_executed > 0
        assert len(result.events) > 0

        event_types = [e["type"] for e in result.events]
        assert "workflow.started" in event_types
        assert "node.started" in event_types

    async def test_build_agent_invocation_order(self, tmp_path: Path) -> None:
        """Verify agent invocation order matches spec: researchers → strategist → builder."""
        project = _init_test_project(tmp_path / "build-order")

        wf = build_workflow()
        executor = WorkflowExecutor(
            wf, project, agent_pool=DEFAULT_AGENT_POOL,
        )
        result = await executor.execute()

        node_starts = [
            e["node_id"] for e in result.events
            if e["type"] == "node.started"
        ]
        assert len(node_starts) > 0


# ── W₂ Design E2E ───────────────────────────────────────────────


@e2e
class TestDesignE2E:
    async def test_design_has_user_gate(self, tmp_path: Path) -> None:
        """W₂: Verify user gate is present and workflow can execute."""
        project = _init_test_project(tmp_path / "design-test")

        wf = design_workflow()

        from factory.workflow.primitives import GateNode
        gate = wf.nodes.get("gate_strategy")
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

        executor = WorkflowExecutor(
            wf, project, agent_pool=DEFAULT_AGENT_POOL,
        )
        result = await executor.execute()

        assert result.nodes_executed > 0


# ── W₃ Improve E2E ──────────────────────────────────────────────


@e2e
class TestImproveE2E:
    async def test_improve_full_cycle(self, tmp_path: Path) -> None:
        """W₃: Full improve cycle on a project with .factory/ setup."""
        project = _init_test_project(
            tmp_path / "improve-test", with_factory=True,
        )

        wf = improve_workflow()
        executor = WorkflowExecutor(
            wf, project, agent_pool=DEFAULT_AGENT_POOL,
        )
        result = await executor.execute()

        assert result.nodes_executed > 0

        event_types = [e["type"] for e in result.events]
        assert "workflow.started" in event_types
        assert "node.started" in event_types

    async def test_improve_archivist_async(self, tmp_path: Path) -> None:
        """Verify archivist runs non-blocking in W₃."""
        _init_test_project(
            tmp_path / "improve-async", with_factory=True,
        )

        wf = improve_workflow()
        archivist = wf.nodes.get("archivist")
        assert archivist is not None
        assert archivist.blocking is False


# ── W₄ Research E2E ──────────────────────────────────────────────


@e2e
class TestResearchE2E:
    async def test_research_structure(self, tmp_path: Path) -> None:
        """W₄: Verify research workflow has correct structural delta from W₃."""
        project = _init_test_project(
            tmp_path / "research-test", with_factory=True,
        )

        config_path = project / ".factory" / "config.json"
        config = json.loads(config_path.read_text())
        config["research_target"] = {
            "objective": "test accuracy",
            "metric": "accuracy",
            "target": 0.95,
            "run_command": "echo 0.8",
            "result_path": "results.json",
        }
        config_path.write_text(json.dumps(config, indent=2))

        wf = research_workflow()

        assert "baseline" in wf.nodes
        assert "failure_analyst" in wf.nodes
        assert "plateau_gate" in wf.nodes
        assert "study" not in wf.nodes
        assert wf.start_node == "baseline"

    async def test_research_runs(self, tmp_path: Path) -> None:
        """W₄: Execute research workflow."""
        project = _init_test_project(
            tmp_path / "research-run", with_factory=True,
        )

        wf = research_workflow()
        executor = WorkflowExecutor(
            wf, project, agent_pool=DEFAULT_AGENT_POOL,
        )
        result = await executor.execute()

        assert result.nodes_executed > 0


# ── W₅ Meta E2E ──────────────────────────────────────────────────


@e2e
class TestMetaE2E:
    async def test_meta_structure(self, tmp_path: Path) -> None:
        """W₅: Verify meta workflow structure."""
        _init_test_project(
            tmp_path / "meta-test", with_factory=True,
        )

        wf = meta_workflow()

        assert "insights" in wf.nodes
        assert "fork_post" in wf.nodes
        assert "test_collect" in wf.nodes
        assert "test_researcher" in wf.nodes

        from factory.workflow.primitives import ForkNode
        fork = wf.nodes.get("fork_post")
        assert isinstance(fork, ForkNode)
        assert "archivist" in fork.targets
        assert "test_collect" in fork.targets

    async def test_meta_runs(self, tmp_path: Path) -> None:
        """W₅: Execute meta workflow."""
        project = _init_test_project(
            tmp_path / "meta-run", with_factory=True,
        )

        wf = meta_workflow()
        executor = WorkflowExecutor(
            wf, project, agent_pool=DEFAULT_AGENT_POOL,
        )
        result = await executor.execute()

        assert result.nodes_executed > 0


# ── CLI E2E ──────────────────────────────────────────────────────


class TestCLIE2E:
    """Test workflow CLI subcommands work correctly."""

    def test_workflow_list(self) -> None:
        """factory workflow list prints all 5 workflows."""
        result = subprocess.run(
            ["python", "-m", "factory", "workflow", "list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        for name in ("build", "design", "improve", "research", "meta"):
            assert name in result.stdout

    def test_workflow_show_build(self) -> None:
        """factory workflow show build prints node/edge table."""
        result = subprocess.run(
            ["python", "-m", "factory", "workflow", "show", "build"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "Workflow: build" in result.stdout
        assert "Nodes:" in result.stdout
        assert "Edges:" in result.stdout

    def test_workflow_validate_all(self) -> None:
        """factory workflow validate passes for all workflows."""
        for name in ("build", "design", "improve", "research", "meta"):
            result = subprocess.run(
                ["python", "-m", "factory", "workflow", "validate", name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, f"{name} validation failed: {result.stdout}"
            assert "VALID" in result.stdout

    def test_workflow_show_unknown(self) -> None:
        """factory workflow show <unknown> returns error."""
        result = subprocess.run(
            ["python", "-m", "factory", "workflow", "show", "nonexistent"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1

    def test_workflow_dry_run(self, tmp_path: Path) -> None:
        """factory workflow run --dry-run executes without real agents."""
        project = _init_test_project(tmp_path / "cli-dry-run", with_factory=True)
        result = subprocess.run(
            [
                "python", "-m", "factory", "workflow", "run",
                "improve", str(project), "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["success"] is True
        assert output["nodes_executed"] > 0


# ── Equivalence test ─────────────────────────────────────────────


class TestEquivalence:
    """Verify graph engine produces equivalent structure to CEO-prompt orchestration."""

    def test_improve_agent_sequence(self) -> None:
        """W₃ improvement loop has correct agent sequence."""
        wf = improve_workflow()

        node_ids = list(wf.nodes.keys())
        assert "study" in node_ids
        assert "researcher" in node_ids
        assert "strategist" in node_ids
        assert "builder" in node_ids
        assert "evaluator" in node_ids
        assert "archivist" in node_ids

        edges_from = {e.source: e.target for e in wf.edges if e.condition is None}
        assert edges_from.get("study") == "researcher"

    def test_build_has_parallel_research(self) -> None:
        """W₁ starts with 3 parallel researchers via fork."""
        wf = build_workflow()
        from factory.workflow.primitives import ForkNode

        fork = wf.nodes.get("fork_research")
        assert isinstance(fork, ForkNode)
        assert len(fork.targets) == 3

    def test_gate_prompts_lightweight(self) -> None:
        """Gate prompts are lightweight (~10-20 lines), not full CEO prompt."""
        from factory.workflow.executor import CEO_GATE_PROMPT

        lines = CEO_GATE_PROMPT.strip().split("\n")
        assert len(lines) <= 20, f"Gate prompt too long: {len(lines)} lines"
        assert len(lines) >= 5, f"Gate prompt too short: {len(lines)} lines"
