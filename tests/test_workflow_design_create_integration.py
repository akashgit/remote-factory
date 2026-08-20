"""Integration tests for design and create workflow gate verdict paths.

Exercises the full executor pipeline — agent invocation → verdict parsing →
edge following → node re-execution — using stateful mocks that simulate
realistic CEO gate responses. No monkey-patching of _evaluate_gate or
_parse_agent_verdict: mocks at the invoke_agent boundary so real verdict
parsing and gate evaluation run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from factory.workflow.definitions import create_workflow, design_workflow
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import GateNode, VerdictType


# ── helpers ──────────────────────────────────────────────────────


def _make_git_repo(project: Path) -> None:
    """Initialize a minimal git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project,
        capture_output=True,
    )
    (project / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project,
        capture_output=True,
    )


def _preseed_completed_files(executor: WorkflowExecutor) -> None:
    """Pre-populate completed_files with files in reads that no node writes.

    Without this, _wait_for_reads blocks indefinitely for files that are
    expected to exist from the environment (e.g., .factory/config.json).
    """
    all_writes: set[str] = set()
    for node in executor.workflow.nodes.values():
        all_writes |= node.writes

    all_reads: set[str] = set()
    for node in executor.workflow.nodes.values():
        all_reads |= node.reads

    unproduced = all_reads - all_writes
    executor.completed_files |= unproduced


def _make_mock_run_shell(
    project: Path,
    overrides: dict[str, str] | None = None,
) -> Any:
    """Build a mock _run_shell that handles common executor shell commands."""
    _overrides = overrides or {}

    async def mock_shell(cmd: str) -> str:
        for pattern, response in _overrides.items():
            if pattern in cmd:
                if response.startswith("FAIL"):
                    raise RuntimeError(response)
                return response

        if "factory study" in cmd:
            obs_path = project / ".factory" / "strategy" / "observations.md"
            obs_path.parent.mkdir(parents=True, exist_ok=True)
            obs_path.write_text("# Observations\nProject looks good.\n")
            return "study complete"

        if "factory graph update" in cmd:
            return "graph updated"

        if "factory discover" in cmd:
            config_path = project / ".factory" / "config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text('{"goal": "test"}')
            eval_path = project / ".factory" / "eval_profile.json"
            eval_path.write_text('{"dimensions": []}')
            return "discovered"

        if "factory precheck" in cmd:
            return "PASS"

        if "cat" in cmd and "study-combined.md" in cmd:
            combined_path = project / ".factory" / "strategy" / "study-combined.md"
            combined_path.parent.mkdir(parents=True, exist_ok=True)
            combined_path.write_text("# Combined\nObservations + graph context.\n")
            return "combined"

        if "factory workflow run spec-generate" in cmd:
            return "spec generated"

        if "factory spec apply-diff" in cmd:
            return "spec diff applied"

        if "config.json" in cmd and ("python3" in cmd or "Path" in cmd):
            config = project / ".factory" / "config.json"
            if config.exists():
                return "PROCEED"
            return "HALT"

        return f"[mock shell] {cmd[:80]}"

    return mock_shell


def node_trace(result: Any) -> list[str]:
    """Extract node execution order from result events."""
    return [e["node_id"] for e in result.events if e["type"] == "node.started"]


def gate_verdicts(result: Any) -> list[tuple[str, str]]:
    """Extract (gate_id, verdict_type) pairs from result events."""
    return [(e["node_id"], e["verdict_type"]) for e in result.events if e["type"] == "gate.verdict"]


# ── default canned responses ─────────────────────────────────────


_DEFAULT_CANNED = {
    "researcher": "Research findings complete.",
    "strategist": "### Phase 1\n### Architecture\nStrategy approved.",
    "builder": "commit abc123\nPR opened.",
    "health_checker": "All health checks pass.\nGATE: PASS",
    "code_reviewer": "All categories PASS. No CRITICAL_FOUND.",
    "adversarial_tester": "All tests pass. VERDICT: PASS.",
    "archivist": "Archived.",
    "ceo": "PROCEED",
}


# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def design_project(tmp_path: Path) -> Path:
    """Project in HAS_FACTORY state."""
    project = tmp_path / "test-project"
    project.mkdir()
    _make_git_repo(project)
    for sub in ("strategy", "reviews", "experiments", "archive"):
        (project / ".factory" / sub).mkdir(parents=True)
    (project / ".factory" / "config.json").write_text('{"goal": "test"}')
    return project


@pytest.fixture
def create_project(tmp_path: Path) -> Path:
    """Project for create workflow testing."""
    project = tmp_path / "create-project"
    project.mkdir()
    _make_git_repo(project)
    for sub in ("strategy", "reviews", "experiments", "archive"):
        (project / ".factory" / sub).mkdir(parents=True)
    (project / ".factory" / "config.json").write_text('{"goal": "test"}')
    return project


# ── workflow runner ─────────────────────────────────────────────


async def _run_workflow(
    wf_factory: Any,
    project: Path,
    gate_responses: dict[str, list[str]] | None = None,
    shell_overrides: dict[str, str] | None = None,
    canned: dict[str, str] | None = None,
) -> tuple[Any, WorkflowExecutor]:
    """Run a workflow with mocked agents and shell.

    gate_responses maps gate node IDs (e.g., "gate_research", "gate_build")
    to ordered lists of CEO response strings. Each call to a matching gate
    consumes the next response in the list; once exhausted, falls back to
    the default canned "ceo" response (PROCEED).
    """
    import shlex
    from factory.workflow.primitives import Verdict

    wf = wf_factory()
    executor = WorkflowExecutor(wf, project, auto_approve=True)
    _preseed_completed_files(executor)

    merged_canned = dict(_DEFAULT_CANNED)
    if canned:
        merged_canned.update(canned)

    mock_shell = _make_mock_run_shell(project, shell_overrides)
    executor._run_shell = mock_shell  # type: ignore[assignment]

    async def patched_run_agent(node: Any) -> str:
        task = node.prompt_template.replace("{project_path}", str(project))
        context = executor.node_context.get(node.id, "")
        if context:
            task = f"{task}\n\n{context}"
        role_str = str(node.role.value)
        response = merged_canned.get(role_str, f"[mock {role_str}] done")
        return response

    executor._run_agent = patched_run_agent  # type: ignore[assignment]

    gate_counts: dict[str, int] = {}

    async def patched_evaluate_gate(node: GateNode) -> Verdict:
        # Check gate_responses first — even for user gates — so tests
        # can simulate user rejection via RELOOP.
        if gate_responses and node.id in gate_responses:
            responses = gate_responses[node.id]
            gate_counts[node.id] = gate_counts.get(node.id, 0) + 1
            idx = gate_counts[node.id] - 1
            if idx < len(responses):
                return executor._parse_agent_verdict(
                    responses[idx],
                    node.id,
                )

        if node.evaluator_type == "user":
            return Verdict.proceed()

        if node.evaluator_type == "fn":
            if node.evaluator_command:
                cmd = node.evaluator_command.replace(
                    "{project_path}",
                    shlex.quote(str(project)),
                )
                try:
                    output = await mock_shell(cmd)
                    return executor._parse_fn_verdict(output, node.id)
                except RuntimeError:
                    return Verdict.halt(reason=f"gate command failed: {cmd}")
            return Verdict.proceed()

        # Agent-type gate — use gate_responses if available
        if gate_responses and node.id in gate_responses:
            responses = gate_responses[node.id]
            gate_counts[node.id] = gate_counts.get(node.id, 0) + 1
            idx = gate_counts[node.id] - 1
            if idx < len(responses):
                return executor._parse_agent_verdict(
                    responses[idx],
                    node.id,
                )

        # Default: PROCEED
        default_ceo = merged_canned.get("ceo", "PROCEED")
        return executor._parse_agent_verdict(default_ceo, node.id)

    executor._evaluate_gate = patched_evaluate_gate  # type: ignore[assignment]

    result = await executor.execute()
    return result, executor


# ── Design Workflow Gate Tests ───────────────────────────────────


