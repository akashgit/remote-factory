"""Tests for factory.workflow.verification — artifact verification engine."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    Workflow,
)
from factory.workflow.verification import (
    checks_to_bash,
    compile_agent_verification,
    compile_fork_verification,
    generate_hook_script,
    generate_verification_settings,
    write_verification_hooks,
)


# ── ArtifactCheck model ──────────────────────────────────────────


class TestArtifactCheck:
    def test_creation(self) -> None:
        check = ArtifactCheck(path=".factory/strategy/current.md")
        assert check.path == ".factory/strategy/current.md"
        assert check.must_exist is True
        assert check.min_size == 0
        assert check.must_contain == []

    def test_serialization(self) -> None:
        check = ArtifactCheck(
            path="output.md", must_exist=True, min_size=100,
            must_contain=["## Strategy"],
        )
        data = check.model_dump()
        assert data["path"] == "output.md"
        assert data["min_size"] == 100
        roundtrip = ArtifactCheck.model_validate(data)
        assert roundtrip == check

    def test_strict_validation_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            ArtifactCheck(path="x.md", unknown_field="bad")  # type: ignore[call-arg]


# ── AgentNode with post_checks ───────────────────────────────────


class TestAgentNodePostChecks:
    def test_default_empty(self) -> None:
        node = AgentNode(id="test", role=AgentRole.BUILDER)
        assert node.post_checks == []

    def test_explicit_list(self) -> None:
        checks = [ArtifactCheck(path="a.md"), ArtifactCheck(path="b.md", min_size=50)]
        node = AgentNode(id="test", role=AgentRole.BUILDER, post_checks=checks)
        assert len(node.post_checks) == 2
        assert node.post_checks[0].path == "a.md"

    def test_serialization_roundtrip(self) -> None:
        checks = [ArtifactCheck(path="out.md", must_contain=["## Done"])]
        node = AgentNode(id="test", role=AgentRole.BUILDER, post_checks=checks)
        data = node.model_dump(mode="json")
        restored = AgentNode.model_validate(data, strict=False)
        assert restored.post_checks == checks


# ── checks_to_bash ───────────────────────────────────────────────


class TestChecksToBash:
    def test_must_exist(self) -> None:
        checks = [ArtifactCheck(path="output.md")]
        result = checks_to_bash(checks, "builder")
        assert '[ ! -f "$_f" ]' in result
        assert "VERIFY FAIL" in result
        assert 'VERIFY OK: builder' in result

    def test_min_size(self) -> None:
        checks = [ArtifactCheck(path="output.md", min_size=100)]
        result = checks_to_bash(checks, "node1")
        assert "wc -c" in result
        assert "100" in result

    def test_must_contain(self) -> None:
        checks = [ArtifactCheck(path="x.md", must_contain=["## Strategy", "### Hypotheses"])]
        result = checks_to_bash(checks, "strat")
        assert "grep -qE" in result
        # Both sentinels should be in the pattern (pipe-delimited for AND)
        assert "Strategy" in result
        assert "Hypotheses" in result

    def test_vfail_tracking(self) -> None:
        checks = [ArtifactCheck(path="a.md")]
        result = checks_to_bash(checks, "test")
        assert "_vfail=0" in result
        assert "_vfail=1" in result
        assert 'exit 1' in result

    def test_verify_ok_on_success(self) -> None:
        checks = [ArtifactCheck(path="a.md")]
        result = checks_to_bash(checks, "mynode")
        assert 'VERIFY OK: mynode artifacts validated' in result


# ── compile_agent_verification ───────────────────────────────────


class TestCompileAgentVerification:
    def test_non_blocking_returns_none(self) -> None:
        node = AgentNode(
            id="arch", role=AgentRole.ARCHIVIST, blocking=False,
            writes={".factory/archive/plan.md"},
        )
        assert compile_agent_verification(node) is None

    def test_no_writes_no_checks_returns_none(self) -> None:
        node = AgentNode(id="empty", role=AgentRole.BUILDER)
        assert compile_agent_verification(node) is None

    def test_auto_generates_from_writes(self) -> None:
        node = AgentNode(
            id="builder", role=AgentRole.BUILDER,
            writes={".factory/reviews/builder-latest.md"},
        )
        result = compile_agent_verification(node)
        assert result is not None
        assert "builder-latest.md" in result
        assert "VERIFY OK" in result

    def test_uses_post_checks_when_provided(self) -> None:
        node = AgentNode(
            id="strat", role=AgentRole.STRATEGIST,
            writes={".factory/strategy/current.md"},
            post_checks=[
                ArtifactCheck(
                    path=".factory/strategy/current.md",
                    must_contain=["## Strategy"],
                    min_size=100,
                ),
            ],
        )
        result = compile_agent_verification(node)
        assert result is not None
        assert "## Strategy" in result
        assert "100" in result


# ── compile_fork_verification ────────────────────────────────────


class TestCompileForkVerification:
    def test_combines_multiple_nodes(self) -> None:
        nodes = [
            AgentNode(
                id="r1", role=AgentRole.RESEARCHER,
                writes={".factory/strategy/research-similar.md"},
            ),
            AgentNode(
                id="r2", role=AgentRole.RESEARCHER,
                writes={".factory/strategy/research-techstack.md"},
            ),
        ]
        result = compile_fork_verification(nodes)
        assert result is not None
        assert "r1" in result
        assert "r2" in result
        assert "research-similar.md" in result
        assert "research-techstack.md" in result

    def test_returns_none_when_no_writes(self) -> None:
        nodes = [
            AgentNode(id="r1", role=AgentRole.RESEARCHER),
        ]
        assert compile_fork_verification(nodes) is None


# ── generate_hook_script ─────────────────────────────────────────


class TestGenerateHookScript:
    def _make_workflow(self) -> Workflow:
        return Workflow(
            name="test",
            nodes={
                "builder": AgentNode(
                    id="builder", role=AgentRole.BUILDER,
                    writes={".factory/reviews/builder-latest.md"},
                ),
                "qa": AgentNode(
                    id="qa", role=AgentRole.QA,
                    writes={".factory/reviews/qa-latest.md"},
                ),
            },
            edges=[Edge(source="builder", target="qa")],
            start_node="builder",
        )

    def test_produces_valid_bash(self) -> None:
        script = generate_hook_script(self._make_workflow())
        assert script.startswith("#!/usr/bin/env bash")
        assert "factory agent builder" in script
        assert "factory agent qa" in script
        assert "if" in script
        assert "elif" in script
        assert "fi" in script
        assert "hook-log.txt" in script

    def test_logs_every_invocation(self) -> None:
        script = generate_hook_script(self._make_workflow())
        assert "HOOK_FIRED" in script
        assert 'HOOK_FIRED command=$_COMMAND' in script

    def test_logs_verify_ok(self) -> None:
        script = generate_hook_script(self._make_workflow())
        assert "VERIFY_OK node=builder" in script
        assert "VERIFY_OK node=qa" in script

    def test_logs_verify_fail(self) -> None:
        script = generate_hook_script(self._make_workflow())
        assert "VERIFY_FAIL node=builder" in script
        assert "VERIFY_FAIL node=qa" in script

    def test_reads_stdin_json(self) -> None:
        script = generate_hook_script(self._make_workflow())
        assert "_HOOK_INPUT=$(cat)" in script
        assert "jq" in script

    def test_empty_workflow_returns_empty(self) -> None:
        wf = Workflow(
            name="empty",
            nodes={
                "arch": AgentNode(
                    id="arch", role=AgentRole.ARCHIVIST, blocking=False,
                ),
            },
            edges=[],
            start_node="arch",
        )
        assert generate_hook_script(wf) == ""


# ── generate_verification_settings ───────────────────────────────


class TestGenerateVerificationSettings:
    def test_correct_structure(self) -> None:
        from pathlib import Path as P
        wf = Workflow(
            name="test", nodes={}, edges=[], start_node="x",
        )
        settings = generate_verification_settings(wf, P("/tmp/hook.sh"))
        assert "hooks" in settings
        assert "PostToolUse" in settings["hooks"]
        hook_entry = settings["hooks"]["PostToolUse"][0]
        assert hook_entry["matcher"] == "Bash"
        assert hook_entry["hooks"][0]["command"] == "/tmp/hook.sh"
        assert hook_entry["hooks"][0]["timeout"] == 30


# ── write_verification_hooks ─────────────────────────────────────


class TestWriteVerificationHooks:
    def test_creates_files(self, tmp_path: object) -> None:
        import pathlib
        target = pathlib.Path(str(tmp_path))
        wf = Workflow(
            name="build",
            nodes={
                "builder": AgentNode(
                    id="builder", role=AgentRole.BUILDER,
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
        )
        result = write_verification_hooks(wf, target)
        assert result is not None
        assert result.exists()

        # Check hook script exists and is executable
        script_path = target / ".factory" / "hooks" / "verify-build.sh"
        assert script_path.exists()
        assert script_path.stat().st_mode & stat.S_IXUSR

        # Check settings JSON is valid
        settings_data = json.loads(result.read_text())
        assert "hooks" in settings_data

    def test_returns_none_when_no_checks(self, tmp_path: object) -> None:
        import pathlib
        target = pathlib.Path(str(tmp_path))
        wf = Workflow(
            name="empty",
            nodes={
                "arch": AgentNode(
                    id="arch", role=AgentRole.ARCHIVIST, blocking=False,
                ),
            },
            edges=[],
            start_node="arch",
        )
        assert write_verification_hooks(wf, target) is None


# ── Layer 1: skill_export inline verification ────────────────────


class TestSkillExportVerification:
    def test_agent_blocking_with_post_checks_emits_verification(self) -> None:
        from factory.workflow.skill_export import _agent_to_instruction

        node = AgentNode(
            id="strategist", role=AgentRole.STRATEGIST,
            writes={".factory/strategy/current.md"},
            post_checks=[
                ArtifactCheck(
                    path=".factory/strategy/current.md",
                    must_contain=["## Strategy"],
                ),
            ],
        )
        wf = Workflow(
            name="test", nodes={"strategist": node}, edges=[], start_node="strategist",
        )
        result = _agent_to_instruction(node, wf)
        assert "VERIFY OK" in result
        assert "harness verification" in result
        assert "DO NOT SKIP" in result

    def test_agent_non_blocking_no_verification(self) -> None:
        from factory.workflow.skill_export import _agent_to_instruction

        node = AgentNode(
            id="arch", role=AgentRole.ARCHIVIST, blocking=False,
            writes={".factory/archive/plan.md"},
        )
        wf = Workflow(
            name="test", nodes={"arch": node}, edges=[], start_node="arch",
        )
        result = _agent_to_instruction(node, wf)
        assert "VERIFY OK" not in result
        assert "fire-and-forget" in result

    def test_agent_blocking_with_writes_auto_generates(self) -> None:
        from factory.workflow.skill_export import _agent_to_instruction

        node = AgentNode(
            id="builder", role=AgentRole.BUILDER,
            writes={".factory/reviews/builder-latest.md"},
        )
        wf = Workflow(
            name="test", nodes={"builder": node}, edges=[], start_node="builder",
        )
        result = _agent_to_instruction(node, wf)
        assert "VERIFY OK" in result
        assert "builder-latest.md" in result

    def test_fork_with_parallel_agents_emits_post_barrier(self) -> None:
        from factory.workflow.primitives import ForkNode
        from factory.workflow.skill_export import _fork_to_instruction

        r1 = AgentNode(
            id="r1", role=AgentRole.RESEARCHER,
            writes={".factory/strategy/research-similar.md"},
        )
        r2 = AgentNode(
            id="r2", role=AgentRole.RESEARCHER,
            writes={".factory/strategy/research-techstack.md"},
        )
        fork = ForkNode(id="fork_research", targets=["r1", "r2"])
        wf = Workflow(
            name="test",
            nodes={"fork_research": fork, "r1": r1, "r2": r2},
            edges=[
                Edge(source="fork_research", target="r1"),
                Edge(source="fork_research", target="r2"),
            ],
            start_node="fork_research",
        )
        result = _fork_to_instruction(fork, wf)
        assert "post-barrier harness verification" in result
        assert "VERIFY OK" in result

    def test_workflow_to_skill_md_contains_verification(self) -> None:
        from factory.workflow.skill_export import workflow_to_skill_md

        node = AgentNode(
            id="builder", role=AgentRole.BUILDER,
            writes={".factory/reviews/builder-latest.md"},
            post_checks=[ArtifactCheck(path=".factory/reviews/builder-latest.md")],
        )
        wf = Workflow(
            name="build",
            nodes={"builder": node},
            edges=[],
            start_node="builder",
        )
        result = workflow_to_skill_md(wf)
        assert "VERIFY OK" in result
        assert "VERIFY FAIL" in result


# ── Layer 2: ClaudeRunner settings_file ──────────────────────────


class TestClaudeRunnerSettingsFile:
    def test_build_command_includes_settings(self) -> None:
        from factory.models import AgentRunRequest
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        request = AgentRunRequest(
            prompt="test", task="do something", cwd=Path("/tmp"),
            extras={"settings_file": "/tmp/settings.json"},
        )
        cmd, _env, temp_files = runner.build_command(request)
        try:
            assert "--settings" in cmd
            idx = cmd.index("--settings")
            assert cmd[idx + 1] == "/tmp/settings.json"
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def test_build_command_omits_settings_when_absent(self) -> None:
        from factory.models import AgentRunRequest
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        request = AgentRunRequest(
            prompt="test", task="do something", cwd=Path("/tmp"),
        )
        cmd, _env, temp_files = runner.build_command(request)
        try:
            assert "--settings" not in cmd
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def test_build_interactive_command_includes_settings(self) -> None:
        from factory.models import AgentRunRequest
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        request = AgentRunRequest(
            prompt="test", task="do something", cwd=Path("/tmp"),
            extras={"settings_file": "/tmp/settings.json"},
        )
        cmd, _env, temp_files = runner.build_interactive_command(request)
        try:
            assert "--settings" in cmd
            idx = cmd.index("--settings")
            assert cmd[idx + 1] == "/tmp/settings.json"
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)


# ── Layer 2: invoke_agent settings_file ──────────────────────────


class TestInvokeAgentSettingsFile:
    def test_signature_accepts_settings_file(self) -> None:
        import inspect
        from factory.agents.runner import invoke_agent

        sig = inspect.signature(invoke_agent)
        assert "settings_file" in sig.parameters

    def test_ceo_completion_accepts_settings_file(self) -> None:
        import inspect
        from factory.ceo_completion import run_ceo_with_completion_guard

        sig = inspect.signature(run_ceo_with_completion_guard)
        assert "settings_file" in sig.parameters


# ── H4: Design workflow annotations ─────────────────────────────


class TestDesignWorkflowAnnotations:
    def test_build_workflow_has_post_checks(self) -> None:
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        # Researchers
        for nid in ("researcher_similar", "researcher_techstack", "researcher_pitfalls"):
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert len(node.post_checks) > 0, f"{nid} should have post_checks"

        # Strategist — sentinels match real output structure
        strat = wf.nodes["strategist"]
        assert isinstance(strat, AgentNode)
        assert len(strat.post_checks) > 0
        assert strat.post_checks[0].min_size == 200
        assert "### Phase 1" in strat.post_checks[0].must_contain
        assert "### Architecture" in strat.post_checks[0].must_contain

        # Builder — validates real agent output, not just auto-header
        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert len(builder.post_checks) > 0
        assert builder.post_checks[0].min_size == 500
        assert "commit" in builder.post_checks[0].must_contain

        # QA — validates section headers from QA pipeline
        qa = wf.nodes["qa"]
        assert isinstance(qa, AgentNode)
        assert len(qa.post_checks) > 0
        assert qa.post_checks[0].min_size == 500
        assert "## Health Check" in qa.post_checks[0].must_contain
        assert "## Code Review" in qa.post_checks[0].must_contain

    def test_design_workflow_inherits_post_checks(self) -> None:
        from factory.workflow.definitions import design_workflow

        wf = design_workflow()
        # Design inherits from build — verify inherited sentinel values
        strat = wf.nodes["strategist"]
        assert isinstance(strat, AgentNode)
        assert len(strat.post_checks) > 0
        assert "### Phase 1" in strat.post_checks[0].must_contain
        assert "### Architecture" in strat.post_checks[0].must_contain

        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert len(builder.post_checks) > 0
        assert "commit" in builder.post_checks[0].must_contain

        qa = wf.nodes["qa"]
        assert isinstance(qa, AgentNode)
        assert len(qa.post_checks) > 0
        assert "## Health Check" in qa.post_checks[0].must_contain
        assert "## Code Review" in qa.post_checks[0].must_contain

    def test_design_skill_md_contains_verification(self) -> None:
        from factory.workflow.definitions import design_workflow
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = design_workflow()
        result = workflow_to_skill_md(wf)
        assert "VERIFY OK" in result
        assert "harness verification" in result

    def test_design_hook_script_has_role_branches(self) -> None:
        from factory.workflow.definitions import design_workflow

        wf = design_workflow()
        script = generate_hook_script(wf)
        assert script  # non-empty
        assert "factory agent strategist" in script
        assert "factory agent qa" in script
