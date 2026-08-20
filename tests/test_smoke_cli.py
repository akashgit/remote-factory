"""Smoke tests for factory core modes and CLI commands.

Tier 4 integration tests that verify end-to-end behavior of the factory's
kept modes (detect, discover, study, design, create, agent, refactory).
Each test patches only the subprocess boundary (invoke_agent / shell) and
lets the real executor, gates, and file I/O run.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.models import AgentRunResult, EvalProfile, ProjectState
from factory.state import detect_state

pytestmark = pytest.mark.smoke


# ── Helpers ───────────────────────────────────────────────────────


HELLO_CLI_FIXTURE = Path(__file__).parent / "fixtures" / "hello-cli"


def _make_git_repo(path: Path) -> None:
    """Initialize a minimal git repo at *path*."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=path,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(path.parent),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


def _copy_hello_cli(dest: Path) -> Path:
    """Copy the hello-cli fixture into *dest* and init a git repo."""
    project = dest / "hello-cli"
    shutil.copytree(HELLO_CLI_FIXTURE, project, ignore=shutil.ignore_patterns("__pycache__"))
    _make_git_repo(project)
    return project


def _stub_agent_result(stdout: str = "OK", return_code: int = 0) -> AgentRunResult:
    return AgentRunResult(stdout=stdout, return_code=return_code)


def _preseed_completed_files(executor: object, workflow: object) -> None:
    """Pre-seed the executor's completed_files with files declared in reads
    that no node produces via writes, so _wait_for_reads doesn't block."""
    all_writes: set[str] = set()
    all_reads: set[str] = set()
    for node in workflow.nodes.values():  # type: ignore[union-attr]
        all_writes |= node.writes or set()
        all_reads |= node.reads or set()
    orphan_reads = all_reads - all_writes
    executor.completed_files |= orphan_reads  # type: ignore[union-attr]


def _make_mock_invoke_agent(project: Path, canned: dict[str, str]):
    """Build a mock invoke_agent that writes artifact files based on task content."""

    async def mock_invoke_agent(role, task, project_path, **kwargs) -> tuple[str, int]:
        response = canned.get(role, f"OK from {role}")

        strategy_dir = project_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True, exist_ok=True)
        reviews_dir = project_path / ".factory" / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = project_path / ".factory" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        write_targets = re.findall(
            r"Write (?:findings|output) to (\S+)", task
        )
        for rel_path in write_targets:
            rel_path = rel_path.rstrip(".")
            full = project_path / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(response)

        if role == "strategist" and "current.md" not in " ".join(write_targets):
            (strategy_dir / "current.md").write_text(response)
        if role == "builder":
            (reviews_dir / "builder-latest.md").write_text(response)
        if role == "health_checker":
            (reviews_dir / "health-check.md").write_text(response)
        if role == "code_reviewer":
            (reviews_dir / "code-review.md").write_text(response)
        if role == "adversarial_tester":
            (reviews_dir / "adversarial-qa.md").write_text(response)

        return response, 0

    return mock_invoke_agent


# ── a) factory detect — all 5 ProjectState values ────────────────