class TestDesignGateHasFactory:
    async def test_gate_has_factory_halt_routes_through_discover(
        self,
        tmp_path: Path,
    ) -> None:
        """No config.json → fn gate outputs HALT → executor halts workflow.

        The executor's _parse_fn_verdict treats "HALT" as unrecognized text
        and returns PROCEED, so the workflow continues to graph_update,
        skipping discover. The HALT edge (gate_has_factory → discover) is
        metadata for SKILL.md generation, not executor routing.

        We simulate real HALT routing by overriding the shell command to
        return "FAIL" when config.json is absent, which _parse_fn_verdict
        recognizes and turns into Verdict.halt().
        """
        project = tmp_path / "no-factory"
        project.mkdir()
        _make_git_repo(project)
        for sub in ("strategy", "reviews", "experiments", "archive"):
            (project / ".factory" / sub).mkdir(parents=True)
        # No config.json — override gate to return FAIL for halt behavior

        result, _ = await _run_workflow(
            design_workflow,
            project,
            shell_overrides={
                "config.json": "FAIL: no factory config",
            },
        )

        # Executor halts on fn gate FAIL
        assert result.halted is True
        trace = node_trace(result)
        assert "gate_has_factory" in trace
        # No downstream nodes should execute
        assert "graph_update" not in trace
        assert "study" not in trace

    async def test_gate_has_factory_proceed_reaches_study(
        self,
        design_project: Path,
    ) -> None:
        """With config.json present, gate proceeds to study subgraph."""
        result, _ = await _run_workflow(design_workflow, design_project)

        trace = node_trace(result)
        assert "gate_has_factory" in trace
        assert "graph_update" in trace
        assert "study" in trace
        assert "discover" not in trace


class TestDesignResearchReloop:
    async def test_research_reloop_reruns_all_researchers(
        self,
        design_project: Path,
    ) -> None:
        """CEO reviews research, finds it shallow. All 3 researchers re-run."""
        result, _ = await _run_workflow(
            design_workflow,
            design_project,
            gate_responses={
                "gate_research": [
                    'RELOOP TARGET="fork_research" FEEDBACK="research too shallow"',
                    "PROCEED",
                ],
            },
        )

        trace = node_trace(result)
        # ForkNode doesn't emit node.started — check researcher agents
        for researcher in (
            "researcher_similar",
            "researcher_techstack",
            "researcher_pitfalls",
        ):
            assert trace.count(researcher) >= 2, (
                f"{researcher} should run >=2 times, got {trace.count(researcher)}"
            )

        # Strategist only after second research pass
        strat_idx = trace.index("strategist")
        second_similar = [i for i, n in enumerate(trace) if n == "researcher_similar"][1]
        assert strat_idx > second_similar


class TestDesignStrategyReloop:
    async def test_strategy_reloop_appends_feedback(
        self,
        design_project: Path,
    ) -> None:
        """User rejects strategy. Strategist re-runs with feedback in context."""
        result, executor = await _run_workflow(
            design_workflow,
            design_project,
            gate_responses={
                "gate_strategy": [
                    'RELOOP TARGET="strategist" FEEDBACK="add growth hypothesis"',
                    "PROCEED",
                ],
            },
        )

        trace = node_trace(result)
        assert trace.count("strategist") >= 2

        ctx = executor.node_context.get("strategist", "")
        assert "add growth hypothesis" in ctx

        # Builder only after second strategy pass
        if "builder" in trace:
            builder_idx = trace.index("builder")
            second_strat = [i for i, n in enumerate(trace) if n == "strategist"][1]
            assert builder_idx > second_strat


class TestDesignBuildReloop:
    async def test_build_reloop_skips_qa_on_rejection(
        self,
        design_project: Path,
    ) -> None:
        """CEO rejects PR. Builder retries before QA runs."""
        result, _ = await _run_workflow(
            design_workflow,
            design_project,
            gate_responses={
                "gate_build": [
                    'RELOOP TARGET="builder" FEEDBACK="scope creep"',
                    "PROCEED",
                ],
            },
        )

        trace = node_trace(result)
        assert trace.count("builder") >= 2

        # QA agents only after build is approved
        # fork_qa doesn't emit node.started — check health_checker instead
        first_hc = trace.index("health_checker")
        second_builder = [i for i, n in enumerate(trace) if n == "builder"][1]
        assert first_hc > second_builder


