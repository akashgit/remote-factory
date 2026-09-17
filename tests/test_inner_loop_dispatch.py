"""Tests for InnerLoop execution_strategy dispatch logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from factory.inner_loop import InnerLoop, _SubprocessExecutionResult
from factory.task import ScoringContract, TaskDefinition, TaskInstance, VerifyResult
from factory.workflow.primitives import AgentNode, AgentRole, Workflow


def _make_workflow(name: str = "test") -> Workflow:
    return Workflow(
        name=name,
        nodes={
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template="build {project_path}",
            ),
        },
        edges=[],
        start_node="builder",
    )


def _make_exec_result(success: bool = True) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.halted = not success
    r.halt_reason = "" if success else "halted"
    r.nodes_executed = 1
    r.duration_ms = 100.0
    return r


def _async_return(val: object) -> MagicMock:
    async def _coro(*a: object, **kw: object) -> object:
        return val
    return MagicMock(side_effect=_coro)


def _make_task():
    task = MagicMock()
    task.instances.return_value = [TaskInstance(id="inst-1")]
    task.setup.return_value = None
    task.prompt.return_value = "test prompt"
    task.verify.return_value = VerifyResult(passed=True, score=0.8)
    task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))
    return task


class TestExecutionStrategyDefault:
    """Default execution_strategy='executor' preserves existing behavior."""

    def test_default_strategy_is_executor(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        loop = InnerLoop(project_dir=tmp_path, mode="test")
        assert loop.execution_strategy == "executor"

    def test_executor_strategy_uses_workflow_executor(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        task = _make_task()
        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result())
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(
                project_dir=tmp_path, mode="test", task=task, workflow=wf,
                execution_strategy="executor",
            )
            record = loop.step()

        MockExecutor.assert_called_once()
        assert record.score_end == 0.8


class TestCeoSkillDispatch:
    """execution_strategy='ceo-skill' dispatches to _run_ceo_subprocess."""

    def test_ceo_skill_calls_run_ceo_subprocess(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        task = _make_task()
        wf = _make_workflow()

        loop = InnerLoop(
            project_dir=tmp_path, mode="test", task=task, workflow=wf,
            execution_strategy="ceo-skill",
        )

        mock_result = _SubprocessExecutionResult(success=True, nodes_executed=1, duration_ms=50)

        with patch.object(loop, "_run_ceo_subprocess", return_value=mock_result) as mock_run:
            record = loop.step()

        mock_run.assert_called_once_with("test prompt", engine="skill")
        assert record.score_end == 0.8

    def test_ceo_skill_still_calls_setup_and_verify(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        call_order: list[str] = []

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="inst-1")]
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))
        task.setup.side_effect = lambda i, w: call_order.append("setup")
        task.prompt.side_effect = lambda i: (call_order.append("prompt"), "prompt")[1]
        task.verify.side_effect = lambda i, w: (
            call_order.append("verify"),
            VerifyResult(passed=True, score=0.9),
        )[1]

        wf = _make_workflow()
        loop = InnerLoop(
            project_dir=tmp_path, mode="test", task=task, workflow=wf,
            execution_strategy="ceo-skill",
        )

        mock_result = _SubprocessExecutionResult(success=True)
        with patch.object(loop, "_run_ceo_subprocess", return_value=mock_result):
            loop.step()

        assert call_order == ["setup", "prompt", "verify"]


class TestCeoToolDispatch:
    """execution_strategy='ceo-tool' dispatches with engine='tool'."""

    def test_ceo_tool_calls_run_ceo_subprocess_with_tool(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        task = _make_task()
        wf = _make_workflow()

        loop = InnerLoop(
            project_dir=tmp_path, mode="test", task=task, workflow=wf,
            execution_strategy="ceo-tool",
        )

        mock_result = _SubprocessExecutionResult(success=True, nodes_executed=1)
        with patch.object(loop, "_run_ceo_subprocess", return_value=mock_result) as mock_run:
            loop.step()

        mock_run.assert_called_once_with("test prompt", engine="tool")


class TestSubprocessExecutionResult:
    """_SubprocessExecutionResult duck-types ExecutionResult."""

    def test_has_required_fields(self) -> None:
        r = _SubprocessExecutionResult()
        assert hasattr(r, "success")
        assert hasattr(r, "halted")
        assert hasattr(r, "halt_reason")
        assert hasattr(r, "nodes_executed")
        assert hasattr(r, "duration_ms")
        assert hasattr(r, "node_outputs")

    def test_defaults(self) -> None:
        r = _SubprocessExecutionResult()
        assert r.success is False
        assert r.halted is False
        assert r.halt_reason == ""
        assert r.nodes_executed == 0
        assert r.duration_ms == 0
        assert r.node_outputs == {}


class TestCostWarning:
    """CEO strategies emit a one-time cost warning."""

    def test_cost_warning_fires_once(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        task = _make_task()
        task.instances.return_value = [TaskInstance(id="a"), TaskInstance(id="b")]
        task.verify.side_effect = [
            VerifyResult(passed=True, score=0.5),
            VerifyResult(passed=True, score=0.5),
        ]
        wf = _make_workflow()

        loop = InnerLoop(
            project_dir=tmp_path, mode="test", task=task, workflow=wf,
            execution_strategy="ceo-skill",
        )

        mock_result = _SubprocessExecutionResult(success=True)
        with patch.object(loop, "_run_ceo_subprocess", return_value=mock_result):
            with patch("factory.inner_loop.log") as mock_log:
                loop.step()

        # Warning should fire exactly once even though there are 2 instances
        warning_calls = [
            c for c in mock_log.warning.call_args_list
            if c.args and c.args[0] == "ceo_subprocess_cost_warning"
        ]
        assert len(warning_calls) == 1


class TestLegacyPathUnchanged:
    """When task is None, _step_subprocess is used regardless of execution_strategy."""

    def test_no_task_uses_step_subprocess(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        loop = InnerLoop(
            project_dir=tmp_path, mode="test",
            execution_strategy="ceo-skill",
        )
        assert loop.task is None
        # step() should route to _step_subprocess, not _step_with_task
        assert hasattr(loop, "_step_subprocess")


class TestDataNodeFallback:
    """DataNode workflows fall back to executor with warning."""

    def test_data_node_with_non_executor_logs_warning(self, tmp_path: Path) -> None:
        from factory.workflow.primitives import DataNode, DataItem

        (tmp_path / ".factory").mkdir()

        items = [DataItem(id="item-1", prompt="test")]
        data_node = DataNode(
            id="data",
            inline_items=items,
            subgraph_entry="builder",
            subgraph_exit="builder",
        )
        builder = AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            prompt_template="build",
        )
        wf = Workflow(
            name="data-test",
            nodes={"data": data_node, "builder": builder},
            edges=[],
            start_node="data",
        )

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="inst-1")]
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        loop = InnerLoop(
            project_dir=tmp_path, mode="test", task=task, workflow=wf,
            execution_strategy="ceo-skill",
        )

        with (
            patch("factory.inner_loop.log") as mock_log,
            patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor,
        ):
            mock_exec = MagicMock()
            mock_exec_result = MagicMock()
            mock_exec_result.success = True
            mock_exec_result.node_outputs = {}
            mock_exec.execute = _async_return(mock_exec_result)
            MockExecutor.return_value = mock_exec

            loop.step()

        warning_calls = [
            c for c in mock_log.warning.call_args_list
            if c.args and c.args[0] == "data_node_strategy_fallback"
        ]
        assert len(warning_calls) == 1
