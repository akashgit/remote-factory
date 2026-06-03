"""Tests for runner v2 types, protocol, and assemble_prompt()."""

from __future__ import annotations

from factory.agents.runner import assemble_prompt
from factory.runners.types import (
    AgentStep,
    ExecutionTrace,
    FileLocation,
    RunnerCapability,
    RunnerInfo,
    RunnerRequest,
    RunnerResponse,
    ToolCallStatus,
    ToolCallTrace,
    ToolKind,
    UsageStats,
)


# -- RunnerCapability enum ---------------------------------------------------

class TestRunnerCapability:
    def test_all_values(self):
        expected = {
            "model_override", "session_resume", "structured_output",
            "streaming", "interactive", "dry_run", "sandboxing",
            "acp", "execution_trace",
        }
        assert {c.value for c in RunnerCapability} == expected

    def test_member_access(self):
        assert RunnerCapability.MODEL_OVERRIDE.value == "model_override"
        assert RunnerCapability.ACP.value == "acp"
        assert RunnerCapability.EXECUTION_TRACE.value == "execution_trace"


# -- ToolKind enum ------------------------------------------------------------

class TestToolKind:
    def test_all_values(self):
        expected = {"read", "edit", "delete", "search", "execute", "think", "fetch", "other"}
        assert {k.value for k in ToolKind} == expected

    def test_acp_aligned_names(self):
        assert ToolKind.READ.value == "read"
        assert ToolKind.EDIT.value == "edit"
        assert ToolKind.EXECUTE.value == "execute"
        assert ToolKind.THINK.value == "think"


# -- ToolCallStatus enum -----------------------------------------------------

class TestToolCallStatus:
    def test_all_values(self):
        expected = {"pending", "in_progress", "completed", "failed"}
        assert {s.value for s in ToolCallStatus} == expected


# -- RunnerInfo ---------------------------------------------------------------

class TestRunnerInfo:
    def test_minimal(self):
        info = RunnerInfo(name="test", display_name="Test Runner")
        assert info.name == "test"
        assert info.display_name == "Test Runner"
        assert info.version is None
        assert info.capabilities == set()

    def test_with_capabilities(self):
        caps = {RunnerCapability.MODEL_OVERRIDE, RunnerCapability.STREAMING}
        info = RunnerInfo(name="claude", display_name="Claude Code", version="1.2.3", capabilities=caps)
        assert info.version == "1.2.3"
        assert RunnerCapability.MODEL_OVERRIDE in info.capabilities
        assert RunnerCapability.STREAMING in info.capabilities

    def test_capabilities_set_operations(self):
        caps_a = {RunnerCapability.MODEL_OVERRIDE, RunnerCapability.STREAMING}
        caps_b = {RunnerCapability.STREAMING, RunnerCapability.ACP}
        assert caps_a & caps_b == {RunnerCapability.STREAMING}
        assert caps_a | caps_b == {RunnerCapability.MODEL_OVERRIDE, RunnerCapability.STREAMING, RunnerCapability.ACP}
        assert caps_a - caps_b == {RunnerCapability.MODEL_OVERRIDE}


# -- RunnerRequest ------------------------------------------------------------

class TestRunnerRequest:
    def test_defaults(self):
        req = RunnerRequest(system_prompt="You are a builder.", task="Fix the bug", cwd="/tmp")
        assert req.system_prompt == "You are a builder."
        assert req.task == "Fix the bug"
        assert req.cwd == "/tmp"
        assert req.timeout == 300
        assert req.model is None
        assert req.session_name is None
        assert req.role is None
        assert req.skip_permissions is True
        assert req.env_overrides == {}

    def test_prompt_property_combines_system_and_task(self):
        req = RunnerRequest(system_prompt="System.", task="Do stuff", cwd="/tmp")
        assert "System." in req.prompt
        assert "Do stuff" in req.prompt
        assert "Current Task" in req.prompt

    def test_full(self):
        req = RunnerRequest(
            system_prompt="system prompt",
            task="the task",
            cwd="/project",
            timeout=600,
            model="opus",
            session_name="factory: proj/builder",
            role="builder",
            skip_permissions=False,
            env_overrides={"FOO": "bar"},
        )
        assert req.timeout == 600
        assert req.model == "opus"
        assert req.env_overrides == {"FOO": "bar"}