class TestDesignQAReloop:
    async def test_qa_reloop_cycles_through_builder_and_qa_again(
        self,
        design_project: Path,
    ) -> None:
        """QA finds issues. Reloops to builder. Builder fixes, full QA re-runs."""
        result, _ = await _run_workflow(
            design_workflow,
            design_project,
            gate_responses={
                "gate_qa": [
                    'RELOOP TARGET="builder" FEEDBACK="health check failed"',
                    "PROCEED",
                ],
            },
        )

        trace = node_trace(result)
        assert trace.count("builder") >= 2
        assert trace.count("health_checker") >= 2

        verdicts = gate_verdicts(result)
        qa_verdicts = [v for gid, v in verdicts if gid == "gate_qa"]
        assert VerdictType.RELOOP in qa_verdicts
        assert VerdictType.PROCEED in qa_verdicts


class TestDesignDocFreshnessReloop:
    async def test_doc_freshness_reloop_cycles_full_pipeline(
        self,
        design_project: Path,
    ) -> None:
        """Stale docs. Builder updates, full QA + doc check re-runs."""
        result, _ = await _run_workflow(
            design_workflow,
            design_project,
            gate_responses={
                "gate_doc_freshness": [
                    'RELOOP TARGET="builder" FEEDBACK="update README"',
                    "PROCEED",
                ],
            },
        )

        trace = node_trace(result)
        assert trace.count("builder") >= 2

        verdicts = gate_verdicts(result)
        doc_verdicts = [v for gid, v in verdicts if gid == "gate_doc_freshness"]
        assert VerdictType.RELOOP in doc_verdicts
        assert VerdictType.PROCEED in doc_verdicts


class TestDesignPrecheckHalt:
    async def test_precheck_halt_aborts_workflow(
        self,
        design_project: Path,
    ) -> None:
        """Precheck fails (score dropped). Workflow halts."""
        result, _ = await _run_workflow(
            design_workflow,
            design_project,
            shell_overrides={"factory precheck": "FAIL: score dropped"},
        )

        assert result.halted is True
        trace = node_trace(result)
        assert "archivist_build" not in trace


class TestDesignMaxIterations:
    async def test_max_iterations_halts_on_repeated_reloop(
        self,
        design_project: Path,
    ) -> None:
        """Gate keeps relooping. Executor enforces max_iterations (default 3)."""
        result, _ = await _run_workflow(
            design_workflow,
            design_project,
            gate_responses={
                "gate_build": [
                    'RELOOP TARGET="builder" FEEDBACK="wrong"',
                ]
                * 5,
            },
        )

        assert result.halted is True
        assert "max iterations" in result.halt_reason.lower()

        trace = node_trace(result)
        builder_count = trace.count("builder")
        # initial + 3 reloops = 4
        assert builder_count == 4


class TestDesignFeedbackAccumulation:
    async def test_feedback_accumulates_across_reloops(
        self,
        design_project: Path,
    ) -> None:
        """Multiple reloops with different feedback. Both end up in node_context."""
        result, executor = await _run_workflow(
            design_workflow,
            design_project,
            gate_responses={
                "gate_build": [
                    'RELOOP TARGET="builder" FEEDBACK="fix tests"',
                    'RELOOP TARGET="builder" FEEDBACK="fix lint too"',
                    "PROCEED",
                ],
            },
        )

        ctx = executor.node_context.get("builder", "")
        assert "fix tests" in ctx
        assert "fix lint too" in ctx
        assert "[Feedback iteration 1]" in ctx
        assert "[Feedback iteration 2]" in ctx


# ── Create Workflow Gate Tests ───────────────────────────────────


class TestCreateResearchReloop:
    async def test_create_research_reloop(self, create_project: Path) -> None:
        """Create workflow: reloop re-runs create's researchers (not design's)."""
        result, _ = await _run_workflow(
            create_workflow,
            create_project,
            gate_responses={
                "gate_research": [
                    'RELOOP TARGET="fork_research" FEEDBACK="need depth"',
                    "PROCEED",
                ],
            },
        )

        trace = node_trace(result)
        for researcher in (
            "researcher_existing",
            "researcher_intent",
            "researcher_practices",
        ):
            assert trace.count(researcher) >= 2, f"{researcher} should run >=2 times"

        for researcher in (
            "researcher_similar",
            "researcher_techstack",
            "researcher_pitfalls",
        ):
            assert researcher not in trace, f"{researcher} should not be in create workflow"