class TestDetect:
    def test_no_repo(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        assert detect_state(missing) == ProjectState.NO_REPO

    def test_no_repo_no_git(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare"
        bare.mkdir()
        assert detect_state(bare) == ProjectState.NO_REPO

    def test_no_factory(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _make_git_repo(project)
        with patch("factory.state._has_open_plan_issues", return_value=False):
            assert detect_state(project) == ProjectState.NO_FACTORY

    def test_repo_incomplete(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _make_git_repo(project)
        with patch("factory.state._has_open_plan_issues", return_value=True):
            assert detect_state(project) == ProjectState.REPO_INCOMPLETE

    def test_evals_pending_review(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _make_git_repo(project)
        factory_dir = project / ".factory"
        factory_dir.mkdir()
        profile_data = {
            "project_type": "python",
            "dimensions": [],
            "tier": "fallback",
            "confidence": 0.5,
            "human_reviewed": False,
        }
        (factory_dir / "eval_profile.json").write_text(json.dumps(profile_data))
        assert detect_state(project) == ProjectState.EVALS_PENDING_REVIEW

    def test_has_factory(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _make_git_repo(project)
        factory_dir = project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text("{}")
        assert detect_state(project) == ProjectState.HAS_FACTORY


# ── b) factory discover — eval profile generation ────────────────


class TestDiscover:
    def test_discover_hello_cli(self, tmp_path: Path) -> None:
        """Run discovery on hello-cli fixture, verify eval_profile.json is valid."""
        project = _copy_hello_cli(tmp_path)

        from factory.discovery.introspect import introspect_project
        from factory.discovery.profile import build_eval_profile

        profile = introspect_project(project)
        eval_profile = build_eval_profile(profile)

        factory_dir = project / ".factory"
        factory_dir.mkdir(parents=True, exist_ok=True)
        ep_path = factory_dir / "eval_profile.json"
        ep_path.write_text(eval_profile.model_dump_json(indent=2))

        assert ep_path.exists()
        loaded = EvalProfile.model_validate_json(ep_path.read_text())
        assert loaded.project_type
        assert loaded.tier in ("explicit", "discovered", "researched", "fallback")
        assert 0.0 <= loaded.confidence <= 1.0


# ── c) factory study — observations file ─────────────────────────


class TestStudy:
    def test_study_hello_cli(self, tmp_path: Path) -> None:
        """Run study on hello-cli, verify observations.md written and non-empty."""
        project = _copy_hello_cli(tmp_path)
        factory_dir = project / ".factory"
        factory_dir.mkdir(parents=True, exist_ok=True)

        from factory.study import study_project

        summary = study_project(project)

        obs_path = factory_dir / "strategy" / "observations.md"
        obs_path.parent.mkdir(parents=True, exist_ok=True)
        obs_path.write_text(summary)

        assert obs_path.exists()
        assert obs_path.stat().st_size > 0
        assert len(summary) > 50


# ── d) factory workflow run design — Tier 4 integration ──────────


class TestDesignWorkflow:
    async def test_design_workflow_with_mocked_agents(self, tmp_path: Path) -> None:
        """Run design_workflow through the real WorkflowExecutor with patched agents."""
        from factory.workflow.definitions import design_workflow
        from factory.workflow.executor import WorkflowExecutor

        project = tmp_path / "design-test"
        project.mkdir()
        _make_git_repo(project)
        factory_dir = project / ".factory"
        for sub in ("strategy", "reviews", "experiments", "archive"):
            (factory_dir / sub).mkdir(parents=True)
        (factory_dir / "config.json").write_text("{}")

        wf = design_workflow()
        assert wf.name == "design"
        assert wf.start_node == "gate_has_factory"

        canned = {
            "researcher": "## Research findings\nResearch output for testing.",
            "strategist": (
                "## Strategy\n### Architecture\nTest arch.\n"
                "### Phase 1: Scaffold\nBuild the scaffold.\n"
            ),
            "builder": "## Build output\ncommit abc123\nPR #1 opened.",
            "health_checker": "## Health Check\nAll tests pass. Score: 0.85.",
            "code_reviewer": "## Code Review\nAll 7 categories PASS.",
            "adversarial_tester": "## Adversarial QA\nAll tests pass. VERDICT: PASS.",
            "archivist": "## Archive\nArchived.",
            "ceo": "PROCEED\n\nAll checks pass.",
        }

        async def mock_run_shell(cmd: str) -> str:
            strategy_dir = project / ".factory" / "strategy"
            strategy_dir.mkdir(parents=True, exist_ok=True)

            if "python3 -c" in cmd and "config.json" in cmd:
                return "PROCEED"
            if "factory graph update" in cmd:
                (strategy_dir / "graph-context.md").write_text("## Graph\nStub.")
                return "Graph updated."
            if "factory study" in cmd:
                obs = "## Observations\nProject analyzed."
                (strategy_dir / "observations.md").write_text(obs)
                return obs
            if "factory discover" in cmd:
                return "Discovered."
            if "factory precheck" in cmd:
                return "PROCEED"
            if "factory workflow run spec-generate" in cmd:
                return "Spec generated."
            if "cat " in cmd and "study-combined.md" in cmd:
                obs = strategy_dir / "observations.md"
                graph = strategy_dir / "graph-context.md"
                parts = []
                if obs.exists():
                    parts.append(obs.read_text())
                if graph.exists():
                    parts.append(graph.read_text())
                combined = "\n".join(parts) or "combined study"
                (strategy_dir / "study-combined.md").write_text(combined)
                return combined
            return "OK"

        mock_invoke = _make_mock_invoke_agent(project, canned)

        with patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke):
            executor = WorkflowExecutor(wf, project, auto_approve=True)
            _preseed_completed_files(executor, wf)
            executor._run_shell = mock_run_shell  # type: ignore[assignment]
            result = await executor.execute()

        assert result.success, f"Workflow failed: {result.halt_reason}"
        assert result.nodes_executed >= 10

        assert (project / ".factory" / "strategy" / "research-similar.md").exists()
        assert (project / ".factory" / "strategy" / "research-techstack.md").exists()
        assert (project / ".factory" / "strategy" / "research-pitfalls.md").exists()
        assert (project / ".factory" / "strategy" / "current.md").exists()


# ── e) factory workflow run create — Tier 4 integration ──────────


class TestCreateWorkflow:
    async def test_create_workflow_with_mocked_agents(self, tmp_path: Path) -> None:
        """Run create_workflow through the real WorkflowExecutor with patched agents."""
        from factory.workflow.definitions import create_workflow
        from factory.workflow.executor import WorkflowExecutor

        project = tmp_path / "create-test"
        project.mkdir()
        _make_git_repo(project)
        factory_dir = project / ".factory"
        for sub in ("strategy", "reviews", "experiments", "archive"):
            (factory_dir / sub).mkdir(parents=True)
        (factory_dir / "config.json").write_text("{}")

        wf = create_workflow()
        assert wf.name == "create"

        canned = {
            "researcher": "## Research\nExisting patterns analyzed.",
            "strategist": (
                "## Strategy\n### Architecture\nMode architecture.\n"
                "### Phase 1: Define workflow\nDefine the new workflow.\n"
            ),
            "builder": "## Build\ncommit def456\nMode created.",
            "health_checker": "## Health Check\nPASS. Score: 0.90.",
            "code_reviewer": "## Code Review\nAll PASS.",
            "adversarial_tester": "## Adversarial QA\nVERDICT: PASS.",
            "archivist": "## Archive\nArchived.",
            "ceo": "PROCEED\n\nAll checks pass.",
        }

        async def mock_run_shell(cmd: str) -> str:
            if "factory precheck" in cmd:
                return "PROCEED"
            if "factory workflow run spec-generate" in cmd:
                return "Spec generated."
            return "OK"

        mock_invoke = _make_mock_invoke_agent(project, canned)

        with patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke):
            executor = WorkflowExecutor(wf, project, auto_approve=True)
            _preseed_completed_files(executor, wf)
            executor._run_shell = mock_run_shell  # type: ignore[assignment]
            result = await executor.execute()

        assert result.success, f"Workflow failed: {result.halt_reason}"
        assert result.nodes_executed >= 8

        assert (project / ".factory" / "strategy" / "research-existing.md").exists()
        assert (project / ".factory" / "strategy" / "research-intent.md").exists()
        assert (project / ".factory" / "strategy" / "research-practices.md").exists()
        assert (project / ".factory" / "strategy" / "current.md").exists()


# ── f) factory agent <role> — prompt resolution + review files ───


class TestAgentInvocation:
    """Test each kept agent role: prompt resolves, review file is written."""

    KEPT_ROLES = [
        "researcher",
        "strategist",
        "builder",
        "health_checker",
        "code_reviewer",
        "adversarial_tester",
        "archivist",
        "ceo",
    ]

    @pytest.fixture
    def agent_project(self, tmp_path: Path) -> Path:
        project = tmp_path / "agent-test"
        project.mkdir()
        _make_git_repo(project)
        factory_dir = project / ".factory"
        for sub in ("reviews", "strategy", "archive"):
            (factory_dir / sub).mkdir(parents=True)
        return project

    @pytest.mark.parametrize("role", KEPT_ROLES)
    async def test_agent_prompt_resolution_and_review(
        self, role: str, agent_project: Path
    ) -> None:
        """Verify prompt resolves and review file is written for each role."""
        from factory.agents.runner import resolve_prompt

        prompt = resolve_prompt(role, agent_project)
        assert len(prompt) > 100, f"Prompt for {role} is suspiciously short"

        mock_result = _stub_agent_result(stdout=f"Agent {role} completed successfully.")
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=mock_result)

        with patch("factory.agents.runner.get_runner", return_value=mock_runner):
            from factory.agents.runner import invoke_agent

            stdout, code = await invoke_agent(
                role,
                f"Test task for {role}",
                agent_project,
                timeout=10.0,
                _track_failures=False,
            )

        assert code == 0
        assert f"Agent {role} completed" in stdout

        review_path = agent_project / ".factory" / "reviews" / f"{role}-latest.md"
        assert review_path.exists(), f"Review file missing for {role}"
        content = review_path.read_text()
        assert f"Agent {role} completed" in content

    async def test_agent_review_tag(self, agent_project: Path) -> None:
        """Verify --review-tag writes to the tagged review file."""
        mock_result = _stub_agent_result(stdout="Tagged output.")
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=mock_result)

        with patch("factory.agents.runner.get_runner", return_value=mock_runner):
            from factory.agents.runner import invoke_agent

            await invoke_agent(
                "researcher",
                "Tagged test",
                agent_project,
                review_tag="similar",
                _track_failures=False,
            )

        tagged_path = (
            agent_project / ".factory" / "reviews" / "researcher-similar-latest.md"
        )
        assert tagged_path.exists()
        assert "Tagged output" in tagged_path.read_text()


# ── g) factory refactory — workspace setup ───────────────────────


class TestRefactory:
    def test_refactory_setup(self, tmp_path: Path) -> None:
        """Verify setup_workspace creates the expected directory structure."""
        from factory.refactory import setup_workspace

        project = tmp_path / "refactory-test"
        project.mkdir()

        workspace = setup_workspace(project)

        assert workspace == project / ".refactory"
        assert workspace.is_dir()

        claude_dir = project / ".claude"
        assert claude_dir.is_dir()

        settings_path = claude_dir / "settings.local.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings or "permissions" in settings

        claude_md = workspace / "CLAUDE.md"
        assert claude_md.exists()
        assert claude_md.stat().st_size > 0

    def test_refactory_session_id(self, tmp_path: Path) -> None:
        """Verify get_session_id creates and persists a session ID."""
        from factory.refactory import get_session_id, setup_workspace

        project = tmp_path / "session-test"
        project.mkdir()
        setup_workspace(project)

        sid1 = get_session_id(project)
        assert sid1
        assert isinstance(sid1, str)

        sid2 = get_session_id(project)
        assert sid1 == sid2

        sid3 = get_session_id(project, reset=True)
        assert sid3 != sid1


# ── h) Deletion safety tests ──────────────────────────────────


DELETED_SYMBOLS = [
    "BobRunner",
    "CodexRunner",
    "OpenCodeRunner",
    "is_dry_run",
    "is_codex_dry_run",
    "is_opencode_dry_run",
    "FACTORY_BOB_DRY_RUN",
    "FACTORY_CODEX_DRY_RUN",
    "FACTORY_OPENCODE_DRY_RUN",
]


class TestPackageImports:
    """Verify the full package import chain works."""

    def test_import_factory(self) -> None:
        import factory  # noqa: F401

    def test_build_parser_loads_all_subparsers(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        assert parser is not None
        assert parser._subparsers is not None


CLI_COMMANDS = [
    "ceo",
    "run",
    "agent",
    "detect",
    "discover",
    "init",
    "study",
    "precheck",
    "graph",
    "spec",
    "workflow",
    "outer-loop",
    "config",
    "refactory",
    "install",
    "tmux",
    "serve-mcp",
    "guard",
    "contained",
    "eval",
    "log",
    "emit",
]


class TestCommandHelp:
    """Run 'factory <cmd> --help' for every surviving CLI command."""

    @pytest.mark.parametrize("cmd", CLI_COMMANDS)
    def test_command_help(self, cmd: str) -> None:
        result = subprocess.run(
            ["factory", cmd, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"'factory {cmd} --help' exited {result.returncode}: {result.stderr}"
        )


class TestRegistryInstantiation:
    """Instantiate every registered workflow mode and verify it has nodes."""

    def test_all_registered_modes_instantiate(self) -> None:
        from factory.workflow.registry import WorkflowRegistry

        WorkflowRegistry.reset()
        entries = WorkflowRegistry.discover()
        assert len(entries) > 0, "No workflows discovered"

        for name, entry in entries.items():
            if entry._workflow_fn is None:
                continue
            wf = entry._workflow_fn()
            assert wf is not None, f"Mode '{name}' returned None"
            assert len(wf.nodes) > 0, f"Mode '{name}' has no nodes"


AGENT_ROLES_WITH_PROMPTS = [
    "researcher",
    "strategist",
    "builder",
    "health_checker",
    "code_reviewer",
    "adversarial_tester",
    "archivist",
    "ceo",
    "refactory",
]


class TestPromptResolution:
    """Verify resolve_prompt returns a non-empty string for every kept role."""

    @pytest.mark.parametrize("role", AGENT_ROLES_WITH_PROMPTS)
    def test_prompt_resolves(self, role: str) -> None:
        from factory.agents.runner import resolve_prompt

        prompt = resolve_prompt(role)
        assert isinstance(prompt, str)
        assert len(prompt) > 100, (
            f"Prompt for '{role}' is suspiciously short ({len(prompt)} chars)"
        )


class TestSuiteCollects:
    """Verify 'pytest --collect-only' succeeds (no broken conftest fixtures)."""

    def test_collect_only(self) -> None:
        result = subprocess.run(
            ["pytest", "--collect-only", "-q", "--ignore=tests/test_mcp_server.py"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"pytest --collect-only failed (rc={result.returncode}): {result.stderr[-500:]}"
        )


class TestNoDanglingReferences:
    """Verify deleted symbols have no remaining references in production code.

    Symbols in DELETED_SYMBOLS are scheduled for removal. Tests are xfail
    until the code is actually deleted — they become safety nets that pass
    once the symbol is gone, and would fail loudly if re-introduced.
    """

    @pytest.mark.parametrize("symbol", DELETED_SYMBOLS)
    @pytest.mark.xfail(reason="symbols scheduled for deletion, not yet removed", strict=False)
    def test_no_reference(self, symbol: str) -> None:
        result = subprocess.run(
            ["grep", "-rn", symbol, "factory/", "--include=*.py"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.stdout == "", (
            f"Deleted symbol '{symbol}' still referenced in production code:\n{result.stdout}"
        )