# -- UsageStats ---------------------------------------------------------------

class TestUsageStats:
    def test_defaults(self):
        usage = UsageStats()
        assert usage.input_tokens is None
        assert usage.output_tokens is None
        assert usage.total_tokens is None
        assert usage.cost_usd is None
        assert usage.duration_seconds is None
        assert usage.model_used is None

    def test_with_values(self):
        usage = UsageStats(
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            cost_usd=0.05,
            duration_seconds=12.3,
            model_used="claude-sonnet-4-6",
        )
        assert usage.input_tokens == 1000
        assert usage.cost_usd == 0.05
        assert usage.model_used == "claude-sonnet-4-6"


# -- FileLocation ------------------------------------------------------------

class TestFileLocation:
    def test_path_only(self):
        loc = FileLocation(path="src/main.py")
        assert loc.path == "src/main.py"
        assert loc.line is None

    def test_with_line(self):
        loc = FileLocation(path="src/main.py", line=42)
        assert loc.line == 42


# -- ToolCallTrace ------------------------------------------------------------

class TestToolCallTrace:
    def test_minimal(self):
        trace = ToolCallTrace(tool_name="Read", kind=ToolKind.READ)
        assert trace.tool_name == "Read"
        assert trace.kind == ToolKind.READ
        assert trace.status == ToolCallStatus.COMPLETED
        assert trace.input_summary is None
        assert trace.output_summary is None
        assert trace.locations == []
        assert trace.duration_ms is None
        assert trace.error is None

    def test_failed_with_error(self):
        trace = ToolCallTrace(
            tool_name="Bash",
            kind=ToolKind.EXECUTE,
            status=ToolCallStatus.FAILED,
            input_summary="pytest -x",
            error="Exit code 1",
            duration_ms=5000,
        )
        assert trace.status == ToolCallStatus.FAILED
        assert trace.error == "Exit code 1"

    def test_with_locations(self):
        trace = ToolCallTrace(
            tool_name="Edit",
            kind=ToolKind.EDIT,
            locations=[FileLocation("a.py", 10), FileLocation("b.py")],
        )
        assert len(trace.locations) == 2
        assert trace.locations[0].line == 10


# -- AgentStep ----------------------------------------------------------------

class TestAgentStep:
    def test_defaults(self):
        step = AgentStep(step_index=0)
        assert step.step_index == 0
        assert step.tool_calls == []
        assert step.reasoning is None
        assert step.output_text is None
        assert step.usage is None

    def test_with_tool_calls(self):
        step = AgentStep(
            step_index=1,
            tool_calls=[
                ToolCallTrace(tool_name="Read", kind=ToolKind.READ),
                ToolCallTrace(tool_name="Edit", kind=ToolKind.EDIT),
            ],
            reasoning="Need to fix the import",
            usage=UsageStats(input_tokens=500, output_tokens=200),
        )
        assert len(step.tool_calls) == 2
        assert step.usage is not None
        assert step.usage.input_tokens == 500


# -- ExecutionTrace -----------------------------------------------------------