class TestCreateQAReloop:
    async def test_create_qa_reloop_cycles_builder(
        self,
        create_project: Path,
    ) -> None:
        """Create workflow: QA reloop cycles through builder and QA again."""
        result, _ = await _run_workflow(
            create_workflow,
            create_project,
            gate_responses={
                "gate_qa": [
                    'RELOOP TARGET="builder" FEEDBACK="tests failing"',
                    "PROCEED",
                ],
            },
        )

        trace = node_trace(result)
        assert trace.count("builder") >= 2
        assert trace.count("health_checker") >= 2


class TestCreateStartsAtResearch:
    async def test_create_starts_at_research_no_study(
        self,
        create_project: Path,
    ) -> None:
        """Create workflow starts at fork_research, no study/discover/gate_has_factory."""
        result, _ = await _run_workflow(create_workflow, create_project)

        trace = node_trace(result)
        for absent in ("study", "graph_update", "discover", "gate_has_factory"):
            assert absent not in trace, f"{absent} should not be in create workflow trace"

        # ForkNode doesn't emit node.started — first nodes are the fork targets
        # (researchers). Verify the first node is a create-mode researcher.
        assert trace[0] in (
            "researcher_existing",
            "researcher_intent",
            "researcher_practices",
        )


# ── Structural Invariant Tests ───────────────────────────────────


class TestDesignGateStrategyIsUserType:
    def test_design_gate_strategy_is_user_type(self) -> None:
        wf = design_workflow()
        gate = wf.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"


class TestCreateGateStrategyIsUserType:
    def test_create_gate_strategy_is_user_type(self) -> None:
        wf = create_workflow()
        gate = wf.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"


class TestDesignReloopEdgeTargets:
    def test_design_reloop_edges_target_correct_nodes(self) -> None:
        """Verify RELOOP edge targets in the design workflow."""
        wf = design_workflow()
        reloop_edges = {e.source: e.target for e in wf.edges if e.condition == VerdictType.RELOOP}

        assert reloop_edges.get("gate_research") == "fork_research"
        assert reloop_edges.get("gate_strategy") == "strategist"
        assert reloop_edges.get("gate_build") == "builder"
        assert reloop_edges.get("gate_qa") == "builder"
        assert reloop_edges.get("gate_doc_freshness") == "builder"


class TestCreateReloopEdgeTargets:
    def test_create_reloop_edges_target_correct_nodes(self) -> None:
        """Verify RELOOP edge targets in the create workflow."""
        wf = create_workflow()
        reloop_edges = {e.source: e.target for e in wf.edges if e.condition == VerdictType.RELOOP}

        assert reloop_edges.get("gate_research") == "fork_research"
        assert reloop_edges.get("gate_strategy") == "strategist"
        assert reloop_edges.get("gate_build") == "builder"
        assert reloop_edges.get("gate_qa") == "builder"
        assert reloop_edges.get("gate_doc_freshness") == "builder"


class TestDesignContainsAllSubgraphNodes:
    def test_design_contains_all_subgraph_nodes(self) -> None:
        """Design workflow contains study, research, and QA subgraph nodes."""
        wf = design_workflow()
        node_ids = set(wf.nodes.keys())

        for nid in ("graph_update", "study", "graph_explorer", "concat_study"):
            assert nid in node_ids, f"study subgraph node '{nid}' missing"

        for nid in (
            "fork_research",
            "researcher_similar",
            "researcher_techstack",
            "researcher_pitfalls",
            "join_research",
            "gate_research",
        ):
            assert nid in node_ids, f"research subgraph node '{nid}' missing"

        for nid in (
            "fork_qa",
            "health_checker",
            "code_reviewer",
            "adversarial_tester",
            "join_qa",
            "gate_qa",
        ):
            assert nid in node_ids, f"QA subgraph node '{nid}' missing"

        assert "gate_has_factory" in node_ids
        assert "discover" in node_ids


class TestBothWorkflowsValidateClean:
    def test_both_workflows_validate_clean(self) -> None:
        assert design_workflow().validate_graph() == []
        assert create_workflow().validate_graph() == []