class TestExecutionTrace:
    def test_defaults(self):
        trace = ExecutionTrace()
        assert trace.steps == []
        assert trace.files_read == []
        assert trace.files_written == []
        assert trace.commands_executed == []
        assert trace.thinking_blocks == []
        assert trace.sub_agent_traces == []

    def test_with_data(self):
        trace = ExecutionTrace(
            steps=[AgentStep(step_index=0)],
            files_read=["a.py", "b.py"],
            files_written=["c.py"],
            commands_executed=["pytest"],
            thinking_blocks=["Let me think..."],
        )
        assert len(trace.steps) == 1
        assert len(trace.files_read) == 2

    def test_nested_sub_agent_traces(self):
        inner = ExecutionTrace(
            files_read=["inner.py"],
            steps=[AgentStep(step_index=0, tool_calls=[
                ToolCallTrace(tool_name="Read", kind=ToolKind.READ),
            ])],
        )
        outer = ExecutionTrace(
            steps=[AgentStep(step_index=0)],
            sub_agent_traces=[inner],
        )
        assert len(outer.sub_agent_traces) == 1
        assert outer.sub_agent_traces[0].files_read == ["inner.py"]
        assert len(outer.sub_agent_traces[0].steps[0].tool_calls) == 1

    def test_deeply_nested_traces(self):
        level3 = ExecutionTrace(files_read=["deep.py"])
        level2 = ExecutionTrace(sub_agent_traces=[level3])
        level1 = ExecutionTrace(sub_agent_traces=[level2])
        assert level1.sub_agent_traces[0].sub_agent_traces[0].files_read == ["deep.py"]


# -- RunnerResponse -----------------------------------------------------------

class TestRunnerResponse:
    def test_minimal(self):
        resp = RunnerResponse(output="Done.", exit_code=0)
        assert resp.output == "Done."
        assert resp.exit_code == 0
        assert resp.usage is None
        assert resp.trace is None
        assert resp.session_id is None
        assert resp.metadata == {}

    def test_full(self):
        resp = RunnerResponse(
            output="Fixed the bug.",
            exit_code=0,
            usage=UsageStats(input_tokens=1000, cost_usd=0.03),
            trace=ExecutionTrace(files_written=["fix.py"]),
            session_id="abc-123",
            metadata={"model": "claude-opus-4-6"},
        )
        assert resp.usage is not None
        assert resp.usage.cost_usd == 0.03
        assert resp.trace is not None
        assert resp.trace.files_written == ["fix.py"]
        assert resp.session_id == "abc-123"
        assert resp.metadata["model"] == "claude-opus-4-6"

    def test_nonzero_exit_code(self):
        resp = RunnerResponse(output="Error: timeout", exit_code=1)
        assert resp.exit_code == 1


# -- assemble_prompt() --------------------------------------------------------

class TestAssemblePrompt:
    def test_minimal(self):
        result = assemble_prompt("You are a builder.", None, None, "Fix the bug")
        assert result == "You are a builder.\n\n---\n\n## Current Task\n\nFix the bug"

    def test_with_playbook(self):
        result = assemble_prompt("System.", "Do X\nDon't Y", None, "Task")
        assert "---\n\nBehavioral Playbook (auto-evolved)\n\nDo X\nDon't Y" in result
        assert result.startswith("System.")
        assert result.endswith("---\n\n## Current Task\n\nTask")

    def test_with_profile(self):
        result = assemble_prompt("System.", None, "User prefers Python", "Task")
        assert "---\n\nUser Profile\n\nUser prefers Python" in result
        assert "Behavioral Playbook" not in result

    def test_full(self):
        result = assemble_prompt("System.", "Playbook text", "Profile text", "Task text")
        parts = result.split("\n\n---\n\n")
        assert len(parts) == 4
        assert parts[0] == "System."
        assert parts[1] == "Behavioral Playbook (auto-evolved)\n\nPlaybook text"
        assert parts[2] == "User Profile\n\nProfile text"
        assert parts[3] == "## Current Task\n\nTask text"

    def test_ordering(self):
        result = assemble_prompt("S", "P", "U", "T")
        pb_idx = result.index("Behavioral Playbook")
        up_idx = result.index("User Profile")
        ct_idx = result.index("Current Task")
        assert pb_idx < up_idx < ct_idx
